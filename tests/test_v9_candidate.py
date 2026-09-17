"""Generated calibration and live admission contracts; no inference or workspace dependency."""
import base64
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from test_auto_routing_service import fixture, request
from routing.service import resolve, encode_pin, RoutingUnavailable, compiled_router
from routing.v7.catalog import calibration_candidate, compile_catalog
from routing.v7.candidate import CalibratedRouter

ROOT = Path(__file__).resolve().parents[1]
DATA = calibration_candidate()
VARIANTS = sorted(DATA['variants'])


def descriptor(family='transformation', demand='standard', **changes):
    return dict(operation='transform', demand=demand, task_family=family,
                relation='independent', reference_ids=[], needs_context=False,
                reason='Transform supplied records', effort_need='low',
                input_status='provided', retrieval_source_ids=[], **changes)


def classified(value=None):
    return {'message': {'content': json.dumps(value or descriptor())}, 'finish_reason': 'stop'}


def inputs(vid, **metadata):
    args = fixture()
    target = DATA['variants'][vid]['model']
    # Classifier stays available but cannot perform an8k generation. The single
    # new generation model must pass the real family and transport gates.
    args['models'] = [{'name': 'global.openai.gpt-5.6-luna', 'project_id': 7,
                       'context_window': 128000, 'max_output_tokens': 1024},
                     {'name': target, 'project_id': 7, 'context_window': 128000,
                      'max_output_tokens': 32000, **metadata}]
    args['price_snapshot']['entries'].append(dict(model_name=target,
        input_cost_per_token='.000001', output_cost_per_token='.000002',
        cache_read_input_token_cost='.0000001', cache_creation_input_token_cost='.00000125'))
    return args


def state_body(token):
    body = token.split('.')[0]
    return json.loads(base64.urlsafe_b64decode(body+'='*((-len(body)) % 4)))


def test_generated_evidence_keeps_all_original_failures_unknowns_and_bounds():
    assert len(DATA['records']) == 100
    assert sum(r['eligible_local_beta'] for r in DATA['records']) == 54
    assert {k: sum(r[k] for r in DATA['records']) for k in ('pass','fail','unknown')} == {
        'pass': 313, 'fail': 57, 'unknown': 5}
    assert DATA['production_promotion_allowed'] is False
    assert {v['effort'] for v in DATA['variants'].values()} == {None}
    assert {v['total_output_allowance'] for v in DATA['variants'].values()} == {32000}
    assert all(r['wilson95'][0] < .6 for r in DATA['records'] if r['eligible_local_beta'])
    assert set(compile_catalog()['variants']) == set(VARIANTS) | {
        'gpt54-medium', 'gpt54-low', 'gpt54-high', 'mini-low', 'mini-medium',
        'haiku-default', 'sonnet-default', 'luna-default'}


@pytest.mark.parametrize('row', DATA['records'], ids=lambda r: r['variant']+'-'+r['family'])
@pytest.mark.parametrize('demand', ['simple','standard','deep'])
def test_every_cell_requires_full_family_success_and_exact_observed_demand(row,demand):
    chosen = CalibratedRouter(object()).select(descriptor(row['family'], demand),
        allowed=['gpt54-high', row['variant']])
    expected = row['eligible_local_beta'] and demand in row['demand_coverage']
    assert (chosen['variant'] == row['variant']) == expected
    if not expected:
        assert chosen['variant'] == 'gpt54-high'
        assert chosen['reason'] == 'UNCERTAIN_TASK_BASELINE'


@pytest.mark.parametrize('change', [{'task_family':'unknown'}, {'operation':'other'},
    {'needs_context':True}, {'relation':'ambiguous'}, {'operation':'greeting'}])
def test_uncertain_or_social_descriptors_never_gain_new_default_fallback(change):
    desc=descriptor();desc.update(change)
    chosen=CalibratedRouter(object()).select(desc,allowed=['gpt54-high','sol-default'])
    assert chosen['variant']=='gpt54-high'


@pytest.mark.parametrize('vid', VARIANTS)
def test_absent_cap_materializes_measured_contract_and_renews_without_classifier(vid):
    args=inputs(vid);req=request('Reformat the supplied records A=1 B=2.');calls=[]
    result=resolve(req, complete=lambda *a,**k: (calls.append(a) or classified()), **args)
    assert len(calls)==1
    assert result['config']['max_tokens']==32000
    assert result['trace']['budget']['completion_cap']==32000
    assert result['trace']['selection']['economics']['output_range']==[0,32000]
    assert result['trace']['selection']['economics']['quotes'][vid]['output_allowance']==32000
    assert result['config']['routing_transport']==DATA['variants'][vid]['transport']
    assert result['config']['routing_min_output_cap']==32000
    assert result['config']['reasoning_effort'] is None
    state=state_body(result['state_token'])
    assert state['decision']['budget']['completion_cap']==32000
    req['prior_pin']=result['pin'];req['state_token']=result['state_token']
    req['messages'].append({'role':'tool','content':'Completed step','tool_call_id':'t1'})
    renewed=resolve(req, complete=lambda *a,**k: pytest.fail('Same run reclassified'), **args)
    assert renewed['config']==result['config']
    assert renewed['state_token']==result['state_token']
    req['output_cap']=8000
    with pytest.raises(RoutingUnavailable):
        resolve(req,complete=lambda *a,**k: pytest.fail('Invalid renewal classified'),**args)


@pytest.mark.parametrize('change', [ {'output_cap':8000}, {'output_cap':31999},
    {'output_cap':None}, {'output_schema':{'type':'object'}},
    {'selection':{'mode':'auto','profile_ref':{'id':'v7-quality-cost','revision':1},
                  'reasoning':{'mode':'explicit','preset':'high'}}}])
