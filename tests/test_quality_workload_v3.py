"""Synthetic evidence tests routing mechanics, never real model capability."""
import copy
import hashlib
import json

import pytest

from routing import service
from routing.quality import frontier, validate
from routing.selection_policy import validate as validate_policy
from routing.v7.retrieval import digest
from test_auto_routing_service import request
from test_configured_selection_policy import args_with_policy
from test_quality_contract_v2 import quality, SCOPE
from test_v14_installed_bundle import installed
from test_v14_native_fallback import native
from test_v14_qualification import DESC, PROFILE
from test_v14_scoped_bundle import cohort, candidate, compile


def scoped_quality(variants=None):
    variants = variants or {v: {'native': native(v)} for v in ('cheap', 'reference')}
    q = quality(1000)
    c = q['cohorts']['standard']
    c.update(demand='standard', work_profile=copy.deepcopy(PROFILE))
    q.update(version=3, cohorts={'implementation': c},
             native_contract_sha256={v: digest(row['native']) for v, row in variants.items()})
    return q, variants


def screen(q, variants, **scope):
    validate(q, variants)
    return frontier(q, list(variants), 'standard', request_scope={**SCOPE, **scope}, work_profile=PROFILE)


def test_same_difficulty_does_not_transfer_quality_between_different_work():
    q, variants = scoped_quality()
    assert screen(q, variants)['eligible'] == ['cheap', 'reference']
    for field, value in {'work':'explain', 'verification':'prove', 'evidence':'retrieve',
                         'reasoning':'bounded', 'creativity':'constrained'}.items():
        r = frontier(q, list(variants), 'standard', request_scope=SCOPE,
                     work_profile={**PROFILE, field:value})
        assert r['eligible'] == [] and r['status'] == 'missing_quality_workload_scope'
    assert frontier(q, list(variants), 'standard', request_scope=SCOPE)['eligible'] == []


@pytest.mark.parametrize('change', ['effort', 'alias', 'owner', 'fingerprint', 'transport', 'output', 'reasoning', 'cache'])
def test_native_contract_changes_cannot_reuse_quality(change):
    q, variants = scoped_quality(); n = variants['cheap']['native']
    if change == 'effort': n['effort'] = 'high'
    elif change == 'alias': n['model_binding']['name'] = 'another-alias'
    elif change == 'owner': n['model_binding']['project_id'] = 8
    elif change == 'fingerprint': n['model_binding']['fingerprint'] = 'e'*64
    elif change == 'transport': n['transport'] = 'anthropic_messages'
    elif change == 'output': n['output_allowance'] = 16000
    elif change == 'reasoning': n['reasoning_fields'] = {'reasoning_effort':'high'}
    else: n['cache_write_mode'] = 'separate'
    with pytest.raises(ValueError, match='native deployment/effort'): validate(q, variants)


@pytest.mark.parametrize('scope', [{'id':'other'}, {'has_history':True}, {'has_tools':True}, {'user_turns':2}])
def test_workload_quality_retains_execution_scope(scope):
    q, variants = scoped_quality()
    assert not screen(q, variants, **scope)['eligible']


def test_overlapping_scopes_abstain_without_winner_selection():
    q, variants = scoped_quality()
    q['cohorts']['duplicate'] = copy.deepcopy(q['cohorts']['implementation'])
    r = screen(q, variants)
    assert r['eligible'] == [] and r['status'] == 'ambiguous_quality_workload_scope'
    q['cohorts']['duplicate']['evaluation_scope']['request_scope_ids'] = ['another-contract']
    assert screen(q, variants)['cohort_id'] == 'implementation'


def test_statistical_bounds_unknowns_and_small_samples_remain_conservative():
    q, variants = scoped_quality(); c = q['cohorts']['implementation']
    c['outcomes']['cheap'][:100] = ['unknown']*100
    r = screen(q, variants)
    assert r['eligible'] == ['reference'] and r['assessments']['cheap']['unknown'] == 100
    c['groups'] = c['groups'][:20]
    c['outcomes'] = {v: ['pass']*20 for v in variants}
    assert not screen(q, variants)['eligible']


