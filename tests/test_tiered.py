"""#6722: `tiered` mode: simple cheapest; standard split per scope between the cheapest and the mid tier; deep at the frontier."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import args_for, classifier, request
from routing import escalation, guard
from routing.service import resolve

ALL = ('luna', 'terra', 'sol', 'sonnet55', 'opus55', 'gpt54')
TIERED = {'escalation_mode': 'tiered'}


def scope(i):
    return hashlib.sha256(f'synthetic-{i}'.encode()).hexdigest()


def find_scope(arm, share=0.2):
    return next(scope(i) for i in range(10_000) if escalation.arm_of(scope(i), share) == arm)


def run(demand='standard', scope_id=None, settings=TIERED, keys=ALL, **fields):
    args = args_for(*keys)
    args['settings'] = {**args['settings'], **settings}
    req = request('Total the invoices.', **({'scope_id': scope_id} if scope_id else {}))
    result = resolve(req, complete=classifier(demand=demand), **args)
    return args, result


def head(result):
    return result['trace']['selection']['variant']


def moved(result):
    return result['trace']['escalation']


# --- tier derivation from a fixture table ---------------------------------------------------------------------
def fixture_rows(spec):
    """spec: variant -> (tier, lb90). Returns the ladder (cheapest first), rows and canonical models."""
    ladder = list(spec)
    rows = {v: {'tier': tier, 'cell': {'lb90': lb90}} for v, (tier, lb90) in spec.items()}
    family = {v: v.split('-')[0] for v in ladder}  # 'a-low' and 'a-high' are one model
    return ladder, rows, family


TIERING = {'scope_id': find_scope('mid'), 'share': escalation.share_of({})}


def test_mid_tier_excludes_the_cheapest_model_and_non_eligible_rows():
    ladder, rows, family = fixture_rows({'a-low': ('eligible', .99), 'a-high': ('eligible', .98), 'b-x': ('provisional', .95),
                                         'c-x': ('eligible', .93), 'd-x': ('eligible', .97)})
    start, reason, arm = escalation._start('tiered', 'standard', ladder, rows, family, TIERING)
    assert (start, reason, arm) == ('c-x', 'TIERED_START', 'mid')  # not a-high (same model), not b (provisional)


def test_a_mid_arm_without_a_mid_head_falls_back_to_the_cheapest():
    ladder, rows, family = fixture_rows({'a-low': ('eligible', .99), 'a-high': ('eligible', .98), 'b-x': ('provisional', .95)})
    assert escalation._start('tiered', 'standard', ladder, rows, family, TIERING)[0] == 'a-low'


def test_frontier_is_the_eligible_head_with_the_highest_lower_bound_and_ties_go_to_the_cheaper():
    ladder, rows, family = fixture_rows({'a-x': ('eligible', .90), 'b-x': ('provisional', .999), 'c-x': ('eligible', .96),
                                         'd-x': ('eligible', .96), 'e-x': ('eligible', .94)})
    assert escalation._start('tiered', 'deep', ladder, rows, family, TIERING)[:2] == ('c-x', 'TIERED_START')
    ladder, rows, family = fixture_rows({'a-x': ('provisional', .9), 'b-x': ('provisional', .8)})
    assert escalation._start('tiered', 'deep', ladder, rows, family, TIERING)[0] == 'a-x'  # nothing eligible: cheapest


def test_simple_is_the_cheapest_in_tiered_mode_and_other_modes_have_no_tiered_start():
    ladder, rows, family = fixture_rows({'a-x': ('eligible', .9), 'b-x': ('eligible', .99)})
    assert escalation._start('tiered', 'simple', ladder, rows, family, TIERING) == ('a-x', None, None)
    assert escalation._start('economical', 'deep', ladder, rows, family, None) == ('a-x', None, None)


# --- the standard split ----------------------------------------------------------------------------------------
@pytest.mark.parametrize('share', [0.10, 0.20, 0.25])
def test_the_arm_share_is_honoured_over_10000_scopes(share):
    low = sum(escalation.arm_of(scope(i), share) == 'low' for i in range(10_000))
    assert abs(low / 10_000 - share) <= 0.01


def test_the_arm_is_deterministic_per_scope():
    assert {escalation.arm_of(scope(7), 0.2) for _ in range(5)} == {escalation.arm_of(scope(7), 0.2)}
    assert escalation.arm_of(scope(7), 0.1) in {'low', 'mid'}


def test_a_higher_share_only_moves_scopes_from_mid_to_low():
    assert all(escalation.arm_of(scope(i), 0.25) == 'low' for i in range(2000) if escalation.arm_of(scope(i), 0.10) == 'low')


@pytest.mark.parametrize('raw,used,clamped', [
    (None, 0.20, False), (0.15, 0.15, False), (0.9, 0.25, True), (0.01, 0.10, True), (1, 0.25, True),
    ('0.2', 0.20, False), (True, 0.20, False), (float('nan'), 0.20, False)])
def test_the_share_is_clamped_to_its_range(raw, used, clamped):
    share = escalation.share_of({} if raw is None else {'standard_low_share': raw})
    assert (share['used'], share['clamped']) == (used, clamped)


def test_the_clamp_is_traced():
    _, result = run(settings={**TIERED, 'standard_low_share': 0.9})
    assert moved(result)['standard_low_share'] == {'requested': 0.9, 'used': 0.25, 'clamped': True}


# --- routing ----------------------------------------------------------------------------------------------------
def test_standard_low_arm_stays_on_the_cheapest_head():
    _, result = run(scope_id=find_scope('low'))
    assert head(result) == 'luna-default' and moved(result)['arm'] == 'low' and moved(result)['reason'] is None


def test_standard_mid_arm_starts_at_the_cheapest_eligible_head_of_another_model():
    _, result = run(scope_id=find_scope('mid'))
    assert head(result) == 'sol-default' and moved(result)['arm'] == 'mid' and moved(result)['reason'] == 'TIERED_START'
    assert result['trace']['selection']['tier'] == 'eligible'
    assert result['config']['model_name'] == 'global.openai.gpt-5.6-sol'  # measured on this table: Sol default is cheaper than Terra at standard


def test_the_arm_follows_the_share_setting():
    scope_id = find_scope('low', 0.25)
    mid_at_ten = next(scope(i) for i in range(10_000) if escalation.arm_of(scope(i), 0.10) == 'mid'
                      and escalation.arm_of(scope(i), 0.25) == 'low')
    assert moved(run(scope_id=mid_at_ten, settings={**TIERED, 'standard_low_share': 0.10})[1])['arm'] == 'mid'
    assert moved(run(scope_id=mid_at_ten, settings={**TIERED, 'standard_low_share': 0.25})[1])['arm'] == 'low'
    assert scope_id


def test_deep_starts_at_the_frontier_head():
    _, result = run(demand='deep')
    assert head(result) == 'sol-high' and moved(result)['reason'] == 'TIERED_START' and moved(result)['arm'] is None
    assert result['trace']['selection']['tier'] == 'eligible'


def test_simple_is_unchanged():
    _, result = run(demand='simple')
    assert head(result) == 'luna-default' and moved(result)['reason'] is None and moved(result)['arm'] is None


def test_other_modes_carry_no_tier_fields_and_unknown_modes_stay_economical():
    _, result = run(settings={'escalation_mode': 'economical'})
    assert 'arm' not in moved(result) and 'standard_low_share' not in moved(result)
    _, result = run(demand='deep', settings={'escalation_mode': 'tiered_typo'})
    assert head(result) == 'luna-default' and moved(result)['mode'] == 'economical'


# --- escalation from a tiered start ------------------------------------------------------------------------------
def follow(args, previous, scope_id, **fields):
    req = request('Please also add March.', invocation_id='b' * 64, scope_id=scope_id, **fields)
    req['messages'] = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a'}, req['messages'][0]]
    req['state_token'] = previous['state_token']
    return resolve(req, complete=classifier(demand='standard', relation='followup', reference_ids=['m0']), **args)


def test_a_signal_escalates_across_models_from_either_arm():
    low, mid = find_scope('low'), find_scope('mid')
    args, one = run(scope_id=low)
    two = follow(args, one, low, escalation={'event': 'regenerate'})
    assert (head(one), head(two)) == ('luna-default', 'sol-default') and 'luna-high' not in moved(two)['ladder']
    args, one = run(scope_id=mid)
    two = follow(args, one, mid, escalation={'event': 'regenerate'})
    assert (head(one), head(two), moved(two)['arm']) == ('sol-default', 'terra-default', 'mid')


def test_the_arm_and_the_head_are_stable_across_turns_of_a_scope():
    mid = find_scope('mid')
    args, one = run(scope_id=mid)
    two = follow(args, one, mid)
    assert (head(two), moved(two)['arm']) == ('sol-default', 'mid')


def test_the_guard_records_against_the_tiered_start():
    mid = find_scope('mid')
    args, one = run(scope_id=mid)
    req = {'pin': one['pin'], 'scope_id': mid, 'invocation_id': 'a' * 64, 'answer_text': 'a',
           'request_text_projection': {}, 'last_response': {'refusal': True}}
    result = guard.guard(req, project_id=7, user_id=2, settings=args['settings'], models=args['models'],
                         price_snapshot=args['price_snapshot'], signing_key=args['signing_key'],
                         complete=lambda *a, **k: pytest.fail('hard signal'), now=args['now'])
    assert (result['verdict'], result['code']) == ('fail', 'refusal') and 'escalate_to' not in result
    assert result['trace']['escalation']['arm'] == 'mid' and result['trace']['escalation']['next'] == 'terra-default'
    assert guard.usage_record(7, mid, result, now=1)['arm'] == 'mid'


def test_the_usage_record_carries_the_arm():
    mid = find_scope('mid')
    _, one = run(scope_id=mid)
    assert escalation.usage_record(7, request(scope_id=mid), one, now=1)['arm'] == 'mid'


# --- tiered is the default (plan section 13.5); economical stays selectable ---------------------------------------
def absent(demand, scope_id=None):
    args = args_for(*ALL)
    del args['settings']['escalation_mode']
    req = request('Total the invoices.', **({'scope_id': scope_id} if scope_id else {}))
    return resolve(req, complete=classifier(demand=demand), **args)


def test_an_absent_escalation_mode_is_tiered():
    assert escalation.mode_of({}) == 'tiered' and escalation.mode_of({'escalation_mode': None}) == 'tiered'
    deep, simple = absent('deep'), absent('simple')
    assert (head(deep), moved(deep)['mode'], moved(deep)['reason']) == ('sol-high', 'tiered', 'TIERED_START')
    assert (head(simple), moved(simple)['mode']) == ('luna-default', 'tiered')
    mid, low = absent('standard', find_scope('mid')), absent('standard', find_scope('low'))
    assert (head(mid), moved(mid)['arm'], head(low), moved(low)['arm']) == ('sol-default', 'mid', 'luna-default', 'low')
    assert moved(low)['standard_low_share'] == {'requested': None, 'used': 0.20, 'clamped': False}


def test_economical_stays_selectable_and_an_unknown_value_is_economical():
    assert escalation.mode_of({'escalation_mode': 'economical'}) == 'economical'
    assert escalation.mode_of({'escalation_mode': 'typo'}) == 'economical'
    _, deep = run(demand='deep', settings={'escalation_mode': 'economical'})
    assert (head(deep), moved(deep)['mode']) == ('luna-default', 'economical')
