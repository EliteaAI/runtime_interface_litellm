"""Missing optimization evidence may use only an explicit, bounded baseline."""
import copy
import json

import pytest

from routing import bundle, service
from routing.v7.candidate import CalibratedRouter
from routing.v7.catalog import QualificationSnapshot
from routing.v7.economics import rank
from test_auto_routing_service import fixture, request
from test_v14_installed_bundle import installed
from test_v14_qualification import DESC
from test_v14_uniform_catalog import compile, snapshot


def descriptor(**changes):
    return {**DESC, 'operation': 'design', 'demand': 'standard', 'effort_need': 'low',
            'work_profile': {**DESC['work_profile'], 'verification': 'none'}, **changes}


def router(variant='old', **kwargs):
    return CalibratedRouter(object(), catalog=compile(snapshot()), coverage_fallback_variant=variant, **kwargs)


def test_missing_exact_profile_uses_only_the_explicit_baseline_without_granting_quality():
    result = router().select(descriptor(), allowed=['old', 'new'])
    assert result['variant'] == 'old'
    assert result['reason'] == 'UNMEASURED_CONFIGURED_FALLBACK'
    evidence = result['family_qualification']
    assert evidence['eligible'] == [] and evidence['qualification_granted'] is False
    assert evidence['optimization_eligible'] is False and evidence['promotion'] is False
    assert evidence['coverage_gap'] == 'unmeasured_work_profile'
    assert all(not x['eligible'] for x in evidence['assessments'].values())


def test_strict_default_remains_strict_and_policy_revision_changes_on_opt_in():
    strict = router(None)
    with pytest.raises(ValueError, match='No eligible configured model'):
        strict.select(descriptor())
    assert strict.revision != router().revision


def test_explicit_fallback_is_independent_of_catalog_baseline_and_classifier():
    chosen = router('new').select(descriptor())
    assert chosen['variant'] == 'new'
    assert chosen['family_qualification']['fallback_variant'] == 'new'
    assert router('new').revision != router('old').revision


def test_no_implicit_classifier_or_catalog_baseline_when_setting_is_absent():
    r = CalibratedRouter(object(), catalog=compile(snapshot()))
    with pytest.raises(ValueError, match='No eligible configured model'):
        r.select(descriptor())


@pytest.mark.parametrize('change', [
    {'independent_templates': 1}, {'wilson95': [.1, 1]},
])
def test_sparse_support_keeps_original_measurements(change):
    value = compile(snapshot())
    for variant in value['variants'].values():
        for cell in variant['calibration_contract']['family_evidence']['development']['profile_evidence']:
            cell.update(change)
    original = copy.deepcopy(value)
    result = CalibratedRouter(object(), catalog=value, coverage_fallback_variant='old').select(
        descriptor(work_profile=DESC['work_profile']))
    assert result['variant'] == 'old' and result['family_qualification']['eligible'] == []
    assert value == original


@pytest.mark.parametrize('change', [
    {'fail': 1}, {'fail': 1, 'refused': 1}, {'disagreement': 1}, {'unknown': 1},
    {'delivery_failure': 1}, {'compatible_contracts': False}, {'adverse_evidence': ['hold']},
])
def test_adverse_disputed_unknown_or_incompatible_exact_baseline_never_falls_back(change):
    catalog = compile(snapshot())
    for vid in ('old', 'new'):
        cell = catalog['variants'][vid]['calibration_contract']['family_evidence']['development']['profile_evidence'][0]
        cell.update(change)
    with pytest.raises(ValueError, match='No eligible configured model'):
        CalibratedRouter(object(), catalog=catalog, coverage_fallback_variant='old').select(
            descriptor(work_profile=DESC['work_profile']))


@pytest.mark.parametrize('change', [
    {'demand': 'deep'}, {'effort_need': 'high'}, {'operation': 'greeting'},
])
def test_existing_operation_demand_and_effort_guards_remain(change):
    r = router()
    if 'effort_need' in change:
        r.catalog['variants']['old']['effort'] = 'low'
    with pytest.raises(ValueError, match='No eligible configured model'):
        r.select(descriptor(**change))


@pytest.mark.parametrize('allowed', [[], ['new'], ['classifier']])
def test_baseline_outside_runtime_pool_cannot_be_restored(allowed):
    with pytest.raises(ValueError, match='No eligible configured model'):
        router().select(descriptor(), allowed=allowed)


def test_explicit_qualification_rejection_still_blocks_baseline():
    qualifications = QualificationSnapshot([
        {'variant': 'old', 'task_family': 'design', 'contract': 'text-tools-v1', 'status': 'rejected'}])
    with pytest.raises(ValueError, match='No eligible configured model'):
        router(qualifications=qualifications).select(descriptor())


