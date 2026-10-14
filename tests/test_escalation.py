"""#6722 section 13: cheap-first routing, signal-based escalation, sticky level and its observability record."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import args_for, classifier, request
from routing import escalation
from routing.service import RoutingUnavailable, compiled_router, decision_log_line, resolve, restore_state

ALL = ('luna', 'terra', 'sol', 'sonnet55', 'opus55', 'gpt54')
# Deep ladder of ALL: Terra (provisional), Sol high (eligible), Sonnet 5.5 (provisional), Opus 5.5 high
# (provisional); one head per canonical model, each strictly dearer than the one before.
LADDER = ['terra-default', 'sol-high', 'sonnet-5-5-default', 'opus-5-5-high']
SENTINEL = 'SENTINEL-do-not-store-5519 zero trust worker'


def first(keys=ALL, demand='deep', settings=None, **args):
    args = args_for(*keys, **args)
    args['settings'] = {**args['settings'], **(settings or {})}
    result = resolve(request('Analyze the supplied figures.'), complete=classifier(demand=demand), **args)
    return args, result


def follow(args, previous, text='Please also add the totals.', demand='deep', relation='followup', **fields):
    """The next turn of the same scope: a follow-up that references the first request."""
    req = request(text, invocation_id='b' * 64, **fields)
    req['messages'] = [{'role': 'user', 'content': 'Analyze the supplied figures.'},
                       {'role': 'assistant', 'content': 'Done.'}, {'role': 'user', 'content': text}]
    req['state_token'] = previous['state_token']
    reference = ['m0'] if relation != 'independent' else []
    return resolve(req, complete=classifier(demand=demand, relation=relation, reference_ids=reference), **args)


def head(result):
    return result['trace']['selection']['variant']


def moved(result):
    return result['trace']['escalation']


def state_of(args, result):
    return restore_state(result['state_token'], args['signing_key'], project_id=7, user_id=2, scope_id='1' * 64,
                         gate_revision='g1', policy_revision=compiled_router().revision)


# --- policy: economical is cheapest-admitted at every demand; premium_at_deep keeps the top tier at deep ------
@pytest.mark.parametrize('settings', [None, {'escalation_mode': 'economical'}, {'escalation_mode': 'unknown-mode'}, {'escalation_mode': 'premium'}])
def test_economical_and_absent_modes_choose_the_cheapest_admitted_head_at_deep(settings):
    _, result = first(settings=settings)
    assert head(result) == LADDER[0] and result['trace']['selection']['tier'] == 'provisional'
    assert moved(result) == {'mode': 'economical', 'event': 'none', 'from': None, 'to': 'terra-default',
                             'level': 0, 'reason': None, 'ladder': LADDER}


def test_economical_deep_picks_a_cheaper_eligible_head_when_there_is_one():
    _, result = first(prices={'sol': ('.0000001', '.0000001')})
    assert head(result) == 'sol-high' and result['trace']['selection']['tier'] == 'eligible'


def test_premium_at_deep_starts_deep_cells_at_the_top_tier_head():
    _, result = first(settings={'escalation_mode': 'premium_at_deep'})
    assert head(result) == 'sol-high' and result['trace']['selection']['tier'] == 'eligible'
    assert result['config']['model_name'] == 'global.openai.gpt-5.6-sol'
    assert moved(result)['mode'] == 'premium_at_deep' and moved(result)['reason'] == 'PREMIUM_START'
    assert result['trace']['selection']['economics']['output_range'][1] == (
        result['trace']['selection']['economics']['quotes']['sol-high']['output_allowance'])


@pytest.mark.parametrize('demand', ['simple', 'standard'])
@pytest.mark.parametrize('mode', ['economical', 'premium_at_deep'])
def test_simple_and_standard_never_change_with_the_mode(demand, mode):
    _, result = first(demand=demand, settings={'escalation_mode': mode})
    assert head(result) == 'luna-default' and moved(result)['reason'] is None


def test_premium_at_deep_without_a_top_tier_head_keeps_the_cheapest():
    _, result = first(keys=('luna', 'terra', 'sonnet55'), settings={'escalation_mode': 'premium_at_deep'})
    assert head(result) == 'terra-default' and moved(result)['reason'] is None


# --- premium_from_standard: the same top-tier rule at standard and deep, simple stays cheapest ------------------------
PREMIUM_STANDARD = {'escalation_mode': 'premium_from_standard'}


def test_premium_from_standard_matches_premium_at_deep_at_deep_and_leaves_simple_alone():
    _, deep = first(settings=PREMIUM_STANDARD)
    assert head(deep) == 'sol-high' and moved(deep)['mode'] == 'premium_from_standard'
    _, simple = first(demand='simple', settings=PREMIUM_STANDARD)
    assert head(simple) == 'luna-default' and moved(simple)['reason'] is None


def test_premium_from_standard_starts_standard_at_the_cheapest_eligible_tier_head():
    # On the current table every admitted standard head is eligible, so the cheapest eligible head is Luna.
    _, result = first(demand='standard', settings=PREMIUM_STANDARD)
    assert head(result) == 'luna-default' and result['trace']['selection']['tier'] == 'eligible'
    # Where the cheapest standard head is only provisional (Anthropic over an OpenAI-compatible deployment),
    # the premium start is the cheapest eligible-tier head instead; economical keeps the provisional one.
    cheap = {'prices': {'sonnet55': ('.0000001', '.0000001')}, 'sonnet55': {'openai_compatible': True}}
    _, economical = first(keys=('sol', 'sonnet55'), demand='standard', **cheap)
    _, premium = first(keys=('sol', 'sonnet55'), demand='standard', settings=PREMIUM_STANDARD, **cheap)
    assert (head(economical), economical['trace']['selection']['tier']) == ('sonnet-5-5-default', 'provisional')
    assert (head(premium), premium['trace']['selection']['tier']) == ('sol-default', 'eligible')
    assert moved(premium)['reason'] == 'PREMIUM_START'


def test_premium_from_standard_escalation_changes_the_model_not_the_effort():
    args, one = first(demand='standard', settings=PREMIUM_STANDARD)
    two = follow(args, one, demand='standard', **FIELDS['regenerate'])
    variants = compiled_router().catalog['variants']
    assert head(one) == 'luna-default' and moved(two)['reason'] == 'ESCALATED'
    assert variants[head(two)]['canonical_model'] != 'openai/gpt-5-6-luna' and head(two) != 'luna-high'
    assert 'luna-high' not in moved(two)['ladder']
    three = follow(args, two, demand='standard')  # sticky from the premium start
    assert head(three) == head(two) and moved(three)['reason'] == 'STICKY'


# --- signals ---------------------------------------------------------------------------------------------------
FIELDS = {
    'upgrade_control': {'escalation': {'event': 'upgrade_control', 'from_message_id': 'm1'}},
    'regenerate': {'escalation': {'event': 'regenerate'}},
    'refusal': {'last_response': {'finish_reason': 'stop', 'refusal': True}},
    'incomplete': {'last_response': {'finish_reason': 'length'}},
    'tool_failure': {'last_response': {'tool_failure': True}},
    'inability': {'last_response': {'inability': True}},
}


@pytest.mark.parametrize('event', sorted(FIELDS))
def test_each_request_signal_moves_the_scope_to_the_next_admitted_head(event):
    args, one = first()
    two = follow(args, one, **FIELDS[event])
    assert moved(two) == {'mode': 'economical', 'event': event, 'from': 'terra-default', 'to': 'sol-high',
                          'level': 1, 'reason': 'ESCALATED', 'ladder': LADDER}
    assert head(two) == 'sol-high' and two['config']['model_name'] == 'global.openai.gpt-5.6-sol'
    assert two['trace']['selection']['tier'] == 'eligible' and two['trace']['selection']['provisional'] is False


def test_a_correction_turn_is_the_correction_event():
    args, one = first()
    two = follow(args, one, text="That's wrong, you missed the totals.")
    assert moved(two)['event'] == 'correction' and head(two) == 'sol-high'


@pytest.mark.parametrize('stronger,weaker', [
    ('upgrade_control', 'correction'), ('regenerate', 'correction'), ('correction', 'refusal'),
    ('refusal', 'incomplete'), ('incomplete', 'tool_failure'), ('tool_failure', 'inability')])
def test_only_the_strongest_event_fires_per_resolve(stronger, weaker):
    fields = {}
    for name in (stronger, weaker):
        for key, value in FIELDS.get(name, {}).items():
            fields[key] = {**fields.get(key, {}), **value} if key == 'last_response' else value
    text = "That's wrong." if 'correction' in (stronger, weaker) else 'Please also add the totals.'
    args, one = first()
    two = follow(args, one, text=text, **fields)
    assert moved(two)['event'] == stronger and moved(two)['level'] == 1


def test_all_signals_together_resolve_to_one_step_not_several():
    args, one = first()
    fields = {'escalation': FIELDS['regenerate']['escalation'],
              'last_response': {'finish_reason': 'length', 'refusal': True, 'tool_failure': True, 'inability': True}}
    two = follow(args, one, text="That's wrong.", **fields)
    assert moved(two)['event'] == 'regenerate' and head(two) == 'sol-high'


@pytest.mark.parametrize('fields', [
    {'escalation': {'event': 'silently_upgrade'}}, {'escalation': 'regenerate'}, {'escalation': {'event': 'regenerate', 'from_message_id': 7}},
    {'last_response': 'length'}, {'last_response': {'refusal': 'yes'}}, {'last_response': {'finish_reason': 3}}])
def test_malformed_signal_fields_are_refused(fields):
    args, one = first()
    with pytest.raises(RoutingUnavailable):
        follow(args, one, **fields)


def test_no_event_without_a_signal_and_a_plain_followup_stays_cheap():
    args, one = first()
    two = follow(args, one)
    assert head(two) == 'terra-default' and moved(two)['event'] == 'none' and moved(two)['reason'] is None
    assert 'escalation' not in state_of(args, two)


# --- correction rule ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize('text', [
    "That's wrong.", 'that is incorrect, try again', 'THIS WAS NOT CORRECT', "You missed the second table.", 'you forgot the units',
    'As I said, use the staging database.', 'As I asked earlier: only totals.', "This is not what I asked for.",
    "It doesn't work on my machine.", 'It did not work', 'The build still fails', "That’s wrong"])
def test_correction_rule_positives(text):
    assert escalation.correction_event([{'role': 'user', 'content': text}], {'relation': 'followup'}) == {'correction'}


@pytest.mark.parametrize('text', [
    'Please also add tests.', 'Thanks, that is great.', 'Now explain the second table.', 'Add error handling and a retry.',
    'What would be wrong with a hash join here?', 'Can you summarize?', ''])
def test_correction_rule_negatives(text):
    assert escalation.correction_event([{'role': 'user', 'content': text}], {'relation': 'followup'}) == set()


def test_correction_needs_a_followup_or_return_relation_and_a_user_turn_and_the_first_400_chars():
    wrong = [{'role': 'user', 'content': "That's wrong."}]
    assert escalation.correction_event(wrong, {'relation': 'independent'}) == set()
    assert escalation.correction_event(wrong, {'relation': 'ambiguous'}) == set()
    assert escalation.correction_event(wrong, {'relation': 'return'}) == {'correction'}
    assert escalation.correction_event(wrong + [{'role': 'assistant', 'content': "That's wrong."}], {'relation': 'followup'}) == set()
    late = [{'role': 'user', 'content': 'x' * 400 + " that's wrong"}]
    assert escalation.correction_event(late, {'relation': 'followup'}) == set()


def test_an_independent_turn_with_correction_wording_does_not_escalate():
    args, one = first()
    two = follow(args, one, text="That's wrong.", relation='independent')
    assert moved(two)['event'] == 'none'


# --- the ladder moves between models, never between efforts of one model -------------------------------------------
def test_at_simple_luna_escalates_to_the_next_model_never_to_luna_high():
    args, one = first(demand='simple')
    two = follow(args, one, demand='simple', **FIELDS['regenerate'])
    assert head(one) == 'luna-default' and head(two) == 'terra-default'
    assert moved(two)['ladder'] == ['luna-default', 'terra-default', 'sol-default', 'sonnet-5-5-default', 'opus-5-5-high']
    assert 'luna-high' not in moved(two)['ladder']
    three = follow(args, two, demand='simple', **FIELDS['refusal'])  # a second signal: one more step
    assert (head(three), moved(three)['level']) == ('sol-default', 2)


def test_at_deep_the_ladder_is_terra_sol_sonnet_opus_by_price_with_no_same_model_steps():
    args, result = first()
    heads = [head(result)]
    for _ in range(4):
        result = follow(args, result, **FIELDS['regenerate'])
        heads.append(head(result))
    assert heads == LADDER + [LADDER[-1]]  # three steps, then the ceiling keeps Opus
    assert moved(result)['reason'] == 'ESCALATION_CEILING' and moved(result)['ladder'] == LADDER
    variants = compiled_router().catalog['variants']
    assert len({variants[h]['canonical_model'] for h in LADDER}) == len(LADDER)


def test_a_cheaper_effort_variant_of_the_current_model_is_skipped_as_a_step():
    args, one = first(demand='standard')
    two = follow(args, one, demand='standard', **FIELDS['regenerate'])
    assert head(one) == 'luna-default' and moved(two)['to'] != 'luna-high'
    assert compiled_router().catalog['variants'][head(two)]['canonical_model'] != 'openai/gpt-5-6-luna'


# --- escalation target, stickiness, ceiling, no prior route ------------------------------------------------------
def test_escalation_walks_the_ladder_one_head_per_event_and_is_sticky_across_turns():
    args, one = first()
    two = follow(args, one, **FIELDS['regenerate'])
    assert head(two) == 'sol-high'
    three = follow(args, two)  # no signal: the scope stays where it was moved, not back at the default
    assert head(three) == 'sol-high' and moved(three) == {
        'mode': 'economical', 'event': 'none', 'from': 'sol-high', 'to': 'sol-high', 'level': 1, 'reason': 'STICKY',
        'ladder': LADDER}
    four = follow(args, three, **FIELDS['refusal'])  # a further event raises it again
    assert (head(four), moved(four)['from'], moved(four)['level']) == ('sonnet-5-5-default', 'sol-high', 2)
    assert state_of(args, four)['escalation'] == {'event': 'refusal', 'from': 'sol-high', 'level': 2, 'to': 'sonnet-5-5-default'}
    five = follow(args, four)
    assert head(five) == 'sonnet-5-5-default' and moved(five)['reason'] == 'STICKY'


def test_a_new_scope_starts_at_the_default():
    args, one = first()
    two = follow(args, one, **FIELDS['regenerate'])
    fresh = resolve(request('Analyze the supplied figures.', scope_id='2' * 64), complete=classifier(demand='deep'), **args)
    assert head(two) == 'sol-high' and head(fresh) == 'terra-default' and moved(fresh)['level'] == 0


def test_the_level_is_applied_on_the_demand_of_the_later_turn():
    args, one = first()
    two = follow(args, one, **FIELDS['regenerate'])
    simple = follow(args, two, demand='simple')  # the ladder of a simple turn starts at Luna: one step is Terra
    assert head(simple) == 'terra-default' and moved(simple)['level'] == 1


def test_the_ceiling_keeps_the_current_head_and_says_so():
    args, one = first(keys=('luna', 'terra', 'sol'))
    two = follow(args, one, **FIELDS['regenerate'])
    three = follow(args, two, **FIELDS['regenerate'])
    assert (head(two), moved(two)['reason']) == ('sol-high', 'ESCALATED')
    assert (head(three), moved(three)['reason'], moved(three)['event']) == ('sol-high', 'ESCALATION_CEILING', 'regenerate')
    assert moved(three)['from'] == moved(three)['to'] == 'sol-high'
    assert follow(args, three)['trace']['selection']['variant'] == 'sol-high'


def test_a_single_admitted_head_is_a_ceiling_from_the_start():
    args, one = first(keys=('luna', 'terra'))  # Luna is out at deep: Terra is the only admitted head
    two = follow(args, one, **FIELDS['regenerate'])
    assert head(two) == 'terra-default' and moved(two)['reason'] == 'ESCALATION_CEILING' and moved(two)['level'] == 0
    assert 'escalation' not in state_of(args, two)


def test_no_escalation_without_a_prior_route_in_the_scope():
    args, _ = first()
    req = request('Analyze the supplied figures.', escalation={'event': 'regenerate'})
    result = resolve(req, complete=classifier(demand='deep'), **args)
    assert head(result) == 'terra-default'
    assert moved(result) == {'mode': 'economical', 'event': 'regenerate', 'from': None, 'to': 'terra-default',
                             'level': 0, 'reason': 'NO_PRIOR_ROUTE', 'ladder': []}
    assert 'escalation' not in state_of(args, result)


def test_a_state_token_of_another_scope_is_refused():
    args, one = first()
    req = request('Again', scope_id='3' * 64, state_token=one['state_token'], escalation={'event': 'regenerate'})
    with pytest.raises(RoutingUnavailable):
        resolve(req, complete=classifier(demand='deep'), **args)


def test_premium_at_deep_still_escalates_on_a_signal_from_the_top_tier():
    args, one = first(settings={'escalation_mode': 'premium_at_deep'})
    two = follow(args, one, **FIELDS['regenerate'])
    assert (head(one), head(two), moved(two)['level']) == ('sol-high', 'sonnet-5-5-default', 1)
    three = follow(args, two)
    assert head(three) == 'sonnet-5-5-default' and moved(three)['reason'] == 'STICKY'


def test_the_uncertain_baseline_has_no_ranked_heads_to_climb():
    args, one = first(keys=('luna', 'gpt54', 'haiku'))  # nothing clears the deep floor: the baseline answers
    two = follow(args, one, **FIELDS['regenerate'])
    assert head(two) == 'gpt54-medium' and moved(two)['reason'] == 'ESCALATION_NO_RANKED_HEADS'


def test_the_escalated_selection_is_a_complete_admitted_selection():
    args, one = first()
    two = follow(args, one, **FIELDS['regenerate'])
    admission = two['trace']['selection']['admission']
    assert admission['status'] == 'admitted' and admission['tier'] == 'eligible' and admission['demand'] == 'deep'
    assert two['config']['reasoning_effort'] == 'high'
    assert two['trace']['selection']['economics']['output_range'][1] == (
        two['trace']['selection']['economics']['quotes']['sol-high']['output_allowance'])
    assert state_of(args, two)['decision']['selection']['variant'] == 'sol-high'


# --- observability: trace, log line, usage record carry names and codes only ------------------------------------
def test_the_log_line_names_the_event_and_the_move_or_a_dash():
    args, one = first()
    plain = follow(args, one)
    req = request(SENTINEL, invocation_id='b' * 64, **FIELDS['regenerate'])
    req['messages'] = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}, {'role': 'user', 'content': SENTINEL}]
    req['state_token'] = one['state_token']
    two = resolve(req, complete=classifier(demand='deep', relation='followup', reference_ids=['m0'], reason=SENTINEL), **args)
    assert decision_log_line(7, req, plain).endswith(' escalation=-')
    line = decision_log_line(7, req, two)
    assert line.endswith(' escalation=regenerate/terra-default->sol-high')
    assert 'SENTINEL' not in line and 'zero' not in line and '\n' not in line


def test_trace_log_and_usage_record_never_carry_request_text():
    args, one = first()
    req = request(SENTINEL, invocation_id='b' * 64, escalation={'event': 'upgrade_control', 'from_message_id': 'm1'},
                  last_response={'finish_reason': 'length'})
    req['messages'] = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}, {'role': 'user', 'content': SENTINEL}]
    req['state_token'] = one['state_token']
    two = resolve(req, complete=classifier(demand='deep', reason=SENTINEL), **args)
    record = escalation.usage_record(7, req, two, now=1234)
    assert record == {'kind': 'auto_routing_escalation', 'scope_id': '1' * 64, 'project_id': 7, 'demand': 'deep',
                      'family': 'quantitative', 'head': 'sol-high', 'model': 'global.openai.gpt-5.6-sol',
                      'escalation_event': 'upgrade_control', 'escalation_from': 'terra-default',
                      'escalation_to': 'sol-high', 'level': 1, 'mode': 'economical', 'reason': 'ESCALATED', 'ts': 1234}
    assert set(moved(two)) == {'mode', 'event', 'from', 'to', 'level', 'reason', 'ladder'}
    for text in (json.dumps(record), json.dumps(moved(two)), decision_log_line(7, req, two),
                 json.dumps(state_of(args, two)['escalation'])):
        assert 'SENTINEL' not in text and 'zero trust' not in text


def test_every_resolve_writes_a_record_even_without_an_event():
    _, one = first()
    record = escalation.usage_record(7, request(), one, now=5)
    assert record['escalation_event'] == 'none' and record['escalation_to'] == 'terra-default' and record['level'] == 0
    assert escalation.usage_record(7, request(), {'trace': {'reason': 'REAUTHORIZED_CHECKPOINT_PIN'}}) is None


def test_record_escalation_logs_one_json_line(monkeypatch):
    lines = []
    log = types.SimpleNamespace(info=lines.append, exception=lambda *a, **k: None)
    tools = types.ModuleType('pylon.core.tools')
    tools.log = log
    for name, module in (('pylon', types.ModuleType('pylon')), ('pylon.core', types.ModuleType('pylon.core')),
                         ('pylon.core.tools', tools)):
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location('metering_under_test', Path(__file__).resolve().parents[1] / 'utils' / 'metering.py')
    metering = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metering)
    metering.record_escalation(None)
    assert lines == []
    metering.record_escalation({'kind': 'auto_routing_escalation', 'escalation_event': 'regenerate', 'level': 1})
    assert len(lines) == 1 and lines[0].startswith('auto_routing_escalation {')
    assert json.loads(lines[0].split(' ', 1)[1]) == {'escalation_event': 'regenerate', 'kind': 'auto_routing_escalation', 'level': 1}
