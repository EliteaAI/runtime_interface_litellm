"""Measured effort selection and signed wire contract; no model calls."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from routing.effort import validate_fields
from routing.service import resolve, RoutingUnavailable
from routing.v7.catalog import effort_candidate, compile_catalog
from routing.v7.candidate import CalibratedRouter, compiled_policy
from test_auto_routing_service import fixture, request

ROOT = Path(__file__).resolve().parents[1]
DATA = effort_candidate()


@pytest.fixture(autouse=True)
def frozen_v12_family_cells(monkeypatch):
    # This suite reproduces the frozen V12 measured-effort contracts. The product catalog admits by
    # the data-derived table (tests/test_admission_*.py); here the family cells still decide.
    import routing.service as service
    router = service.GenerationRouter(service.ClassifierTransport(), service.CONFIGURED_CLASSIFIER,
                                      catalog=compile_catalog(admission=False))
    monkeypatch.setattr(service, 'compiled_router', lambda: router)


def descriptor(family, demand, effort='low'):
    return {'operation': 'analysis', 'task_family': family, 'demand': demand,
            'effort_need': effort, 'relation': 'independent', 'needs_context': False,
            'reference_ids': [], 'reason': 'Analyze supplied evidence',
            'input_status': 'provided', 'retrieval_source_ids': []}


@pytest.mark.parametrize('row', DATA['records'], ids=lambda r: r['variant']+'-'+r['family'])
@pytest.mark.parametrize('demand', ['simple', 'standard', 'deep'])
def test_all_cells_use_unchanged_full_family_outcomes_and_observed_demand(row, demand):
    router = CalibratedRouter(object(), 'luna-default', catalog=compile_catalog(admission=False))
    result = router.select(descriptor(row['family'], demand), allowed=['gpt54-high', row['variant']])
    assert (result['variant'] == row['variant']) == (row['eligible_local_beta'] and demand in row['demand_coverage'])
    if result['variant'] == row['variant']:
        assert row['fail'] == row['unknown'] == 0 and row['sample_count'] >= 3


def inputs(preset):
    args = fixture()
    contract = DATA['variants'][preset]
    args['models'] = [
        # Classifier-only: the context window cannot hold the model's own output maximum.
        {'name': 'global.openai.gpt-5.6-luna', 'project_id': 7, 'context_window': 1000, 'max_output_tokens': 1024},
        {'name': contract['model'], 'project_id': 7, 'context_window': 128000,
         'max_output_tokens': 32000, 'supports_reasoning': True}]
    args['price_snapshot']['entries'].append(dict(model_name=contract['model'],
        input_cost_per_token='.000001', output_cost_per_token='.000002',
        cache_read_input_token_cost='.0000001', cache_creation_input_token_cost='.00000125'))
    return args


@pytest.mark.parametrize('preset', [p for p,v in DATA['variants'].items() if v['effort']])
def test_each_explicit_effort_can_be_selected_pinned_and_renewed(preset):
    contract = DATA['variants'][preset]
    cell = next(r for r in DATA['records'] if r['variant'] == preset and r['eligible_local_beta'])
    req = request('Analyze supplied evidence')
    req['selection']['reasoning'] = {'mode': 'explicit', 'preset': contract['effort']}
    desc = descriptor(cell['family'], cell['demand_coverage'][0], contract['effort'])
    calls = []
    def complete(*a, **k):
        calls.append(1)
        return {'message': {'content': json.dumps(desc)}, 'finish_reason': 'stop'}
    args = inputs(preset)
    result = resolve(req, complete=complete, **args)
    assert result['trace']['selection']['variant'] == preset
    assert result['config']['reasoning_effort'] == contract['effort']
    assert result['config']['routing_reasoning_fields'] == contract['requested_reasoning_fields']
    validate_fields(result['config'], contract['requested_reasoning_fields'])
    req.update(prior_pin=result['pin'], state_token=result['state_token'])
    req['messages'].append({'role': 'user', 'content': 'Steering: consider another failure case'})
    renewed = resolve(req, complete=lambda *a, **k: pytest.fail('Run reclassified'), **args)
    assert renewed['config'] == result['config'] and len(calls) == 1


@pytest.mark.parametrize('preset', ['terra-high', 'opus5-high'])
@pytest.mark.parametrize('mutation', ['alias', 'extra', 'effort', 'null', 'budget'])
def test_measured_fields_reject_aliases_overrides_and_conflicts(preset, mutation):
    c = DATA['variants'][preset]
    config = {'reasoning_effort': c['effort'], 'routing_transport': c['transport'],
              'routing_reasoning_fields': c['requested_reasoning_fields']}
    body = copy.deepcopy(c['requested_reasoning_fields'])
    if mutation == 'alias': body = {'reasoning_effort': c['effort']}
    elif mutation == 'extra': body['reasoning_effort'] = c['effort']
    elif mutation == 'effort': body = {'reasoning': {'effort': 'low'}}
    elif mutation == 'null': body['reasoning_effort'] = None
    else: body['thinking'] = {'type': 'enabled', 'budget_tokens': 9092}
    with pytest.raises(ValueError): validate_fields(config, body)


def test_reasoning_disabled_configuration_still_excludes_a_native_effort():
    args = inputs('opus5-high'); args['models'][-1].update(supports_reasoning=False)
    req = request('Analyze supplied evidence'); req['selection']['reasoning'] = {'mode': 'explicit', 'preset': 'high'}
    with pytest.raises(RoutingUnavailable):
        resolve(req, complete=lambda *a, **k: pytest.fail('No contract admitted'), **args)


def test_compatible_transport_is_provisional_not_excluded_and_unpins_the_native_envelope():
    # #6722: an OpenAI-compatible deployment of an Anthropic contract is admitted, flagged, and the
    # pin carries no measured native transport or reasoning envelope.
    args = inputs('opus5-high'); args['models'][-1].update(openai_compatible=True)
    req = request('Analyze supplied evidence'); req['selection']['reasoning'] = {'mode': 'explicit', 'preset': 'high'}
    cell = next(r for r in DATA['records'] if r['variant'] == 'opus5-high' and r['eligible_local_beta'])
    desc = descriptor(cell['family'], cell['demand_coverage'][0], 'high')
    result = resolve(req, complete=lambda *a, **k: {'message': {'content': json.dumps(desc)}, 'finish_reason': 'stop'}, **args)
    assert result['trace']['selection']['variant'] == 'opus5-high'
    assert result['trace']['inventory']['provisional_contracts']['opus5-high']['flags'] == ['CALIBRATION_TRANSPORT_UNMEASURED']
    assert 'routing_transport' not in result['config'] and 'routing_reasoning_fields' not in result['config']


def test_live_output_maximum_is_the_provider_default_bound():
    args = inputs('opus5-high'); args['models'][-1].update(max_output_tokens=8000)
    req = request('Analyze supplied evidence'); req['selection']['reasoning'] = {'mode': 'explicit', 'preset': 'high'}
    cell = next(r for r in DATA['records'] if r['variant'] == 'opus5-high' and r['eligible_local_beta'])
    desc = descriptor(cell['family'], cell['demand_coverage'][0], 'high')
    result = resolve(req, complete=lambda *a, **k: {'message': {'content': json.dumps(desc)}, 'finish_reason': 'stop'}, **args)
    assert result['config']['max_output_tokens'] == 8000 and result['config']['max_tokens'] is None
    req['output_cap'] = 8001
    with pytest.raises(RoutingUnavailable):
        resolve(req, complete=lambda *a, **k: pytest.fail('No model fits the cap'), **args)


def test_original_observations_and_legacy_candidate_remain_reproducible():
    assert len(compile_catalog('v9')['variants']) == 13
    assert len(compile_catalog()['variants']) == 33
    assert len(compile_catalog(admission=False)['variants']) == 28
    assert DATA['original_outcomes'] == {'pass': 1278, 'fail': 210, 'unknown': 12}
    assert sum(r['eligible_local_beta'] for r in DATA['records']) == 248
    assert DATA['production_promotion_allowed'] is False
    assert compiled_policy('v9') != compiled_policy()
    spec = importlib.util.spec_from_file_location('v12compiler', ROOT/'scripts/compile_v12_candidate.py')
    compiler = importlib.util.module_from_spec(spec); spec.loader.exec_module(compiler)
    raw = (ROOT/'routing/v7/calibration-v11-observations.json').read_bytes()
    rebuilt = compiler.compile_candidate(json.loads(raw), compiler.sha(raw))
    assert rebuilt['variants'] == DATA['variants']
    for expected, row in zip(DATA['records'], rebuilt['records']):
        assert {k:v for k,v in expected.items() if k != 'usage_profile'} == {k:v for k,v in row.items() if k != 'usage_profile'}
    with pytest.raises(ValueError): compiler.compile_candidate(json.loads(raw), 'changed')


@pytest.mark.parametrize('family_model', ['terra','sol','opus5','opus48','opus47'])
def test_auto_itself_selects_measured_effort_when_default_lacks_quality_evidence(family_model):
    cells={(r['variant'],r['family']):r for r in DATA['records']}
    cell=next(r for r in DATA['records'] if r['variant'].startswith(family_model+'-')
              and r['eligible_local_beta'] and not cells[(family_model+'-default',r['family'])]['eligible_local_beta'])
    preset=cell['variant'];effort=DATA['variants'][preset]['effort']
    desc=descriptor(cell['family'],cell['demand_coverage'][0],effort)
    req=request('Analyze supplied evidence')
    assert req['selection']['reasoning']=={'mode':'auto'}
    result=resolve(req,complete=lambda *a,**k:{'message':{'content':json.dumps(desc)},'finish_reason':'stop'},**inputs(preset))
    assert result['config']['reasoning_effort'] is not None
    selected=result['trace']['selection']['variant']
    assert cells[(selected,cell['family'])]['eligible_local_beta']
    assert result['config']['routing_reasoning_fields']==DATA['variants'][selected]['requested_reasoning_fields']
