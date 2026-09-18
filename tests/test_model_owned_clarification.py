"""Product uncertainty selects a normal model; it never authors a chat reply."""
import copy
import json

import pytest

from routing.service import (GenerationRouter, RoutingUnavailable, compiled_router,
                             decode_pin, resolve, restore_state)
from routing.v7.candidate import CalibratedRouter
from routing.v7.state import Session
from test_auto_routing_service import fixture, request
from test_classifier_availability_contract import value


@pytest.mark.parametrize('status,relation,prompt,reason', [
    ('missing', 'independent', 'What is the weather today in San Francisco?', 'INPUT_NOT_RETRIEVABLE_OR_AMBIGUOUS'),
    ('missing', 'independent', 'Summarize the missing document.', 'INPUT_NOT_RETRIEVABLE_OR_AMBIGUOUS'),
    ('ambiguous', 'ambiguous', 'Paraphrase the thing I meant.', 'CLASSIFIER_REQUIRES_SOURCE_CLARIFICATION'),
])
def test_uncertainty_keeps_original_request_and_normal_signed_binding(status, relation, prompt, reason):
    args = fixture()
    req = request(prompt)
    req['tools'] = [{'type': 'function', 'function': {'name': 'existing_tool',
                    'parameters': {'type': 'object', 'properties': {}}}}]
    before = copy.deepcopy(req)
    row = value(input_status=status, needs_context=True, relation=relation)
    calls = []
    def complete(*args, **kwargs):
        calls.append((args, kwargs))
        return {'message': {'content': json.dumps(row)}, 'finish_reason': 'stop'}
    result = resolve(req, complete=complete, **args)
    assert len(calls) == 1 and req == before
    assert result['action'] == 'generate' and 'text' not in result
    assert result['trace']['descriptor']['needs_context'] is True
    assert result['trace']['descriptor']['input_status'] == status
    assert result['trace']['uncertainty'] == {'handling': 'generation_model', 'reason': reason}
    assert result['trace']['selection']['reason'] == 'UNCERTAIN_TASK_BASELINE'
    assert result['config']['model_name'] == 'gpt-5.4'
    pin = decode_pin(result['pin'], args['signing_key'], project_id=7, user_id=2,
                     settings=args['settings'], now=101)
    assert pin['config'] == result['config']
    state = restore_state(result['state_token'], args['signing_key'], project_id=7, user_id=2,
        scope_id=req['scope_id'], gate_revision='g1', policy_revision=compiled_router().revision)
    assert state['decision']['action'] == 'generate'
    assert state['decision']['descriptor']['needs_context'] is True
    # Sync, async and streaming SDK paths all consume this same generate binding.
    # Same-run tool progress keeps it without another classifier request.
    req['prior_pin'] = result['pin']
    req['messages'].append({'role': 'tool', 'content': 'No live source available.', 'tool_call_id': 't1'})
    renewal = resolve(req, complete=lambda *a, **k: pytest.fail('Run reclassified'), **args)
    assert renewal['action'] == 'generate' and renewal['config'] == result['config']


def test_supplied_source_keeps_ordinary_eligibility_without_uncertainty_prompt():
    req = request('Reformat the supplied values A=1 B=2 as a table.')
    row = value(operation='transform', task_family='transformation')
    result = resolve(req, complete=lambda *a, **k: {'message': {'content': json.dumps(row)},
                     'finish_reason': 'stop'}, **fixture())
    assert result['action'] == 'generate'
    assert result['trace']['descriptor']['needs_context'] is False
    assert 'uncertainty' not in result['trace'] and 'text' not in result


def test_reference_precheck_generates_without_a_classifier_or_synthetic_response():
    req = request('Go back to it')
    req['messages'] = [
        {'role': 'user', 'content': 'Design the worker architecture.'},
        {'role': 'assistant', 'content': 'Worker design.'},
        {'role': 'user', 'content': 'Design the database architecture.'},
        {'role': 'assistant', 'content': 'Database design.'},
        *req['messages']]
    result = resolve(req, complete=lambda *a, **k: pytest.fail('Precheck classified'), **fixture())
    assert result['action'] == 'generate'
    assert result['trace']['uncertainty']['reason'] == 'RESOLVE_SOURCE_BEFORE_GENERATION'
    assert result['trace']['descriptor']['needs_context'] is True
    assert result['trace']['classifier']['called'] is False
    assert 'text' not in result


def test_multiple_pending_go_does_not_claim_an_intent_or_generate_router_text():
    class Gateway:
        def complete(self, *args, **kwargs):
            pytest.fail('Pending ambiguity classified')
    router = GenerationRouter(Gateway())
    session = Session()
    for name in ('first', 'second'):
        session.register_pending({'id': name, 'status': 'awaiting_user'})
    decision = router.resolve([{'role': 'user', 'content': 'Go'}], session=session)
    assert decision['action'] == 'generate'
    assert decision['uncertainty']['reason'] == 'NO_UNIQUE_PENDING_TASK'
    assert decision['descriptor']['needs_context'] is True
    assert 'clarification' not in decision
    assert all(i['status'] == 'awaiting_user' for i in session.intents.values())
    assert decision['selection']['model'] == 'gpt-5.4'


def test_uncertainty_cannot_escape_explicit_effort_or_available_pool():
    req = request('Summarize the absent document.')
    req['selection']['reasoning'] = {'mode': 'explicit', 'preset': 'high'}
    row = value(input_status='missing', needs_context=True)
    complete = lambda *a, **k: {'message': {'content': json.dumps(row)}, 'finish_reason': 'stop'}
    result = resolve(req, complete=complete, **fixture())
    assert result['config']['reasoning_effort'] == 'high'
    args = fixture()
    args['models'] = [m for m in args['models'] if m['name'] == 'global.openai.gpt-5.6-luna']
    with pytest.raises((RoutingUnavailable, ValueError)):
        resolve(request('Summarize the absent document.'), complete=complete, **args)


def test_product_revision_changes_without_mutating_ancestor_behavior():
    ancestor = CalibratedRouter(object())
    router = GenerationRouter(object())
    assert router.revision == ancestor.revision + '-model-owned-clarification-1'
