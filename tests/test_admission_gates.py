"""#6722: allowance (p90), transport (provisional), context-window and pricing gates of the admission policy."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import args_for, classifier, request
from routing import service
from routing.service import RoutingUnavailable, resolve
from routing.v7 import admission
from routing.v7.catalog import compile_catalog

LUNA = 'openai/gpt-5-6-luna|default'
TABLE = admission.load()


def p90(head, demand):
    return TABLE['heads'][head]['cells'][demand]['expected_output_tokens_p90']


def go(keys, demand='standard', cap=None, preset=None, **kwargs):
    args = args_for(*keys, **kwargs.pop('inventory', {}))
    req = request(**kwargs.pop('request', {}))
    if cap is not None:
        req['output_cap'] = cap
    if preset:
        req['selection']['reasoning'] = {'mode': 'explicit', 'preset': preset}
    return resolve(req, complete=classifier(demand=demand, **kwargs), **args)


# --- output allowance: effective cap against the cell's observed p90 ---------------------------------------------------
def test_provider_default_passes_the_p90_check_and_is_traced():
    info = go(('luna',), demand='standard')['trace']['selection']['admission']
    assert info['calibration_allowance'] == 'provider_default' and info['cap_basis'] == 'provider_default'
    assert info['effective_cap'] == 64000 and info['cell_p90_output'] == p90(LUNA, 'standard')
    assert info['allowance_rejected'] == [] and 'CALIBRATION_ALLOWANCE_BELOW_P90' not in info['flags']


def test_the_provider_default_is_the_models_own_maximum_and_can_fail_the_p90_check():
    small = {'luna': {'max_output_tokens': p90(LUNA, 'standard') - 1}}
    result = go(('luna', 'gpt54'), demand='standard', inventory=small)
    info = result['trace']['selection']['admission']
    assert 'luna-default' in info['allowance_rejected']  # rejected on its own provider maximum
    assert result['trace']['selection']['variant'] == 'gpt54-medium'


@pytest.mark.parametrize('delta,passes', [(0, True), (1, True), (-1, False)])
def test_explicit_cap_is_compared_with_the_cell_p90_at_the_boundary(delta, passes):
    cap = p90(LUNA, 'standard') + delta
    result = go(('luna', 'gpt54'), demand='standard', cap=cap)
    selection = result['trace']['selection']
    if passes:
        assert selection['variant'] == 'luna-default'
        assert selection['admission']['calibration_allowance'] == 'explicit'
        assert selection['admission']['effective_cap'] == cap
        assert result['config']['max_tokens'] == cap
    else:
        assert selection['variant'] == 'gpt54-medium'
        assert 'luna-default' in selection['admission']['allowance_rejected']


def test_small_explicit_cap_leaves_nothing_to_route_to_without_a_baseline():
    with pytest.raises(ValueError, match='No eligible configured model'):
        go(('luna', 'terra'), demand='standard', cap=256)


def test_candidate_row_carries_the_flag():
    from routing.v7.candidate import CalibratedRouter
    from admission_support import descriptor
    router = CalibratedRouter(object(), service.CONFIGURED_CLASSIFIER, catalog=compile_catalog())

    class Gateway:
        output_caps = {'luna-default': 300}
        cap_basis = 'explicit'
        tier_caps = {}
    router.gateway = Gateway()
    chosen = router.select(descriptor('simple'), allowed=['luna-default', 'gpt54-medium'])
    row = next(r for r in chosen['candidates'] if r['variant'] == 'luna-default')
    assert row['rejections'] == ['CALIBRATION_ALLOWANCE_BELOW_P90'] and row['flags'] == ['CALIBRATION_ALLOWANCE_BELOW_P90']
    assert row['expected_output_p90'] == p90(LUNA, 'simple') and row['effective_cap'] == 300


# --- transport -----------------------------------------------------------------------------------------------------------
def test_native_anthropic_head_is_eligible_at_simple_with_the_measured_transport():
    result = go(('opus55',), demand='simple')
    # Both Opus 5.5 heads have a simple row; the high head's shorter observed answers rank it cheaper.
    assert result['trace']['selection']['variant'] == 'opus-5-5-high'
    assert result['trace']['selection']['tier'] == 'eligible'
    assert result['config']['routing_transport'] == 'anthropic_messages'
    assert result['trace']['inventory']['provisional_contracts'] == {}


def test_anthropic_behind_an_openai_compatible_transport_is_provisional_with_the_flag_in_the_trace():
    result = go(('opus55',), demand='simple', inventory={'opus55': {'openai_compatible': True}})
    selection = result['trace']['selection']
    assert selection['variant'] == 'opus-5-5-high'
    assert (selection['tier'], selection['provisional']) == ('provisional', True)
    assert selection['admission']['flags'] == ['CALIBRATION_TRANSPORT_UNMEASURED']
    assert set(result['trace']['inventory']['provisional_contracts']) == {'opus-5-5-default', 'opus-5-5-high'}
    assert result['trace']['inventory']['provisional_contracts']['opus-5-5-high'] == {
        'flags': ['CALIBRATION_TRANSPORT_UNMEASURED'], 'deployment': 'global.anthropic.claude-opus-5-5',
        'configured_transport': 'chat_completions', 'measured_transport': 'anthropic_messages'}
    # No native envelope is pinned for a transport that was never measured.
    assert 'routing_transport' not in result['config'] and 'routing_reasoning_fields' not in result['config']


def test_provisional_transport_cannot_be_preferred_over_an_eligible_cheaper_head():
    # Luna is eligible and cheaper; the DIAL Anthropic head stays an alternative at provisional tier.
    result = go(('luna', 'opus55'), demand='simple', inventory={'opus55': {'openai_compatible': True}})
    assert result['trace']['selection']['variant'] == 'luna-default'
    assert result['trace']['selection']['provisional'] is False


def test_openai_models_are_never_flagged_for_transport():
    assert go(('terra',), demand='deep')['trace']['inventory']['provisional_contracts'] == {}


# --- reasoning ---------------------------------------------------------------------------------------------------------------
def test_explicit_effort_still_needs_reasoning_field_evidence():
    with pytest.raises(RoutingUnavailable):  # opus-5-5-high carries no measured reasoning fields
        go(('opus55',), demand='deep', preset='high')
    result = go(('luna',), demand='standard', preset='high')  # luna-high has no contract to contradict
    assert result['trace']['selection']['variant'] == 'luna-high'
    assert result['config']['reasoning_effort'] == 'high'


# --- context window ----------------------------------------------------------------------------------------------------------
def test_context_window_gate_uses_the_provider_default_when_no_cap_is_given():
    tight = {'luna': {'context_window': 8000, 'max_output_tokens': 128000}}
    with pytest.raises(RoutingUnavailable):
        go(('luna',), demand='simple', inventory=tight)


def test_context_window_gate_uses_the_explicit_cap_when_given():
    tight = {'luna': {'context_window': 8000, 'max_output_tokens': 128000}}
    result = go(('luna',), demand='simple', cap=1000, inventory=tight)
    assert result['config']['max_tokens'] == 1000 and result['config']['context_window'] == 8000
    with pytest.raises(RoutingUnavailable):
        go(('luna',), demand='simple', cap=8000, inventory=tight)


# --- economics ---------------------------------------------------------------------------------------------------------------
def test_ranking_prices_the_cell_p90_never_the_provider_maximum():
    def quotes(max_output):
        inventory = {k: {'max_output_tokens': max_output} for k in ('luna', 'terra', 'sol')}
        result = go(('luna', 'terra', 'sol'), demand='standard', inventory=inventory)
        return result['trace']['selection']['economics']
    small, huge = quotes(64000), quotes(128000)
    for vid, head in {'luna-default': LUNA, 'terra-default': 'openai/gpt-5-6-terra|default',
                      'sol-default': 'openai/gpt-5-6-sol|default'}.items():
        quote = small['quotes'][vid]
        assert quote['output_allowance'] == p90(head, 'standard') and quote['output_basis'] == 'admission_table_p90'
        assert quote['ranking_usd'] == huge['quotes'][vid]['ranking_usd']
    assert small['reason'] == 'ADMISSION_P90_EXPENDITURE' and small['output_range'][1] == p90(LUNA, 'standard')


def test_a_cheaper_model_with_a_longer_expected_answer_can_lose():
    # Opus-5.5-high's deep answers average far more tokens than Terra's: a lower token price does not win.
    prices = {'opus55': ('.0000001', '.000004')}  # half Terra's output price per token
    result = go(('terra', 'opus55'), demand='deep', inventory={'prices': prices})
    quotes = result['trace']['selection']['economics']['quotes']
    assert float(quotes['opus-5-5-high']['ranking_usd']) > float(quotes['terra-default']['ranking_usd'])
    assert result['trace']['selection']['variant'] == 'terra-default'


def test_expected_output_falls_back_to_the_cap_when_the_table_has_no_p90(monkeypatch):
    catalog = compile_catalog()
    for cells in catalog['admission']['heads'].values():
        for cell in cells['cells'].values():
            cell['expected_output_tokens_p90'] = None
    router = service.GenerationRouter(service.ClassifierTransport(), service.CONFIGURED_CLASSIFIER, catalog=catalog)
    monkeypatch.setattr(service, 'compiled_router', lambda: router)
    explicit = go(('luna',), demand='standard', cap=5000)
    quote = explicit['trace']['selection']['economics']['quotes']['luna-default']
    assert (quote['output_allowance'], quote['output_basis']) == (5000, 'explicit_cap')
    default = go(('luna',), demand='standard')
    quote = default['trace']['selection']['economics']['quotes']['luna-default']
    assert (quote['output_allowance'], quote['output_basis']) == (64000, 'max_output_tokens')


def test_unpriced_admitted_model_is_not_admitted():
    args = args_for('luna', 'terra')
    args['price_snapshot']['entries'] = [e for e in args['price_snapshot']['entries']
                                         if e['model_name'] != 'global.openai.gpt-5.6-luna']
    result = resolve(request(), complete=classifier(demand='standard'), **args)
    assert result['trace']['selection']['variant'] == 'terra-default'


def test_greeting_ranking_prices_the_simple_cell_not_the_provider_maximum():
    def economics(max_output):
        inventory = {k: {'max_output_tokens': max_output} for k in ('luna', 'gpt54', 'haiku')}
        args = args_for('luna', 'gpt54', 'haiku', **inventory)
        result = resolve(request('Hi'), complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
        return result['trace']['selection']
    small, huge = economics(8192), economics(128000)
    assert small['variant'] == huge['variant'] == 'luna-default'
    for vid in ('luna-default', 'haiku-default'):  # heads with a simple row
        assert small['economics']['quotes'][vid] == huge['economics']['quotes'][vid]
        assert small['economics']['quotes'][vid]['output_basis'] == 'admission_table_p90'
    # A head without a row falls back to the model maximum, which makes it look dearer, not cheaper.
    assert small['economics']['quotes']['gpt54-medium']['output_basis'] == 'max_output_tokens'
