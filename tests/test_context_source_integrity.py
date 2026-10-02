"""Full contract coverage, bounded failure and ordinary child pinning."""
import copy
import json
import threading
from types import SimpleNamespace

from routing.service import GenerationRouter
from routing.v7.retrieval import ContextIndex, build
from routing.v7.state import Session


def msg(role, text, **kw):
    return dict(role=role, content=text, **kw)


def test_selected_long_contract_remains_complete_after_detours():
    contract = 'Outline now and implement after Go.\n' + ('Constraint A and B must both hold.\n' * 50) + 'Rollback every partial write.'
    messages = [msg('user', contract), msg('assistant', 'Pending.')]
    for i in range(16):
        messages += [msg('user', f'Unrelated conversion {i}'), msg('assistant', str(i))]
    messages.append(msg('user', 'Go'))
    before = copy.deepcopy(messages)
    view, _ = build(messages, None, ContextIndex())
    source = next(x for x in view['recent'] + view['earlier_index'] if x['id'] == 'm0')
    assert source['text'] == contract and not source['content_truncated']
    assert view['required_source_coverage']['status'] == 'complete'
    assert messages == before


def test_oversize_pending_tool_group_is_unavailable_without_paid_classifier():
    messages = [msg('user', 'Inspect the report and defer the repair.'),
                msg('assistant', '', tool_calls=[{'id': 'r', 'name': 'read', 'arguments': {}}]),
                msg('tool', 'report\n' * 7000, tool_call_id='r'),
                msg('assistant', 'Repair pending.'), msg('user', 'Go')]
    view, _ = build(messages, None, ContextIndex(), force_sources=['m0', 'm3'])
    assert len(json.dumps(view, ensure_ascii=False).encode()) <= 24000
    assert view['required_source_coverage']['status'] == 'unavailable'
    assert set(view['required_source_coverage']['omitted_ids']) == {'m0', 'm1', 'm2', 'm3'}
    router = GenerationRouter(SimpleNamespace(complete=lambda *a, **k: (_ for _ in ()).throw(AssertionError('paid call'))))
    descriptor, info = router.classifier.classify(view)
    assert not info['schema_valid'] and info['called'] is False


def test_large_single_recent_contract_fails_closed_inside_budget():
    view, _ = build([msg('user', 'x' * 30000), msg('assistant', 'Ready'), msg('user', 'Continue')], None, ContextIndex())
    assert len(json.dumps(view).encode()) <= 24000
    assert view['required_source_coverage']['status'] == 'unavailable'


def test_forced_sources_include_exact_amended_contract_and_tool_response():
    text = 'constraint\n' * 220
    messages = [msg('user', text), msg('assistant', '', tool_calls=[{'id': 'a'}]),
                msg('tool', 'observed\n' * 240, tool_call_id='a'), msg('assistant', 'Pending'),
                msg('user', 'Add atomic rollback.'), msg('assistant', 'Amended'), msg('user', 'Go')]
    view, _ = build(messages, None, ContextIndex(), force_sources=['m0', 'm3', 'm4', 'm5'])
    rows = {x['id']: x for x in view['earlier_index']}
    assert view['required_source_coverage']['status'] == 'complete'
    for i in range(6):
        assert rows[f'm{i}']['text'] == messages[i]['content']
        assert not rows[f'm{i}']['content_truncated']


def test_classifier_keeps_strict_invalid_response_and_uses_versioned_allowance():
    calls = []
    def complete(model, payload, **kw):
        calls.append(kw)
        return {'message': {'content': '{}'}, 'finish_reason': 'length'}
    decision = GenerationRouter(SimpleNamespace(complete=complete)).resolve([msg('user', 'Design a queue.')], mode='llm')
    assert calls == [{'effort': None, 'max_tokens': 1800}]
    assert not decision['classifier']['schema_valid']


def test_ordinary_child_reuses_root_scope_without_new_classifier():
    calls = []
    def complete(model, payload, **kw):
        calls.append(payload)
        return {'message': {'content': json.dumps(dict(operation='analysis', demand='standard',
            relation='independent', reference_ids=[], needs_context=False, reason='Analyze the stated report.',
            task_family='evidence_synthesis', input_status='provided', retrieval_source_ids=[],
            task_continuity={'action':'independent','task_id':None,'summary':''}))}, 'finish_reason':'stop'}
    router = GenerationRouter(SimpleNamespace(complete=complete))
    scope = SimpleNamespace(id='scope', lock=threading.RLock(), decision=None, classifications=0)
    root = router.resolve([msg('user', 'Analyze these report facts: A=1, B=2.')], mode='llm', scope=scope)
    child = router.resolve([msg('user', 'Summarize one subsection.')], mode='llm', scope=scope)
    assert len(calls) == 1 and scope.classifications == 1
    assert child['selection']['variant'] == root['selection']['variant']
    assert child['classifier_reused']
