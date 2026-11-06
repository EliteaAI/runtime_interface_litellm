"""#6826 round (vii): the table of record and the selection rule of record (rules 1-6, WAVE-ADOPTION-20261106)."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import args_for, classifier, descriptor, request
from routing import escalation
from routing.service import RoutingUnavailable, resolve
from routing.v7 import admission
from routing.v7.catalog import compile_catalog

V7 = Path(__file__).resolve().parents[1] / 'routing' / 'v7'
VII = V7 / 'admission-table-material-r2-vii-3of4-ref30-limit-ai.json'
VII_SHA256 = '09a9bcd29f7b2f8590126c9fc27b46b087801d7e89e93e488dd76129d6e4ba00'
MATERIAL_R2 = V7 / 'admission-table.json'  # the previous table of record, kept beside it as history
MATERIAL_R2_SHA256 = '0d2320a526469f6cf12ad4313148b250bfe75b06e23cbb0890273491305e4692'
CUSTOMER_PLUS_FIVE = ('sonnet5', 'sonnet46', 'mini', 'luna6', 'haiku55', 'sol61', 'gemini31', 'gemini38')


def route(keys, demand, mode=None, **changes):
    args = args_for(*keys)
    args['settings'] = {k: v for k, v in args['settings'].items() if k != 'escalation_mode'}
    if mode is not None:
        args['settings']['escalation_mode'] = mode
    return resolve(request(), complete=classifier(demand=demand, **changes), **args)


def head(result):
    return result['trace']['selection']['variant']


def moved(result):
    return result['trace']['escalation']


def rows_of(heads, demand):
    """Chooser rows built from the shipped cells (admission._summary), keyed by head."""
    table = admission.load()
    return {h: {'tier': table['heads'][h]['cells'][demand]['tier'],
                'cell': admission._summary(table['heads'][h]['cells'][demand])} for h in heads}


# --- the table pin --------------------------------------------------------------------------------------------------
def test_the_vii_table_is_pinned_and_served():
    raw = VII.read_bytes()
    table = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == VII_SHA256
    assert len(table['heads']) == 21
    assert table['rule_revision'] == 'interim-reviewed-demand-1+ac2+ref30+limit_oracle'
    assert admission.TABLE_PATH == VII and admission.load()['sha256'] == VII_SHA256
    assert compile_catalog()['admission']['sha256'] == VII_SHA256
    # The material-r2 table stays beside it, unchanged, for the history of the pins.
    assert hashlib.sha256(MATERIAL_R2.read_bytes()).hexdigest() == MATERIAL_R2_SHA256


def test_table_heads_get_distinct_variant_ids():
    # gpt-6.1-sol and gpt-5.6-sol both shorten to `sol`: the new head takes its full id, the old one keeps its own.
    variants = compile_catalog()['variants']
    assert variants['sol-default']['canonical_model'] == 'openai/gpt-5-6-sol'
    assert variants['gpt-6-1-sol-default']['canonical_model'] == 'openai/gpt-6-1-sol'
    assert variants['gpt-6-luna-default']['canonical_model'] == 'openai/gpt-6-luna'
    with pytest.raises(ValueError, match='no free variant id'):
        admission._variant_id('openai/gpt-6-1-sol', None, {'sol-default', 'gpt-6-1-sol-default'})


# --- rule 1: floor_only only under an explicit `economical`; `out` never ------------------------------------------
def test_floor_only_is_hidden_unless_the_project_chose_economical():
    # gpt-5.6-sol default and high are floor_only at standard (21/21, no reference head shares 30 items). In any
    # other mode nothing is admitted, so the deployment runs only as #7307's unmeasured last resort (no tier).
    for mode in (None, 'tiered', 'premium_at_deep', 'premium_from_standard', 'unknown-mode'):
        selection = route(('sol',), 'standard', mode)['trace']['selection']
        assert (selection['reason'], selection['tier']) == ('UNMEASURED_CHEAPEST', None), mode
        assert selection['admission']['status'] == 'unmeasured_installation', mode
    result = route(('sol',), 'standard', 'economical')
    assert result['trace']['selection']['tier'] == 'floor_only'
    assert result['trace']['selection']['admission']['status'] == 'admitted'
    assert result['trace']['selection']['admission']['cell']['status'] == 'floor_only'


def test_an_unknown_mode_starts_as_economical_but_never_admits_floor_only():
    result = route(('luna', 'sol'), 'standard', 'unknown-mode')
    assert moved(result)['mode'] == 'economical' and head(result) == 'luna-default'
    assert not {'sol-default', 'sol-high'} & set(result['trace']['selection']['economics']['quotes'])
    economical = route(('luna', 'sol'), 'standard', 'economical')
    assert {'sol-default', 'sol-high'} <= set(economical['trace']['selection']['economics']['quotes'])


def test_an_out_cell_is_never_a_candidate_even_under_economical():
    catalog = compile_catalog()
    chosen = admission.choose_admitted(descriptor('deep', effort_need='low'), None, None, 'simple', catalog,
                                       context={'floor_only': True})
    rows = {r['variant']: r for r in chosen['candidates']}
    assert rows['sol-default']['rejections'] == ['ADMISSION_ROW_OUT'] and rows['sol-default']['tier'] == 'out'
    assert rows['opus-5-5-high']['tier'] == 'out' and not rows['opus-5-5-high']['eligible']  # out at deep (vii)
    assert all(r['tier'] in {'eligible', 'provisional', 'floor_only'} for r in chosen['candidates'] if r['eligible'])


def test_a_default_mode_never_admits_floor_only_through_the_unmeasured_path():
    # Rule 6: nothing admitted and no baseline is #7307's last resort; a floor_only head there is an unmeasured
    # route like any out head (provisional, guard-sampled), never an admitted floor_only tier.
    selection = route(('sol', 'haiku'), 'standard')['trace']['selection']
    assert selection['reason'] == 'UNMEASURED_CHEAPEST' and selection['tier'] is None
    assert selection['provisional'] is True and selection['guard_sample'] is True
    assert selection['admission']['status'] == 'unmeasured_installation'


# --- rules 3-4: premium = highest lb90 among cross-provider-confirmed, panel-labelled admitted cells -------------------
def test_premium_ignores_single_judge_cells():
    # Anthropic simple cells (REPORT-vii AI.3): Sonnet 5.5 (0.985) and Opus 5.5 high (0.981) are single_judge, so
    # the premium head is Opus 5.5 default (0.974, panel).
    heads = ['anthropic/claude-sonnet-5-5|default', 'anthropic/claude-opus-5-5|high', 'anthropic/claude-opus-5-5|default']
    rows = rows_of(heads, 'simple')
    assert [rows[h]['cell']['label_regime'] for h in heads] == ['single_judge', 'single_judge', 'panel']
    assert escalation._premium(heads, rows) == ('anthropic/claude-opus-5-5|default', 'lb90')


def test_premium_ignores_unconfirmed_cells():
    # sonnet-5 high is eligible at simple but not cross-provider confirmed; gpt-5.4 (lower lb90) is.
    heads = ['anthropic/claude-sonnet-5|high', 'openai/gpt-5-4|default']
    rows = rows_of(heads, 'simple')
    assert rows[heads[0]]['cell']['cross_provider_confirmed'] is False
    assert escalation._premium(heads, rows) == ('openai/gpt-5-4|default', 'lb90')
    assert escalation._premium(heads[:1], rows) == (None, None)


def tied(**cells):
    return {v: {'tier': 'eligible', 'cell': {'n': 63, 'pass': 63, 'lb90': 0.97884, 'adverse': 0, 'paired_n': 63,
                                              'cross_provider_confirmed': True, 'label_regime': 'panel', **c}}
            for v, c in cells.items()}


def test_premium_tiebreak_order_on_an_exact_lb90_tie():
    ladder = ['a', 'b', 'c']
    rows = tied(a={'adverse': 1}, b={}, c={})
    # a is out on the adverse rate although it is the cheapest; b beats c on list price.
    assert escalation._premium(ladder, rows, {'a': 1, 'b': 2, 'c': 3}) == ('b', 'list_price')
    assert escalation._premium(['a', 'b'], rows, {'a': 1, 'b': 3}) == ('b', 'paired_adverse_rate')
    assert escalation._premium(['b', 'c'], rows, {'b': 2, 'c': 2}, {'b': 'z|default', 'c': 'y|default'}) == (
        'c', 'canonical_order')
    # lb90 is compared at full precision from the counts, not as the rounded table value.
    rows = tied(a={'pass': 62, 'lb90': 0.97884}, b={})
    assert escalation._premium(['a', 'b'], rows) == ('b', 'lb90')


def test_deep_premium_is_gpt_6_luna_by_list_price_over_gpt_6_1_sol():
    # REPORT-vii AI.3: both 63/63 at deep, both 0/63 adverse; gpt-6-luna has the cheaper list price.
    result = route(CUSTOMER_PLUS_FIVE, 'deep', 'premium_at_deep')
    assert head(result) == 'gpt-6-luna-default'
    assert moved(result)['premium'] == {'head': 'gpt-6-luna-default', 'premium_tiebreak': 'list_price', 'code': None}


def test_standard_premium_is_gpt_6_1_sol_and_economical_starts_on_gpt_6_luna():
    premium = route(CUSTOMER_PLUS_FIVE, 'standard', 'premium_from_standard')
    assert head(premium) == 'gpt-6-1-sol-default' and moved(premium)['reason'] == 'PREMIUM_START'
    assert moved(premium)['premium'] == {'head': 'gpt-6-1-sol-default', 'premium_tiebreak': 'lb90', 'code': None}
    for demand in ('simple', 'standard', 'deep'):
        assert head(route(CUSTOMER_PLUS_FIVE, demand, 'economical')) == 'gpt-6-luna-default', demand


def test_premium_is_inactive_without_a_confirmed_panel_cell_and_the_trace_says_so():
    # Anthropic-only installation: nothing is admitted at deep, the baseline runs and premium is inactive.
    result = route(('sonnet55', 'opus55', 'gpt54'), 'deep', 'premium_at_deep')
    assert result['trace']['selection']['reason'] == 'UNCERTAIN_TASK_BASELINE'
    assert moved(result)['premium'] == {'head': None, 'premium_tiebreak': None, 'code': 'PREMIUM_INACTIVE'}
    # Simple is not a premium demand: no premium record.
    assert 'premium' not in moved(route(('sonnet55', 'opus55'), 'simple', 'premium_at_deep'))


def test_marginal_is_traced_and_changes_nothing():
    # luna-default at standard is marginal (one item moves the tier); it is still the cheapest admitted head.
    result = route(('luna', 'terra'), 'standard', 'economical')
    assert head(result) == 'luna-default'
    assert result['trace']['selection']['admission']['cell']['marginal'] is True


# --- rule 5: default mode ------------------------------------------------------------------------------------------
def test_the_default_mode_is_tiered():
    result = route(CUSTOMER_PLUS_FIVE, 'deep')
    assert moved(result)['mode'] == 'tiered' and 'premium' not in moved(result)
    # tiered deep starts on the frontier (highest-lb90 eligible head; the tie goes to the cheaper).
    assert head(result) == 'gpt-6-luna-default'
