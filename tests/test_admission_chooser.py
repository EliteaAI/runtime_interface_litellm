"""#6722: the chooser admits by the data-derived table, ranks admitted variants by expenditure and flags tiers."""
import copy
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import args_for, classifier, descriptor, request
from routing import service
from routing.service import CONFIGURED_CLASSIFIER, GenerationRouter, RoutingUnavailable, decode_pin, resolve
from routing.v7 import admission
from routing.v7.candidate import CalibratedRouter
from routing.v7.catalog import compile_catalog

ALL = ('luna', 'terra', 'sol', 'sonnet55', 'opus55', 'gpt54')


def run(keys=ALL, demand='standard', prompt='Analyze the supplied figures.', **changes):
    args = args_for(*keys, **changes.pop('inventory', {}))
    req = request(prompt, **changes.pop('request', {}))
    return resolve(req, complete=classifier(demand=demand, **changes), **args)


def selected(result):
    trace = result['trace']['selection']
    return trace['variant'], trace['tier'], trace['provisional']


# --- admission by the table: simple, standard ---------------------------------------------------------------------
@pytest.mark.parametrize('demand', ['simple', 'standard'])
def test_simple_and_standard_work_goes_to_luna_default(demand):
    result = run(demand=demand)
    assert selected(result) == ('luna-default', 'eligible', False)
    assert result['config']['model_name'] == 'global.openai.gpt-5.6-luna'
    info = result['trace']['selection']['admission']
    assert info['demand'] == demand and info['status'] == 'admitted' and info['cell']['pass'] / info['cell']['n'] > 0.98


def test_the_old_task_ceiling_tables_no_longer_decide_product_routing():
    catalog = compile_catalog()
    # luna-default's legacy cohort says "max_demand: standard" and design is not one of its tasks ...
    assert catalog['variants']['luna-default']['max_demand'] == 'standard'
    assert 'design' not in catalog['variants']['luna-default']['tasks']
    # ... yet a design request of standard demand is admitted by the table alone.
    result = run(keys=('luna',), demand='standard', operation='design', task_family='architecture')
    assert selected(result) == ('luna-default', 'eligible', False)


def test_every_current_variant_with_a_simple_row_is_a_candidate():
    result = run(demand='simple')
    quotes = result['trace']['selection']['economics']['quotes']
    assert {'luna-default', 'terra-default', 'sol-default', 'sol-high'} <= set(quotes)


# --- admission by the table: deep -------------------------------------------------------------------------------
def test_deep_work_goes_to_the_cheapest_provisional_head_and_says_so():
    result = run(demand='deep')
    assert selected(result) == ('terra-default', 'provisional', True)
    assert result['trace']['selection']['admission']['tier'] == 'provisional'
    quotes = result['trace']['selection']['economics']['quotes']
    assert set(quotes) == {'terra-default', 'sol-high', 'sonnet-5-5-default', 'opus-5-5-high'}  # luna is out at deep


def test_an_eligible_head_that_is_cheaper_wins_without_the_provisional_flag():
    prices = {'sol': ('.0000001', '.0000001')}
    assert selected(run(demand='deep', inventory={'prices': prices})) == ('sol-high', 'eligible', False)


def test_with_terra_gated_the_next_cheapest_admitted_head_is_chosen():
    result = run(keys=('luna', 'sonnet55', 'opus55', 'gpt54'), demand='deep')
    assert selected(result) == ('sonnet-5-5-default', 'provisional', True)  # a DIAL-less native Anthropic head
    assert result['config']['routing_transport'] == 'anthropic_messages'


def test_when_nothing_clears_the_deep_floor_the_baseline_is_used():
    result = run(keys=('luna', 'gpt54', 'haiku'), demand='deep')
    selection = result['trace']['selection']
    assert selection['variant'] == 'gpt54-medium' and selection['reason'] == 'UNCERTAIN_TASK_BASELINE'
    assert selection['tier'] is None and selection['provisional'] is False
    assert selection['admission']['status'] == 'no_variant_clears_floor'
    assert selection['family_qualification']['status'] == 'insufficient_evidence_baseline'


def test_the_baseline_honours_the_effort_need_filter():
    result = run(keys=('luna', 'gpt54'), demand='deep', effort_need='high')
    assert result['trace']['selection']['variant'] == 'gpt54-high'