def test_malformed_workload_and_unbound_evidence_rejected():
    for change in ('field', 'enum', 'hash', 'variant'):
        q, variants = scoped_quality()
        if change == 'field': q['cohorts']['implementation']['work_profile']['rubric'] = 'benchmark-q1'
        elif change == 'enum': q['cohorts']['implementation']['work_profile']['work'] = 'anything'
        elif change == 'hash': q['native_contract_sha256']['cheap'] = '0'*64
        else: q['native_contract_sha256']['unmeasured'] = 'a'*64
        with pytest.raises(ValueError): validate(q, variants)


def certify(c):
    c['validation_certificate']['cohort_sha256'] = digest({k:v for k,v in c.items() if k != 'validation_certificate'})
    return c


def v3_args(installed, *, forecasts=True):
    service._calibrated_router.cache_clear()
    args = args_with_policy(installed)
    policy = args['settings']['calibration_selection_policy']
    q, _ = scoped_quality(policy['variants']); c = q['cohorts']['implementation']
    c['outcomes'] = {'customer-small': c['outcomes']['cheap'], 'customer-large': c['outcomes']['reference']}
    c['reference_variant'] = 'customer-large'
    c['evaluation_scope']['request_scope_ids'] = policy['request_scope_ids']
    policy['quality'] = q
    policy['revision'] = 'synthetic-workload-v3-test'
    value, _, path = installed; value = copy.deepcopy(value)
    value['validated_usage_cohorts'] = {}
    for vid, configured in [('old','customer-small'), ('new','customer-large')]:
        n = policy['variants'][configured]['native']
        value['variants'][vid].update(effort=n['effort'], requested_reasoning_fields=n['reasoning_fields'],
            reasoning_format=n['reasoning_format'], request_contract={'native':n, 'output_mode':'provider_default'})
        if forecasts:
            cost = cohort()
            cost.update(work_profile=copy.deepcopy(PROFILE), output_mode='provider_default')
            for row in cost['trajectories']:
                row['request_contract_sha256'] = digest(value['variants'][vid]['request_contract'])
                row['calls'][0]['output_tokens'] = 6000 if vid == 'old' else 100
            value['validated_usage_cohorts'][vid] = [certify(cost)]
    raw = json.dumps(value, sort_keys=True).encode(); (path/'text.json').write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    manifest = json.loads((path/'manifest.json').read_text()); manifest['profiles']['text']['sha256'] = sha
    (path/'manifest.json').write_text(json.dumps(manifest))
    args['settings']['calibration_snapshot_sha256'] = sha
    for vid, configured in [('old','customer-small'), ('new','customer-large')]:
        policy['variants'][configured]['evidence_ref'] = dict(profile='text', revision='test-installed-r1',
            variant=vid, snapshot_sha256=sha)
    validate_policy(policy)
    return args


def classify(*args, **kwargs):
    d = {**DESC, 'operation':'design', 'demand':'standard', 'effort_need':'low',
         'input_status':'provided', 'retrieval_source_ids':[], 'reference_ids':[], 'reason':'Supplied implementation',
         'task_continuity':{'action':'independent', 'task_id':None, 'summary':''}}
    return {'message':{'content':json.dumps(d)}, 'finish_reason':'stop'}


def test_gateway_chooses_total_forecast_over_cheapest_tokens_and_signs_choice(installed):
    args = v3_args(installed)
    r = service.resolve(request('Implement the supplied contract.'), complete=classify, **args)
    s = r['trace']['selection']
    assert r['config']['model_name'] == 'customer-large'
    assert s['reason'] == 'MEASURED_TRAJECTORY_EXPENDITURE'
    assert s['economics']['forecast_comparable'] is True
    assert s['economics']['switching_evidence'] is None
    quotes = s['economics']['quotes']
    assert float(quotes['configured-customer-small']['ranking_usd']) > float(quotes['configured-customer-large']['ranking_usd'])
    assert all(q['cache_ranking_enabled'] is False for q in quotes.values())
    pin = service.decode_pin(r['pin'], args['signing_key'], project_id=7, user_id=2,
                             settings=args['settings'], now=101)
    assert pin['config'] == r['config'] and r['config']['routing_output_mode'] == 'provider_default'