def test_explicit_limits_presets_and_unmeasured_schema_are_not_overridden(change):
    req=request('Reformat A=1 B=2.');req.update(change)
    with pytest.raises(RoutingUnavailable):
        resolve(req,complete=lambda *a,**k: pytest.fail('No model admitted'),**inputs('sol-default'))


@pytest.mark.parametrize('metadata', [{'max_output_tokens':16000}, {'context_window':32001},
    {'openai_compatible':True}])
def test_native_opus_contract_cannot_override_live_configuration(metadata):
    with pytest.raises(RoutingUnavailable):
        resolve(request('Reformat supplied records.'),complete=lambda *a,**k: classified(),
                **inputs('opus5-default',**metadata))


def test_compatible_opus_is_reported_as_unmeasured_transport_not_incapable():
    args = fixture()
    extra = inputs('opus5-default', openai_compatible=True)
    args['models'].append(extra['models'][1])
    args['price_snapshot']['entries'].append(extra['price_snapshot']['entries'][-1])
    result = resolve(request('Reformat supplied records A=1 B=2.'),
                     complete=lambda *a, **k: classified(), **args)
    gap = result['trace']['inventory']['unmeasured_contracts']['opus5-default']
    assert gap == {'reasons': ['CALIBRATION_TRANSPORT_UNMEASURED'],
                   'configured_transport': 'chat_completions',
                   'measured_transport': 'anthropic_messages'}
    assert DATA['variants']['opus5-default']['model'] not in result['trace']['inventory']['excluded']


def test_unmeasured_effort_is_distinct_from_transport_gap():
    args = fixture()
    extra = inputs('sol-default')
    args['models'].append(extra['models'][1])
    args['price_snapshot']['entries'].append(extra['price_snapshot']['entries'][-1])
    req = request('Reformat supplied records A=1 B=2.')
    req['selection']['reasoning'] = {'mode': 'explicit', 'preset': 'high'}
    result = resolve(req, complete=lambda *a, **k: classified(), **args)
    gap = result['trace']['inventory']['unmeasured_contracts']['sol-default']
    assert gap['reasons'] == ['CALIBRATION_EXPLICIT_EFFORT_UNMEASURED']


def test_explicit_measured_cap_is_accepted_and_missing_price_stays_unknown():
    req=request('Reformat A=1.');req['output_cap']=32000
    result=resolve(req,complete=lambda *a,**k: classified(),**inputs('terra-default'))
    assert result['config']['max_tokens']==32000
    args=inputs('terra-default');args['price_snapshot']['entries'].pop()
    with pytest.raises(RoutingUnavailable):
        resolve(req,complete=lambda *a,**k: pytest.fail('No price'),**args)


def test_old_policy_pin_rejected_and_public_saved_handle_stays_stable():
    from routing.service import PROFILE
    assert PROFILE=={'id':'v7-quality-cost','revision':1}
    args=inputs('sol-default');req=request('Reformat A=1 B=2.')
    result=resolve(req,complete=lambda *a,**k: classified(),**args)
    pin=state_body(result['pin']);pin['policy_revision']='old-policy'
    req['prior_pin']=encode_pin(pin,args['signing_key'])
    with pytest.raises(RoutingUnavailable,match='policy changed'):
        resolve(req,complete=lambda *a,**k: pytest.fail('No silent reroute'),**args)


def test_compiler_rejects_changed_source_and_preserves_original_baseline_cohorts():
    spec=importlib.util.spec_from_file_location('v9compiler',ROOT/'scripts/compile_v9_candidate.py')
    compiler=importlib.util.module_from_spec(spec);spec.loader.exec_module(compiler)
    with pytest.raises(ValueError,match='original sealed'):
        compiler.compile_candidate({},'not-the-original')
    policy=json.loads((ROOT/'routing/v7/qualification-policy.json').read_text())
    assert compiler.policy_for(DATA,policy)==policy  # reproducible, no duplicate union
    bank=json.loads((ROOT/'tests/fixtures/v7-policy-parity.json').read_text())
    old_ids=set(bank['catalog']['variants'])
    for row in bank['cases']:
        family=row['descriptor'].get('task_family')
        expected=row['expected'].get('family_qualification',{}).get('eligible')
        if expected:
            assert set(expected)<=set(policy['eligible_by_family'][family]) & old_ids


def test_economics_prices_each_eligible_contract_at_its_actual_allowance():
    args=fixture();target=inputs('sol-default')
    args['models'].append(target['models'][1])
    args['price_snapshot']['entries'].append(target['price_snapshot']['entries'][-1])
    for price in args['price_snapshot']['entries']:
        price.update(input_cost_per_token='0',cache_read_input_token_cost='0',cache_creation_input_token_cost='0',
                     output_cost_per_token='.0000005' if price['model_name']==DATA['variants']['sol-default']['model'] else '.000001')
    req=request('Reformat these supplied records A=1 B=2.')
    result=resolve(req,complete=lambda *a,**k: classified(),**args)
    quotes=result['trace']['selection']['economics']['quotes']
    assert quotes['sol-default']['output_allowance']==32000
    assert quotes['luna-default']['output_allowance']==8000
    assert result['trace']['selection']['variant']!='sol-default'
    assert result['config']['max_tokens']==8000  # .008 upper bound < new .016
    req['output_cap']=32000
    for model in args['models']:model['max_output_tokens']=32000
    explicit=resolve(req,complete=lambda *a,**k: classified(),**args)
    assert explicit['trace']['selection']['variant']=='sol-default'
    assert explicit['config']['max_tokens']==32000  # measured .016 < old .032