def test_no_baseline_and_nothing_admitted_is_a_refusal_not_a_guess():
    with pytest.raises(ValueError, match='No eligible configured model'):
        run(keys=('luna', 'haiku'), demand='deep')


# --- uncertain descriptors ---------------------------------------------------------------------------------------
UNCERTAIN = {
    'needs_context': dict(needs_context=True, input_status='missing'),
    'operation_other': dict(operation='other'),
    'family_unknown': dict(task_family='unknown'),
}


@pytest.mark.parametrize('name', sorted(UNCERTAIN))
def test_uncertain_descriptor_goes_to_the_cheapest_variant_clearing_the_deep_floor(name):
    result = run(demand='simple', **UNCERTAIN[name])
    selection = result['trace']['selection']
    assert (selection['variant'], selection['reason']) == ('terra-default', 'UNCERTAIN_CHEAPEST_CLEARING_FLOOR')
    assert selection['admission']['demand'] == 'deep' and selection['provisional'] is True


def test_ambiguous_relation_and_clarify_paths_also_pick_the_cheapest_clearing_variant():
    result = run(demand='simple', relation='ambiguous', needs_context=True, input_status='ambiguous')
    selection = result['trace']['selection']
    assert (selection['variant'], selection['reason']) == ('terra-default', 'UNCERTAIN_CHEAPEST_CLEARING_FLOOR')
    assert result['trace']['uncertainty']['handling'] == 'generation_model'
    # The converted clarify path is ranked by expenditure too: Sol would win if it were cheaper.
    cheap_sol = run(demand='simple', relation='ambiguous', needs_context=True, input_status='ambiguous',
                    inventory={'prices': {'sol': ('.0000001', '.0000001')}})
    assert cheap_sol['trace']['selection']['variant'] == 'sol-high'
    assert cheap_sol['trace']['selection']['provisional'] is False


def test_an_unparseable_classifier_reply_is_uncertain_not_a_cheap_route():
    args = args_for(*ALL)
    result = resolve(request(), complete=classifier(raw='not json'), **args)
    assert result['trace']['classifier']['schema_valid'] is False
    assert result['trace']['selection']['reason'] == 'UNCERTAIN_CHEAPEST_CLEARING_FLOOR'
    assert result['trace']['selection']['variant'] == 'terra-default'


def test_uncertain_with_nothing_clearing_the_floor_keeps_the_baseline():
    result = run(keys=('luna', 'gpt54'), demand='simple', needs_context=True, input_status='missing')
    assert result['trace']['selection']['reason'] == 'UNCERTAIN_TASK_BASELINE'
    assert result['trace']['selection']['variant'] == 'gpt54-medium'