def test_no_reranking_or_forecast_for_unmeasured_baseline():
    r = router()
    selected = r.select(descriptor())
    result = rank(selected, [], r.catalog, session=None, gateway=object(), cap=8000, previous='new')
    assert result['variant'] == 'old'
    assert result['economics']['forecast_comparable'] is False
    assert result['economics']['quotes'] == {} and result['economics']['switching_evidence'] is None


def test_qualified_routes_are_unchanged():
    desc = descriptor(work_profile=DESC['work_profile'])
    strict = router(None).select(desc)
    relaxed = router().select(desc)
    assert strict == relaxed


@pytest.mark.parametrize('mode', [True, '', 'cheapest', [], {}])
def test_bad_mode_is_rejected(mode, installed):
    _, deployment, _ = installed
    deployment['auto_routing_calibration_profiles']['7']['coverage_fallback_variant'] = mode
    with pytest.raises(ValueError):
        bundle.runtime_settings({'enabled': True, 'revision': 'gate'}, deployment, 7)
    with pytest.raises(ValueError):
        router(mode)


def installed_args(installed, mode='old'):
    value, deployment, _ = installed
    deployment['auto_routing_calibration_profiles']['7']['coverage_fallback_variant'] = mode
    args = fixture(); args.pop('router')
    args['settings'] = bundle.runtime_settings(args['settings'], deployment, 7)
    names = [v['model'] for v in value['variants'].values()]
    args['models'] = [dict(name=n, project_id=7, context_window=128000,
                          max_output_tokens=16000, openai_compatible=True) for n in names]
    args['price_snapshot']['entries'] = [dict(model_name=n, input_cost_per_token='.000001',
        output_cost_per_token='.000002', cache_read_input_token_cost='.0000001') for n in names]
    return args


def complete(*args, **kwargs):
    desc = descriptor(input_status='provided', retrieval_source_ids=[], reference_ids=[],
        reason='Design the supplied contract', task_continuity={'action': 'independent', 'task_id': None, 'summary': ''})
    return {'message': {'content': json.dumps(desc)}, 'finish_reason': 'stop'}


@pytest.mark.parametrize('family', ['development', 'quantitative'])
def test_installed_policy_signs_binding_and_same_run_renews_without_another_classifier(installed, family):
    args = installed_args(installed)
    req = request('Design the supplied reservation ledger.')
    def classify(*a, **kw):
        response = complete(*a, **kw)
        desc = json.loads(response['message']['content'])
        desc['task_family'] = family
        response['message']['content'] = json.dumps(desc)
        return response
    result = service.resolve(req, complete=classify, **args)
    assert result['config']['model_name'] == 'gpt-5.4'
    assert result['trace']['selection']['reason'] == 'UNMEASURED_CONFIGURED_FALLBACK'
    assert result['trace']['calibration_contract']['cell'] is None
    assert result['trace']['calibration_contract']['status'] == 'unmeasured_configured_fallback'
    req.update(prior_pin=result['pin'], state_token=result['state_token'])
    req['messages'].append({'role': 'tool', 'content': 'Operation complete', 'tool_call_id': 't1'})
    renewed = service.resolve(req, complete=lambda *a, **k: pytest.fail('Renewal reclassified'), **args)
    assert renewed['config'] == result['config']
    old_revision = args['settings']['revision']
    strict = installed_args(installed, None)
    assert strict['settings']['revision'] != old_revision
    with pytest.raises(service.RoutingUnavailable):
        service.resolve(req, complete=complete, **strict)


def test_request_cannot_enable_fallback(installed):
    args = installed_args(installed, None)
    req = request('Design the supplied reservation ledger.')
    req.update(coverage_fallback_variant='old', calibration_coverage_fallback_variant='old')
    req['selection']['coverage_fallback_variant'] = 'old'
    with pytest.raises(ValueError, match='No eligible configured model'):
        service.resolve(req, complete=complete, **args)


@pytest.mark.parametrize('change', ['missing_baseline', 'unknown_price', 'insufficient_context', 'output_contract', 'disabled_gate'])
def test_service_runtime_exclusions_are_not_overridden(installed, change):
    args = installed_args(installed)
    req = request('Design the supplied reservation ledger.')
    if change == 'missing_baseline':
        args['models'] = [m for m in args['models'] if m['name'] != 'gpt-5.4']
    if change == 'unknown_price':
        args['price_snapshot']['entries'] = [r for r in args['price_snapshot']['entries'] if r['model_name'] != 'gpt-5.4']
    if change == 'insufficient_context':
        next(m for m in args['models'] if m['name'] == 'gpt-5.4')['context_window'] = 8000
    if change == 'output_contract': req['output_cap'] = 4000
    if change == 'disabled_gate': args['settings']['enabled'] = False
    with pytest.raises(ValueError):
        service.resolve(req, complete=complete, **args)
