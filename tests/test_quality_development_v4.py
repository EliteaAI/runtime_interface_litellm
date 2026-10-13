"""Synthetic fixtures prove policy mechanics, not population model quality."""
import copy

import pytest

from routing import service
from routing.quality import frontier, validate
from test_auto_routing_service import request
from test_quality_contract_v2 import SCOPE
from test_quality_workload_v3 import scoped_quality, v3_args, classify, certify
from test_v14_installed_bundle import installed
from test_v14_qualification import PROFILE


def development(q):
    q = copy.deepcopy(q)
    q.update(version=4, minimum_groups=8, minimum_observed_success=.95,
             maximum_observed_paired_adverse_fraction=.02)
    q.pop('minimum_success_lower_bound'); q.pop('maximum_paired_loss_upper_bound')
    for c in q['cohorts'].values():
        c['training_groups'] = c['groups'][:8]
    return q


def args_v4(installed, **kwargs):
    args = v3_args(installed, **kwargs)
    p = args['settings']['calibration_selection_policy']
    p['quality'] = development(p['quality'])
    return args


def test_eight_training_groups_support_development_but_not_release():
    q, variants = scoped_quality(); q = development(q)
    validate(q, variants)
    r = frontier(q, variants, 'standard', request_scope=SCOPE, work_profile=PROFILE)
    assert r['eligible'] == list(variants)
    assert r['training_groups'] == 8 and r['observed_groups'] == 1000
    assert r['evidence_stage'] == 'development' and r['release_qualified'] is False
    assert r['quality_guaranteed'] is False and 'success_lower_bound' not in r['assessments']['cheap']
    q['cohorts']['implementation']['training_groups'].pop()
    r = frontier(q, variants, 'standard', request_scope=SCOPE, work_profile=PROFILE)
    assert not r['eligible']
    assert r['assessments']['cheap']['status'] == 'insufficient_connected_training_groups'


@pytest.mark.parametrize('vote', ['fail','unknown'])
@pytest.mark.parametrize('index', [0,999])
def test_any_training_or_later_exact_negative_vetoes_despite_high_aggregate(vote,index):
    q, variants = scoped_quality(); q = development(q)
    q['cohorts']['implementation']['outcomes']['cheap'][index] = vote
    r = frontier(q, variants, 'standard', request_scope=SCOPE, work_profile=PROFILE)
    assert r['eligible'] == ['reference']
    assert r['assessments']['cheap']['status'] == 'adverse_or_unknown_exact_evidence'


@pytest.mark.parametrize('change', ['empty','duplicate','unknown','nonstrings','minimum','native','missing_source'])
def test_training_and_native_provenance_required(change):
    q, variants = scoped_quality(); q = development(q); c = q['cohorts']['implementation']
    if change == 'empty': c['training_groups'] = []
    elif change == 'duplicate': c['training_groups'] *= 2
    elif change == 'unknown': c['training_groups'].append('invented')
    elif change == 'nonstrings': c['training_groups'] = [True]
    elif change == 'minimum': q['minimum_groups'] = 7
    elif change == 'native': variants['cheap']['native']['effort'] = 'high'
    else: c.pop('source_sha256')
    with pytest.raises(ValueError): validate(q,variants)


@pytest.mark.parametrize('change', ['demand','profile','history','tools','turns','overlap'])
def test_evidence_cannot_transfer_or_pool_across_workload_envelopes(change):
    q, variants = scoped_quality(); q = development(q)
    profile = copy.deepcopy(PROFILE); scope = dict(SCOPE); demand = 'standard'
    if change == 'demand': demand = 'simple'
    elif change == 'profile': profile['verification'] = 'prove'
    elif change == 'history': scope['has_history'] = True
    elif change == 'tools': scope['has_tools'] = True
    elif change == 'turns': scope['user_turns'] = 2
    else: q['cohorts']['overlap'] = copy.deepcopy(q['cohorts']['implementation'])
    assert not frontier(q,variants,demand,request_scope=scope,work_profile=profile)['eligible']


def test_actual_gateway_uses_validated_total_usage_and_provider_default(installed,monkeypatch):
    from routing.v7 import cache_switching
    monkeypatch.setattr(cache_switching,'retained_variant',lambda *a,**kw:pytest.fail('Cache switching invoked'))
    args = args_v4(installed)
    result = service.resolve(request('Implement this contract.'),complete=classify,**args)
    selected = result['trace']['selection']
    assert result['config']['model_name'] == 'customer-large'
    assert result['config']['routing_output_mode'] == 'provider_default'
    assert selected['reason'] == 'MEASURED_TRAJECTORY_EXPENDITURE'
    assert selected['quality_screen']['release_qualified'] is False
    assert selected['economics']['forecast_comparable'] and selected['economics']['switching_evidence'] is None
    pin = service.decode_pin(result['pin'],args['signing_key'],project_id=7,user_id=2,settings=args['settings'],now=101)
    assert pin['config'] == result['config']


@pytest.mark.parametrize('change',['missing','bad_certificate','different_work','outside_range'])
def test_missing_competitor_forecast_cannot_make_a_false_economic_winner(installed,change):
    args = args_v4(installed); router = service.configured_router(args['settings'])
    c = router.catalog['variants']['configured-customer-small']['calibration_contract']
    f = c['usage_cohorts'][0]
    if change == 'missing': c['usage_cohorts'] = []
    elif change == 'bad_certificate': f['validation_certificate']['wape'] = .51
    elif change == 'different_work': f['work_profile']['work']='explain'; certify(f)
    else: f['input_bytes']['min']=999999; certify(f)
    r = service.resolve(request('Implement this contract.'),complete=classify,**args)
    assert r['config']['model_name'] == 'customer-workhorse'
    assert r['trace']['selection']['economics']['savings_claim'] is False


def test_cheaper_forecast_cannot_override_an_exact_negative(installed):
    args = args_v4(installed)
    q = args['settings']['calibration_selection_policy']['quality']
    q['cohorts']['implementation']['outcomes']['customer-large'][-1]='fail'
    r = service.resolve(request('Implement this contract.'),complete=classify,**args)
    assert r['config']['model_name']=='customer-small'


@pytest.mark.parametrize('overlap',[False,True])
def test_administrator_fallback_cannot_hide_same_native_negative(installed,overlap):
    args = args_v4(installed,forecasts=False)
    p = args['settings']['calibration_selection_policy']
    args['settings']['calibration_coverage_fallback_native']=copy.deepcopy(p['variants']['customer-large']['native'])
    p['quality']['cohorts']['implementation']['outcomes']['customer-large'][-1]='unknown'
    if overlap:
        p['quality']['cohorts']['duplicate']=copy.deepcopy(p['quality']['cohorts']['implementation'])
    with pytest.raises(ValueError,match='adverse or unknown exact development evidence'):
        service.resolve(request('Implement this contract.'),complete=classify,**args)


def test_no_implicit_fallback_and_no_borrowing_for_explicit_output_cap(installed):
    args = args_v4(installed)
    req = request('Implement this contract.'); req['output_cap']=1000
    assert service.resolve(req,complete=classify,**args)['config']['model_name']=='customer-workhorse'
    args = args_v4(installed,forecasts=False)
    args['models']=[m for m in args['models'] if m['name']!='customer-workhorse']
    with pytest.raises(ValueError,match='No eligible configured model'):
        service.resolve(request('Implement this contract.'),complete=classify,**args)