def test_missing_forecasts_use_authorized_fallback_not_equal_output_prices(installed):
    args = v3_args(installed, forecasts=False)
    r = service.resolve(request('Implement the supplied contract.'), complete=classify, **args)
    s = r['trace']['selection']
    assert r['config']['model_name'] == 'customer-workhorse'
    assert s['reason'] == 'UNMEASURED_CONFIGURED_FALLBACK'
    assert set(s['quality_screen']['eligible']) == {'customer-small','customer-large'}
    assert s['economics']['forecast_comparable'] is False and s['economics']['expected_cost'] is None
    assert s['economics']['savings_claim'] is False
    assert len(s['economics']['missing_support']) == 2


@pytest.mark.parametrize('change', ['missing', 'different_work', 'certificate', 'unvalidated', 'output_mode', 'range'])
def test_missing_or_changed_competitor_forecast_is_not_a_free_win(installed, change):
    args = v3_args(installed); router = service.configured_router(args['settings'])
    contract = router.catalog['variants']['configured-customer-small']['calibration_contract']
    cost = contract['usage_cohorts'][0]
    if change == 'missing': contract['usage_cohorts'] = []
    elif change == 'different_work': cost['work_profile']['work'] = 'explain'; certify(cost)
    elif change == 'certificate': cost['validation_certificate']['wape'] = .51
    elif change == 'unvalidated': contract.pop('forecast_validation_required')
    elif change == 'output_mode': cost.pop('output_mode'); certify(cost)
    else: cost['input_bytes']['min'] = 1999; certify(cost)
    r = service.resolve(request('Implement the supplied contract.'), complete=classify, **args)
    assert r['config']['model_name'] == 'customer-workhorse'
    assert r['trace']['selection']['economics']['missing_support']


def test_cost_fallback_cannot_escape_unavailable_owner_binding(installed):
    args = v3_args(installed, forecasts=False)
    args['models'] = [m for m in args['models'] if m['name'] != 'customer-workhorse']
    with pytest.raises(ValueError, match='No eligible configured model'):
        service.resolve(request('Implement the supplied contract.'), complete=classify, **args)


def test_lower_total_cost_cannot_override_quality_failure(installed):
    args = v3_args(installed)
    q = args['settings']['calibration_selection_policy']['quality']
    q['cohorts']['implementation']['outcomes']['customer-large'] = ['fail']*1000
    r = service.resolve(request('Implement the supplied contract.'), complete=classify, **args)
    assert r['config']['model_name'] == 'customer-small'
    assert r['trace']['selection']['quality_screen']['eligible'] == ['customer-small']


def test_v3_never_uses_legacy_cache_switching_policy(installed, monkeypatch):
    from routing.v7 import cache_switching
    def forbidden(*a, **kw):
        raise AssertionError('V3 must not call cache-aware switching')
    monkeypatch.setattr(cache_switching, 'retained_variant', forbidden)
    args = v3_args(installed)
    r = service.resolve(request('Implement the supplied contract.'), complete=classify, **args)
    assert r['trace']['selection']['economics']['forecast_comparable']


def test_explicit_output_cap_cannot_borrow_provider_default_quality(installed):
    args = v3_args(installed); req = request('Implement the supplied contract.')
    req['output_cap'] = 1000
    r = service.resolve(req, complete=classify, **args)
    assert r['config']['model_name'] == 'customer-workhorse'


def test_multiple_usage_workloads_compile_without_broad_pooling():
    value = candidate(); first = value['validated_usage_cohorts']['old'][0]
    first['work_profile'] = copy.deepcopy(PROFILE); certify(first)
    second = copy.deepcopy(first); second['work_profile']['work'] = 'explain'; certify(second)
    value['validated_usage_cohorts']['old'].append(second)
    assert len(compile(value)['variants']['old']['calibration_contract']['usage_cohorts']) == 2
    value['validated_usage_cohorts']['old'].append(copy.deepcopy(first))
    with pytest.raises(ValueError, match='Ambiguous usage'): compile(value)
