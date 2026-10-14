"""#6722 section 13.5: the answer guard is record-only: a verdict on the delivered answer, never a change to the turn."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import CLASSIFIER, args_for, classifier, request
from routing import guard
from routing.service import RoutingUnavailable, decode_pin, resolve

ALL = ('luna', 'terra', 'sol', 'sonnet55', 'opus55', 'gpt54')
SENTINEL = 'SENTINEL-do-not-log-8842 zero trust worker'
PROJECTION = {'latest': {'text': 'Total the invoices and list five risks.'}}
PASS = {'material': False, 'clause': 'none', 'cited_defect': ''}
FAIL = {'material': True, 'clause': 'iii', 'cited_defect': 'Only four risks are listed; five were requested.'}


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
    assert_record_only(result)
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
    assert (result['verdict'], result['clause']) == ('pass', None) and len(judge.calls) == 1
    assert_record_only(result)
    call = judge.calls[0]
    assert (call['model'], call['effort'], call['max_tokens']) == (CLASSIFIER, None, guard.JUDGE_MAX_TOKENS)
    assert call['messages'][0] == {'role': 'system', 'content': guard.GUARD_RUBRIC}
    body = json.loads(call['messages'][1]['content'])
    assert body == {'request': PROJECTION, 'answer': 'Total: 120. Risks: a, b, c, d.', 'answer_truncated': False}


def test_a_material_answer_fails_with_the_clause_and_the_cited_defect():
    args, one = turn()
    result = ask(args, one, Judge(FAIL))
    assert (result['verdict'], result['code'], result['clause']) == ('fail', 'quality_material', 'iii')
    assert_record_only(result)
    guard_trace = result['trace']['escalation']['guard']
    assert (guard_trace['verdict'], guard_trace['clause'], guard_trace['code']) == ('fail', 'iii', 'quality_material')
    assert result['trace']['escalation']['event'] == 'none'  # the guard records; it never escalates


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
    assert (result['verdict'], result['code']) == ('pass', 'GUARD_SCHEMA_INVALID')
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


# --- record-only: the guard never changes the turn ---------------------------------------------------------------
def assert_record_only(result):
    assert set(result) == {'verdict', 'code', 'clause', 'scope_id', 'invocation_id', 'trace'}
    for forbidden in ('escalate_to', 'pin', 'state_token', 'config', 'cited_defect'):
        assert forbidden not in result and forbidden not in json.dumps(result['trace'])


def test_a_failed_guard_leaves_the_scope_where_it_was_for_the_next_resolve():
    args, one = turn()
    assert ask(args, one, Judge(FAIL))['verdict'] == 'fail'
    nxt = request('Thanks. Now the same for March.', invocation_id='b' * 64)
    nxt['messages'] = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}, nxt['messages'][0]]
    nxt['state_token'] = one['state_token']
    again = resolve(nxt, complete=classifier(demand='deep', relation='followup', reference_ids=['m0']), **args)
    assert again['trace']['selection']['variant'] == 'terra-default' and again['trace']['escalation']['event'] == 'none'


def test_the_next_turn_signal_escalates_across_models_as_before():
    args, one = turn()
    ask(args, one, Judge(FAIL))
    nxt = request('Again.', invocation_id='b' * 64, last_response={'refusal': True})
    nxt['messages'] = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}, nxt['messages'][0]]
    nxt['state_token'] = one['state_token']
    again = resolve(nxt, complete=classifier(demand='deep', relation='followup', reference_ids=['m0']), **args)
    assert again['trace']['selection']['variant'] == 'sol-high' and again['trace']['escalation']['event'] == 'refusal'


def test_the_guard_can_be_asked_again_for_the_same_invocation_and_each_ask_is_a_record():
    args, one = turn()
    assert [ask(args, one, Judge(FAIL))['verdict'] for _ in range(2)] == ['fail', 'fail']


def test_the_resolve_trace_names_the_head_the_next_step_would_use():
    _, one = turn()
    assert one['trace']['escalation']['next'] == 'sol-high'


# --- authorization and input ------------------------------------------------------------------------------------
REFUSALS = [
    ({'scope_id': '2' * 64}, 'PIN_INVALID'), ({'invocation_id': 'c' * 64}, 'PIN_INVALID'), ({'pin': 'forged.pin'}, 'PIN_INVALID'),
    ({'scope_id': 'short'}, 'IDENTITY'), ({'invocation_id': None}, 'IDENTITY'), ({'pin': None}, 'PIN_TYPE'),
    ({'answer_text': 5}, 'ANSWER_TYPE'), ({'answer_id': 3}, 'ANSWER_ID'),
    ({'request_text_projection': 7}, 'PROJECTION_TYPE'), ({'request_text_projection': None}, 'PROJECTION_TYPE'),
    ({'request_text_projection': [{'role': 'user', 'content': 'x'}]}, 'PROJECTION_TYPE'),  # the full history list
    ({'request_text_projection': {'latest': 'q', 'recent': 'x'}}, 'PROJECTION_TYPE'),
    ({'request_text_projection': {'latest': 'q', 'recent': [{'role': 'user'}]}}, 'PROJECTION_TYPE'),
    ({'request_text_projection': {'latest': 'q', 'turns_omitted': -1}}, 'PROJECTION_TYPE'),
    ({'request_text_projection': {'latest': 'q', 'recent': [{'role': 'user', 'text': 't'}] * 33}}, 'PROJECTION_TYPE'),
    ({'request_text_projection': 'x' * (guard.MAX_PROJECTION_BYTES + 1)}, 'PROJECTION_TOO_LARGE'),
    ({'request_text_projection': {'latest': 'x' * (guard.MAX_PROJECTION_BYTES + 1)}}, 'PROJECTION_TOO_LARGE'),
    ({'last_response': 'length'}, 'LAST_RESPONSE'), ({'last_response': {'refusal': 'yes'}}, 'LAST_RESPONSE')]


@pytest.mark.parametrize('change,reason', REFUSALS)
def test_foreign_or_malformed_requests_are_refused_with_the_specific_reason(change, reason):
    args, one = turn()
    with pytest.raises(RoutingUnavailable) as refused:
        ask(args, one, **change)
    assert refused.value.reason == reason


def test_a_disabled_project_and_a_non_object_request_have_reasons():
    args, one = turn()
    with pytest.raises(RoutingUnavailable) as refused:
        guard.guard(None, project_id=7, user_id=2, settings=args['settings'], models=args['models'],
                    price_snapshot=args['price_snapshot'], signing_key=args['signing_key'], complete=Judge(), now=args['now'])
    assert refused.value.reason == 'INVALID_REQUEST'
    with pytest.raises(RoutingUnavailable) as refused:
        guard.guard({}, project_id=7, user_id=2, settings={**args['settings'], 'enabled': False}, models=args['models'],
                    price_snapshot=args['price_snapshot'], signing_key=args['signing_key'], complete=Judge(), now=args['now'])
    assert refused.value.reason == 'DISABLED'


# --- the SDK's bounded projection and the configured judge --------------------------------------------------------
SDK_PROJECTION = {'latest': 'Total the invoices and list five risks.',
                  'recent': [{'role': 'user', 'text': 'Here are the invoices.'}, {'role': 'assistant', 'text': 'Received.'}],
                  'turns_omitted': 3}


def test_the_sdk_projection_grades_the_latest_request_with_the_recent_turns_as_context():
    args, one = turn()
    judge = Judge(PASS)
    assert ask(args, one, judge, request_text_projection=SDK_PROJECTION)['verdict'] == 'pass'
    body = json.loads(judge.calls[0]['messages'][1]['content'])
    assert body == {'request': SDK_PROJECTION['latest'], 'context': SDK_PROJECTION['recent'], 'turns_omitted': 3,
                    'answer': 'Total: 120. Risks: a, b, c, d.', 'answer_truncated': False}
    assert 'context' in guard.GUARD_RUBRIC and 'latest request' in guard.GUARD_RUBRIC


def test_the_sdk_projection_without_recent_turns_is_accepted_and_the_size_cap_still_holds():
    args, one = turn()
    judge = Judge(PASS)
    ask(args, one, judge, request_text_projection={'latest': 'Summarise.'})
    assert json.loads(judge.calls[0]['messages'][1]['content'])['context'] == []
    near = {'latest': 'x' * (guard.MAX_PROJECTION_BYTES - 200), 'recent': [], 'turns_omitted': 0}
    assert ask(args, one, Judge(PASS), request_text_projection=near)['verdict'] == 'pass'


def test_the_judge_is_the_configured_classifier_deployment_and_the_full_history_is_never_sent():
    args, one = turn()
    other = {**next(m for m in args['models'] if m['name'] == CLASSIFIER), 'name': 'other-judge'}
    args['models'] = args['models'] + [other]
    args['price_snapshot'] = {**args['price_snapshot'], 'entries': args['price_snapshot']['entries'] + [
        {**args['price_snapshot']['entries'][0], 'model_name': 'other-judge'}]}
    args['settings'] = {**args['settings'], 'classifier': {'name': 'other-judge', 'project_id': 7, 'source': 'project'}}
    judge = Judge(PASS)
    ask(args, one, judge, request_text_projection=SDK_PROJECTION)
    assert [c['model'] for c in judge.calls] == ['other-judge']


def test_an_unpriced_configured_classifier_passes_with_a_guard_error_and_no_call():
    args, one = turn()
    args['price_snapshot'] = {**args['price_snapshot'],
                              'entries': [e for e in args['price_snapshot']['entries'] if e['model_name'] != CLASSIFIER]}
    judge = Judge(FAIL)
    result = ask(args, one, judge, request_text_projection=SDK_PROJECTION)
    assert (result['verdict'], result['code'], judge.calls) == ('pass', 'GUARD_PRICE_UNAVAILABLE', [])
    assert result['trace']['escalation']['guard']['guard_error'] == 'GUARD_PRICE_UNAVAILABLE'


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


def test_a_pin_without_routing_context_still_gets_a_verdict():
    args, one = turn()
    pin = decode_pin(one['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=args['now'])
    pin.pop('routing_context')
    from routing.service import encode_pin
    result = ask(args, {**one, 'pin': encode_pin(pin, args['signing_key'])}, Judge(FAIL))
    assert (result['verdict'], result['trace']['escalation']['from']) == ('fail', None)


# --- observability: names, codes and a sha only ------------------------------------------------------------------
def test_log_line_and_usage_record_carry_no_answer_or_request_text():
    args, one = turn()
    judge = Judge({'material': True, 'clause': 'ii', 'cited_defect': SENTINEL})
    req = {'pin': one['pin'], 'scope_id': '1' * 64, 'invocation_id': 'a' * 64, 'answer_id': 'm9',
           'request_text_projection': {'latest': {'text': SENTINEL}}, 'answer_text': SENTINEL}
    result = guard.guard(req, project_id=7, user_id=2, settings=args['settings'], models=args['models'],
                         price_snapshot=args['price_snapshot'], signing_key=args['signing_key'], complete=judge, now=args['now'])
    assert SENTINEL not in json.dumps(result)  # even the judge's cited defect stays out of the response
    record = guard.usage_record(7, '1' * 64, result, now=77)
    line = guard.log_line(7, req, result)
    for text in (json.dumps(record), line, json.dumps(result['trace'])):
        assert 'SENTINEL' not in text and 'zero trust' not in text
    assert record == {
        'kind': 'auto_routing_escalation', 'scope_id': '1' * 64, 'project_id': 7, 'demand': 'deep',
        'family': 'quantitative', 'head': 'terra-default', 'escalation_event': 'none', 'escalation_from': 'terra-default',
        'escalation_to': 'terra-default', 'level': 0, 'mode': 'economical', 'reason': None, 'arm': None,
        'guard_verdict': 'fail', 'guard_code': 'quality_material', 'guard_clause': 'ii', 'guard_error': None,
        'first_answer_id': 'm9', 'first_answer_sha256': guard.sha(SENTINEL), 'ts': 77}
    assert line.startswith('auto-routing guard project=7 scope=11111111 verdict=fail code=quality_material clause=ii ')
    assert 'escalation=none/terra-default->terra-default' in line and f'answer_sha={guard.sha(SENTINEL)[:12]}' in line
    assert '\n' not in line


def test_a_pass_record_names_the_verdict_and_no_move():
    args, one = turn()
    result = ask(args, one, Judge(PASS), answer_id='m1')
    record = guard.usage_record(7, '1' * 64, result, now=1)
    assert (record['guard_verdict'], record['escalation_event'], record['escalation_to'], record['reason']) == (
        'pass', 'none', 'terra-default', None)
    assert_record_only(result)