# --- rules that are kept -------------------------------------------------------------------------------------------
def test_greeting_rule_is_unchanged_and_makes_no_classifier_call():
    args = args_for(*ALL)
    result = resolve(request('Hi'), complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
    selection = result['trace']['selection']
    assert result['trace']['descriptor']['operation'] == 'greeting'
    assert result['trace']['classifier']['called'] is False
    assert selection['tier'] is None and selection['family_qualification']['status'] == 'existing_exact_greeting_rule'
    assert selection['variant'] == 'luna-default'


def test_effort_need_filter_still_applies_to_effort_variants():
    # sol-high is the only admitted effort variant at deep; a low need keeps it, the filter never removes
    # variants whose effort is at least the need.
    result = run(keys=('sol',), demand='deep', effort_need='high')
    assert result['trace']['selection']['variant'] == 'sol-high'


@pytest.fixture
def lab_router():
    return GenerationRouter(object(), CONFIGURED_CLASSIFIER, catalog=compile_catalog())


def test_explicit_binding_path_is_unchanged(lab_router):
    decision = lab_router.resolve([{'role': 'user', 'content': 'x'}], binding={'mode': 'fixed', 'variant': 'sol-high'})
    assert decision['selection']['reason'] == 'EXPLICIT_BINDING' and decision['selection']['variant'] == 'sol-high'


def test_pinned_scope_path_is_unchanged(lab_router):
    class Scope:
        id = 's'
        lock = threading.Lock()
        classifications = 0
        decision = {'selection': {'variant': 'luna-default', 'reason': 'ADMISSION_TABLE_COST_BAND'},
                    'descriptor': descriptor(), 'budget': {'completion_cap': None}}
    decision = lab_router.resolve([{'role': 'user', 'content': 'x'}], scope=Scope())
    assert decision['selection']['reason'] == 'INHERITED_SCOPE_PIN' and decision['classifier_reused'] is True


# --- table lookups ---------------------------------------------------------------------------------------------------
@pytest.fixture
def table():
    return admission.load()


def test_a_head_or_demand_without_a_row_is_out(table):
    assert admission.tier_of(table, 'openai/gpt-5-4|medium', 'deep') == 'out'
    assert admission.tier_of(table, 'vendor/never-measured|default', 'simple') == 'out'
    assert admission.tier_of(table, admission.head_key('openai/gpt-5-6-luna', None), 'simple') == 'eligible'


def test_candidate_rows_record_why_a_variant_was_not_admitted():
    router = CalibratedRouter(object(), CONFIGURED_CLASSIFIER, catalog=compile_catalog())
    chosen = router.select({**descriptor('deep'), 'effort_need': 'low'})
    rows = {r['variant']: r for r in chosen['candidates']}
    assert rows['luna-default']['rejections'] == ['ADMISSION_ROW_OUT']
    assert rows['gpt54-medium']['rejections'] == ['ADMISSION_NO_ROW']
    assert rows['terra-default']['tier'] == 'provisional' and rows['terra-default']['eligible']


def test_family_refinement_overrides_the_pooled_row(table):
    catalog = compile_catalog()
    heads = catalog['admission']['heads']
    heads['openai/gpt-5-6-terra|default'].setdefault('families', {})['rca'] = {
        'deep': {**heads['openai/gpt-5-6-terra|default']['cells']['deep'], 'tier': 'out'}}
    router = CalibratedRouter(object(), CONFIGURED_CLASSIFIER, catalog=catalog)
    pool = ['terra-default', 'sol-high']
    other = router.select(descriptor('deep', task_family='quantitative'), allowed=pool)
    refined = router.select(descriptor('deep', task_family='rca'), allowed=pool)
    assert other['variant'] == 'terra-default'
    assert refined['variant'] == 'sol-high'
    assert admission.tier_of(catalog['admission'], 'openai/gpt-5-6-terra|default', 'deep', 'rca') == 'out'
    assert admission.tier_of(catalog['admission'], 'openai/gpt-5-6-terra|default', 'deep', 'other') == 'provisional'


# --- policy revision and trace ------------------------------------------------------------------------------------------
def test_trace_names_the_table_and_rule_revision():
    result = run(demand='standard')
    policy = result['trace']['policy']
    assert policy == {'admission_table_sha256': admission.load()['sha256'], 'rule_revision': admission.load()['rule_revision']}


def test_policy_revision_changes_when_the_table_changes():
    first = CalibratedRouter(object(), CONFIGURED_CLASSIFIER, catalog=compile_catalog())
    catalog = compile_catalog()
    catalog['admission']['sha256'] = '0' * 64  # any other table bytes
    second = CalibratedRouter(object(), CONFIGURED_CLASSIFIER, catalog=catalog)
    assert first.revision != second.revision
    assert admission.load()['sha256'][:12] in first.revision and '000000000000' in second.revision


def test_a_pin_issued_under_another_table_is_refused():
    args = args_for(*ALL)
    req = request()
    first = resolve(req, complete=classifier(demand='standard'), **args)
    body = first['pin'].split('.')[0]
    import base64
    from routing.service import encode_pin
    pin = json.loads(base64.urlsafe_b64decode(body + '=' * ((-len(body)) % 4)))
    pin['policy_revision'] += '-previous-table'
    req['prior_pin'] = encode_pin(pin, args['signing_key'])
    with pytest.raises(RoutingUnavailable, match='policy changed'):
        resolve(req, complete=lambda *a, **k: pytest.fail('No silent reroute'), **args)
    decode_pin(first['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=101)


def test_lab_catalogs_without_a_table_keep_the_frozen_cells():
    legacy = compile_catalog(admission=False)
    assert 'admission' not in legacy
    chosen = CalibratedRouter(object(), CONFIGURED_CLASSIFIER, catalog=legacy).select(
        descriptor('deep', task_family='architecture'), allowed=['gpt54-high', 'sol-default'])
    assert chosen['variant'] == 'gpt54-high' and chosen['family_qualification']['status'] == 'insufficient_evidence_baseline'
