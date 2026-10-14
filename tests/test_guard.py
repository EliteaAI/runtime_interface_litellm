"""#6722 section 13.4: the answer guard decides inside the turn and escalates across the model ladder."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import CLASSIFIER, args_for, classifier, request
from routing import guard
from routing.service import RoutingUnavailable, compiled_router, decode_pin, resolve, restore_state

ALL = ('luna', 'terra', 'sol', 'sonnet55', 'opus55', 'gpt54')
SENTINEL = 'SENTINEL-do-not-log-8842 zero trust worker'
PROJECTION = {'latest': {'text': 'Total the invoices and list five risks.'}}
PASS = {'material': False, 'clause': 'none', 'cited_defect': ''}
FAIL = {'material': True, 'clause': 'iii', 'cited_defect': 'Only four risks are listed; five were requested.'}


@pytest.fixture(autouse=True)
def fresh_guard_state():
    guard._USED.clear()
    yield
    guard._USED.clear()


class Judge:
    """A stub classifier deployment that records every call."""

    def __init__(self, reply=PASS, finish='stop', error=None):
        self.reply, self.finish, self.error, self.calls = reply, finish, error, []

    def __call__(self, model, messages, effort=None, max_tokens=None):
        self.calls.append({'model': model, 'messages': messages, 'effort': effort, 'max_tokens': max_tokens})
        if self.error:
            raise self.error
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return {'message': {'content': text}, 'finish_reason': self.finish}


def turn(keys=ALL, demand='deep', output_cap=None, **args):
    args = args_for(*keys, **args)
    req = request('Total the invoices and list five risks.')
    if output_cap:
        req['output_cap'] = output_cap
    return args, resolve(req, complete=classifier(demand=demand), **args)


def ask(args, one, judge=None, answer='Total: 120. Risks: a, b, c, d.', **fields):
    req = {'pin': one['pin'], 'scope_id': '1' * 64, 'invocation_id': 'a' * 64, 'state_token': one['state_token'],
           'request_text_projection': PROJECTION, 'answer_text': answer, **fields}
    return guard.guard(req, project_id=7, user_id=2, settings=args['settings'], models=args['models'],
                       price_snapshot=args['price_snapshot'], signing_key=args['signing_key'],
                       complete=judge or Judge(), now=args['now'])


# --- hard signals short-circuit ----------------------------------------------------------------------------------
@pytest.mark.parametrize('last,code', [
    ({'refusal': True}, 'refusal'), ({'finish_reason': 'length'}, 'incomplete'),
    ({'tool_failure': True}, 'tool_failure'), ({'inability': True}, 'inability')])
def test_hard_signals_fail_without_a_judge_call(last, code):
    args, one = turn()
    judge = Judge()
    result = ask(args, one, judge, last_response=last)
    assert (result['verdict'], result['code'], judge.calls) == ('fail', code, [])
    assert result['escalate_to']['head'] == 'sol-high'
    assert result['trace']['escalation']['guard']['guard_error'] is None


def test_the_strongest_hard_signal_names_the_code():
    args, one = turn()
    result = ask(args, one, Judge(), last_response={'refusal': True, 'inability': True, 'finish_reason': 'length'})
    assert result['code'] == 'refusal'


# --- the quality guard --------------------------------------------------------------------------------------------
def test_a_not_material_answer_passes_after_exactly_one_judge_call():
    args, one = turn()
    judge = Judge(PASS)
    result = ask(args, one, judge)
    assert (result['verdict'], result['escalate_to']) == ('pass', None) and len(judge.calls) == 1
    call = judge.calls[0]
    assert (call['model'], call['effort'], call['max_tokens']) == (CLASSIFIER, None, guard.JUDGE_MAX_TOKENS)
    assert call['messages'][0] == {'role': 'system', 'content': guard.GUARD_RUBRIC}
    body = json.loads(call['messages'][1]['content'])
    assert body == {'request': PROJECTION, 'answer': 'Total: 120. Risks: a, b, c, d.', 'answer_truncated': False}


def test_a_material_answer_fails_with_the_clause_and_the_cited_defect():
    args, one = turn()
    result = ask(args, one, Judge(FAIL))
    assert (result['verdict'], result['code'], result['cited_defect']) == ('fail', 'quality_material', FAIL['cited_defect'])
    guard_trace = result['trace']['escalation']['guard']
    assert (guard_trace['verdict'], guard_trace['clause'], guard_trace['code']) == ('fail', 'iii', 'quality_material')
    assert result['trace']['escalation']['event'] == 'guard'


def test_the_judge_reply_is_read_with_the_first_object_parser():
    args, one = turn()
    reply = '```json\n' + json.dumps(FAIL) + '\n```\nThe answer omits one risk.'
    assert ask(args, one, Judge(reply))['verdict'] == 'fail'


@pytest.mark.parametrize('reply,finish', [
    ('not json at all', 'stop'), ('{"material": true}', 'stop'), ('{"material": "yes", "clause": "i", "cited_defect": "x"}', 'stop'),
    ({'material': True, 'clause': 'none', 'cited_defect': 'x'}, 'stop'),   # material needs a clause
    ({'material': True, 'clause': 'iii', 'cited_defect': '  '}, 'stop'),   # and a cited defect
    ({'material': False, 'clause': 'ii', 'cited_defect': ''}, 'stop'),     # and not-material means no clause
    ({'material': True, 'clause': 'iv', 'cited_defect': 'x'}, 'stop'), (FAIL, 'length')])
def test_an_invalid_judge_reply_passes_the_answer_with_a_guard_error(reply, finish):
    args, one = turn()
    result = ask(args, one, Judge(reply, finish))
    assert (result['verdict'], result['code'], result['escalate_to']) == ('pass', 'GUARD_SCHEMA_INVALID', None)
    assert result['trace']['escalation']['guard']['guard_error'] == 'GUARD_SCHEMA_INVALID'


def test_a_failing_judge_call_passes_the_answer():
    args, one = turn()
    result = ask(args, one, Judge(error=RuntimeError('provider down')))
    assert (result['verdict'], result['code']) == ('pass', 'GUARD_JUDGE_ERROR')


def test_an_unpriced_judge_deployment_passes_with_a_code_and_makes_no_call():
    args, one = turn()
    args['price_snapshot'] = {**args['price_snapshot'],
                              'entries': [e for e in args['price_snapshot']['entries'] if e['model_name'] != CLASSIFIER]}
    judge = Judge(FAIL)
    result = ask(args, one, judge)
    assert (result['verdict'], result['code'], judge.calls) == ('pass', 'GUARD_PRICE_UNAVAILABLE', [])
    assert result['trace']['escalation']['guard']['guard_error'] == 'GUARD_PRICE_UNAVAILABLE'


def test_a_missing_classifier_passes_with_a_code():
    args, one = turn()
    args['settings'] = {**args['settings'], 'classifier': None}
    judge = Judge(FAIL)
    result = ask(args, one, judge)
    assert (result['verdict'], result['code'], judge.calls) == ('pass', 'GUARD_CLASSIFIER_UNAVAILABLE', [])


def test_the_answer_is_capped_for_the_judge_and_hashed_whole():
    args, one = turn()
    answer = 'x' * (guard.MAX_ANSWER_CHARS + 500)
    judge = Judge(PASS)
    result = ask(args, one, judge, answer=answer, answer_id='m7')
    body = json.loads(judge.calls[0]['messages'][1]['content'])
    assert len(body['answer']) == guard.MAX_ANSWER_CHARS and body['answer_truncated'] is True
    trace = result['trace']['escalation']['guard']
    assert trace['first_answer_sha256'] == guard.sha(answer) and trace['first_answer_id'] == 'm7'


def test_the_rubric_is_frozen_and_carries_the_judge_panel_standard():
    import hashlib
    assert guard.RUBRIC_SHA256 == hashlib.sha256(guard.GUARD_RUBRIC.encode()).hexdigest()
    for part in ('(i)', '(ii)', '(iii)', '+-10%', 'self-contradiction', 'untrusted data', '"clause"'):
        assert part in guard.GUARD_RUBRIC
    args, one = turn()
    assert ask(args, one, Judge())['trace']['escalation']['guard']['rubric_sha256'] == guard.RUBRIC_SHA256


# --- the escalation target and what the SDK gets -----------------------------------------------------------------
def test_escalate_to_moves_up_the_cross_model_ladder_at_deep():
    args, one = turn()
    target = ask(args, one, Judge(FAIL))['escalate_to']
    assert (target['head'], target['tier']) == ('sol-high', 'eligible')
    assert target['config']['model_name'] == 'global.openai.gpt-5.6-sol' and target['config']['reasoning_effort'] == 'high'


def test_escalate_to_never_picks_another_effort_of_the_same_model():
    args, one = turn(demand='simple')
    assert one['trace']['selection']['variant'] == 'luna-default'
    target = ask(args, one, Judge(FAIL))['escalate_to']
    assert target['head'] == 'terra-default' and target['config']['model_name'] == 'global.openai.gpt-5.6-terra'


def test_the_escalated_pin_binds_the_next_head_for_the_same_invocation():
    args, one = turn()
    target = ask(args, one, Judge(FAIL))['escalate_to']
    pin = decode_pin(target['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'],
                     now=args['now'], scope_id='1' * 64, invocation_id='a' * 64)
    assert pin['config'] == target['config'] and pin['guarded'] is True and 'escalation_plan' not in pin
    assert pin['model_binding']['name'] == 'global.openai.gpt-5.6-sol' and pin['model_binding'] != one and pin['invocation_id'] == 'a' * 64


def test_the_escalated_pin_never_enlarges_the_output_allowance():
    args, one = turn(output_cap=30000)
    target = ask(args, one, Judge(FAIL))['escalate_to']
    assert target['config']['max_tokens'] == 30000 and target['config']['routing_total_output_cap'] is True
    assert target['config'].get('routing_min_output_cap', 30000) == 30000


def test_the_escalated_state_records_the_new_head_and_the_sticky_level():
    args, one = turn()
    target = ask(args, one, Judge(FAIL))['escalate_to']
    state = restore_state(target['state_token'], args['signing_key'], project_id=7, user_id=2, scope_id='1' * 64,
                          gate_revision='g1', policy_revision=compiled_router().revision)
    assert state['decision']['selection']['variant'] == 'sol-high'
    assert state['escalation'] == {'event': 'guard', 'from': 'terra-default', 'level': 1, 'to': 'sol-high'}
    nxt = request('Thanks. Now the same for March.', invocation_id='b' * 64)
    nxt['messages'] = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}, nxt['messages'][0]]
    nxt['state_token'] = target['state_token']
    again = resolve(nxt, complete=classifier(demand='deep', relation='followup', reference_ids=['m0']), **args)
    assert again['trace']['selection']['variant'] == 'sol-high' and again['trace']['escalation']['reason'] == 'STICKY'


def test_without_a_state_token_the_target_still_comes_back_with_no_state():
    args, one = turn()
    target = ask(args, one, Judge(FAIL), state_token=None)['escalate_to']
    assert target['head'] == 'sol-high' and target['state_token'] is None


def test_the_ceiling_fails_the_verdict_but_offers_no_target():
    args, one = turn(keys=('luna', 'terra'))
    result = ask(args, one, Judge(FAIL))
    assert (result['verdict'], result['escalate_to']) == ('fail', None)
    assert result['trace']['escalation']['reason'] == 'ESCALATION_CEILING'


def test_a_target_that_left_the_inventory_is_not_offered():
    args, one = turn()
    args['models'] = [m for m in args['models'] if 'sol' not in m['name']]
    result = ask(args, one, Judge(FAIL))
    assert (result['verdict'], result['escalate_to']) == ('fail', None)
    assert result['trace']['escalation']['reason'] == 'ESCALATION_TARGET_UNAVAILABLE'


def test_the_resolve_trace_names_the_head_a_failed_guard_would_use():
    _, one = turn()
    assert one['trace']['escalation']['next'] == 'sol-high'


# --- one escalation per turn ------------------------------------------------------------------------------------
def test_a_second_guard_call_in_the_same_invocation_passes_with_guard_once():
    args, one = turn()
    first = ask(args, one, Judge(FAIL))
    judge = Judge(FAIL)
    second = ask(args, {'pin': first['escalate_to']['pin'], 'state_token': first['escalate_to']['state_token']}, judge)
    assert (second['verdict'], second['code'], second['escalate_to'], judge.calls) == ('pass', 'GUARD_ONCE', None, [])
    replay = ask(args, one, judge, last_response={'refusal': True})  # the old pin cannot be used again either
    assert (replay['verdict'], replay['code'], judge.calls) == ('pass', 'GUARD_ONCE', [])


def test_a_pass_does_not_use_up_the_escalation():
    args, one = turn()
    assert ask(args, one, Judge(PASS))['verdict'] == 'pass'
    assert ask(args, one, Judge(FAIL))['verdict'] == 'fail'


def test_a_ceiling_failure_does_not_use_up_the_escalation():
    args, one = turn(keys=('luna', 'terra'))
    ask(args, one, Judge(FAIL))
    assert ask(args, one, Judge(FAIL))['code'] == 'quality_material'


# --- authorization and input ------------------------------------------------------------------------------------
@pytest.mark.parametrize('change', [
    {'scope_id': '2' * 64}, {'invocation_id': 'c' * 64}, {'pin': 'forged.pin'}, {'pin': None}, {'answer_text': 5},
    {'request_text_projection': 7}, {'answer_id': 3}, {'last_response': 'length'}, {'last_response': {'refusal': 'yes'}}])
def test_foreign_or_malformed_requests_are_refused(change):
    args, one = turn()
    with pytest.raises(RoutingUnavailable):
        ask(args, one, **change)


def test_another_user_cannot_use_the_pin():
    args, one = turn()
    args['user_id'] = 3
    req = {'pin': one['pin'], 'scope_id': '1' * 64, 'invocation_id': 'a' * 64, 'request_text_projection': PROJECTION,
           'answer_text': 'a'}
    with pytest.raises(RoutingUnavailable):
        guard.guard(req, project_id=7, user_id=3, settings=args['settings'], models=args['models'],
                    price_snapshot=args['price_snapshot'], signing_key=args['signing_key'], complete=Judge(), now=args['now'])


def test_a_disabled_project_is_refused():
    args, one = turn()
    args['settings'] = {**args['settings'], 'enabled': False}
    with pytest.raises(RoutingUnavailable):
        ask(args, one)


def test_a_pin_without_a_plan_cannot_escalate():
    args, one = turn()
    pin = decode_pin(one['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=args['now'])
    pin.pop('escalation_plan')
    from routing.service import encode_pin
    old = {**one, 'pin': encode_pin(pin, args['signing_key'])}
    result = ask(args, old, Judge(FAIL))
    assert (result['verdict'], result['code']) == ('pass', 'GUARD_NO_PLAN')


# --- observability: names, codes and a sha only ------------------------------------------------------------------
def test_log_line_and_usage_record_carry_no_answer_or_request_text():
    args, one = turn()
    judge = Judge({'material': True, 'clause': 'ii', 'cited_defect': SENTINEL})
    req = {'pin': one['pin'], 'scope_id': '1' * 64, 'invocation_id': 'a' * 64, 'answer_id': 'm9',
           'request_text_projection': {'latest': {'text': SENTINEL}}, 'answer_text': SENTINEL}
    result = guard.guard(req, project_id=7, user_id=2, settings=args['settings'], models=args['models'],
                         price_snapshot=args['price_snapshot'], signing_key=args['signing_key'], complete=judge, now=args['now'])
    assert result['cited_defect'] == SENTINEL  # the SDK gets the defect; nothing else does
    record = guard.usage_record(7, '1' * 64, result, now=77)
    line = guard.log_line(7, req, result)
    for text in (json.dumps(record), line, json.dumps(result['trace'])):
        assert 'SENTINEL' not in text and 'zero trust' not in text
    assert record == {
        'kind': 'auto_routing_escalation', 'scope_id': '1' * 64, 'project_id': 7, 'head': 'terra-default',
        'escalation_event': 'guard', 'escalation_from': 'terra-default', 'escalation_to': 'sol-high', 'level': 1,
        'mode': 'economical', 'reason': 'ESCALATED', 'guard_verdict': 'fail', 'guard_code': 'quality_material',
        'guard_clause': 'ii', 'guard_error': None, 'first_answer_id': 'm9',
        'first_answer_sha256': guard.sha(SENTINEL), 'ts': 77}
    assert line.startswith('auto-routing guard project=7 scope=11111111 verdict=fail code=quality_material clause=ii ')
    assert 'escalation=guard/terra-default->sol-high' in line and f'answer_sha={guard.sha(SENTINEL)[:12]}' in line
    assert '\n' not in line


def test_a_pass_record_names_the_verdict_and_no_move():
    args, one = turn()
    result = ask(args, one, Judge(PASS), answer_id='m1')
    record = guard.usage_record(7, '1' * 64, result, now=1)
    assert (record['guard_verdict'], record['escalation_event'], record['escalation_to'], record['reason']) == (
        'pass', 'none', 'terra-default', None)
