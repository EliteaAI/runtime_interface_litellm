"""Configured eligibility must not collapse into one coverage fallback."""
import copy
import json
import hashlib

import pytest

from routing import bundle, service
from routing.inventory import model_binding
from routing.selection_policy import validate
from test_auto_routing_service import request
from test_v14_installed_bundle import installed
from test_v14_native_fallback import configured, native, complete
from test_v14_coverage_fallback import descriptor
from test_v14_qualification import DESC


def policy(value):
    return {'revision': 'customer-policy-1', 'source': 'Authorized test policy',
        'source_sha256': 'a'*64, 'request_scope_ids': [value['request_scope']['id']],
        'maximum_user_turns': 14, 'allow_tools': True, 'scenario_output_tokens': 1000,
        'variants': {name: {'native': native(name),
            'families': {'development': ['simple', 'standard', 'deep']},
            'operations': ['analysis', 'design', 'transform'], 'evidence_ref': None}
            for name in ['customer-small', 'customer-large']}}


def args_with_policy(installed):
    args = configured(installed)
    p = policy(installed[0])
    for name, row in p['variants'].items():
        model = dict(name=name, project_id=7, context_window=128000,
                     max_output_tokens=16000, openai_compatible=True, supports_reasoning=True)
        args['models'].append(model)
        row['native']['model_binding'] = model_binding(model)
        args['price_snapshot']['entries'].append(dict(model_name=name,
            input_cost_per_token='.000001' if name=='customer-small' else '.000003',
            output_cost_per_token='.000002' if name=='customer-small' else '.000006',
            cache_read_input_token_cost='.0000001'))
    args['settings']['calibration_selection_policy'] = p
    return args


def test_missing_exact_measurements_uses_configured_pool_and_comparable_prices(installed):
    args = args_with_policy(installed)
    result = service.resolve(request('Design this component.'), complete=complete, **args)
    cfg, trace = result['config'], result['trace']
    assert cfg['model_name']=='customer-small'
    assert cfg['max_tokens']==16000 and cfg['routing_output_mode']=='provider_default'
    assert cfg['routing_output_required'] is False and 'routing_min_output_cap' not in cfg
    selection=trace['selection'];economics=selection['economics']
    assert selection['reason']=='CONFIGURED_ELIGIBILITY_COMMON_PRICE_SCENARIO'
    assert selection['family_qualification']['qualification_granted'] is False
    assert economics['forecast_comparable'] is False and economics['expected_cost'] is None
    assert economics['savings_claim'] is False
    assert {q['scenario_output_tokens'] for q in economics['quotes'].values()}=={1000}
    assert trace['calibration_contract']['cell'] is None
    assert trace['calibration_contract']['output_allowance']['measured'] is None


def test_pricing_not_catalog_order_and_capacities_not_forecasts(installed):
    args=args_with_policy(installed)
    for model in args['models']:
        if model['name']=='customer-small':
            model['context_window']=1_000_000;model['max_output_tokens']=128000
            args['settings']['calibration_selection_policy']['variants']['customer-small']['native']['model_binding']=model_binding(model)
    result=service.resolve(request('Design it'),complete=complete,**args)
    assert result['config']['model_name']=='customer-small' and result['config']['max_tokens']==128000
    for row in args['price_snapshot']['entries']:
        if row['model_name']=='customer-small':
            row['input_cost_per_token']='.0001';row['output_cost_per_token']='.0002'
    result=service.resolve(request('Design it'),complete=complete,**args)
    assert result['config']['model_name']=='customer-large'


@pytest.mark.parametrize('history', ['conversation', 'tool'])
def test_configured_scope_handles_new_histories_without_borrowed_qualification(installed,history):
    args=args_with_policy(installed);req=request('Design it')
    req['messages'].insert(0,{'role':'tool' if history=='tool' else 'assistant','content':'Prior result'})
    result=service.resolve(req,complete=complete,**args)
    assert result['config']['model_name']=='customer-small'
    assert result['trace']['selection']['family_qualification']['measurement_scope_gap']
    assert result['trace']['selection']['economics']['forecast_comparable'] is False


def test_uncertainty_and_outside_configured_scope_keep_explicit_fallback(installed):
    args=args_with_policy(installed)
    result=service.resolve(request('Do it'),complete=lambda *a,**k:{'message':{'content':'invalid'}},**args)
    assert result['config']['model_name']=='customer-workhorse'
    assert result['config']['routing_output_mode']=='provider_default' and result['config']['max_tokens']==16000
    args['settings']['calibration_selection_policy']['maximum_user_turns']=1
    req=request('Design it');req['messages'].insert(0,{'role':'user','content':'Earlier request'})
    assert service.resolve(req,complete=complete,**args)['config']['model_name']=='customer-workhorse'


@pytest.mark.parametrize('change',['revoked','owner','fingerprint','context','output','reasoning'])
def test_configured_pool_keeps_native_access_and_request_guards(installed,change):
    args=args_with_policy(installed)
    for model in args['models']:
        if not model['name'].startswith('customer-') or model['name']=='customer-workhorse':continue
        if change=='revoked':model['available']=False
        elif change=='owner':model['project_id']=9
        elif change=='fingerprint':model['configuration_fingerprint']='0'*64
        elif change=='context':model['context_window']=100
        elif change=='output':model['max_output_tokens']=0
        else:model['supports_reasoning']=False
    assert service.resolve(request('Design it'),complete=complete,**args)['config']['model_name']=='customer-workhorse'


def test_prompt_cannot_introduce_policy_and_no_implicit_default_change(installed):
    args=configured(installed);req=request('Design it')
    req['selection_policy']=policy(installed[0])
    result=service.resolve(req,complete=complete,**args)
    assert result['config']['model_name']=='customer-workhorse'
    assert result['config']['routing_output_mode']=='measured'


def test_policy_revision_changes_revoke_tool_pin(installed):
    args=args_with_policy(installed);req=request('Design it')
    result=service.resolve(req,complete=complete,**args)
    req['prior_pin']=result['pin'];req['messages'].append({'role':'tool','content':'Result'})
    again=service.resolve(req,complete=lambda *a,**k:pytest.fail('Pinned turn reclassified'),**args)
    assert again['config']==result['config']
    args['settings']['calibration_selection_policy']['revision']='customer-policy-2'
    with pytest.raises(ValueError,match='policy changed'):service.resolve(req,complete=complete,**args)


@pytest.mark.parametrize('field,value', [('source_sha256','x'),('variants',{}),('maximum_user_turns',True),
    ('scenario_output_tokens',0),('scenario_output_tokens',9000),('allow_tools','yes'),('request_scope_ids',[])])
def test_invalid_configuration_fails_before_dispatch(installed,field,value):
    p=policy(installed[0]);p[field]=value
    with pytest.raises(ValueError):validate(p)


def test_binding_validates_and_hashes_policy(installed):
    _,deployment,_=installed;p=policy(installed[0])
    deployment['auto_routing_calibration_profiles']['7']['selection_policy']=p
    args=configured(installed)
    first=bundle.runtime_settings(args['settings'],deployment,7)
    assert first['calibration_selection_policy']==p
    p['revision']='changed'
    second=bundle.runtime_settings(args['settings'],deployment,7)
    assert first['revision']!=second['revision']


def with_evidence(installed, adverse=False):
    value,_,path=installed
    for identity in value['variants'].values():identity['requested_reasoning_fields']={}
    if adverse:
        for row in value['records']:
            if row['variant']=='old' and row['family']=='development':
                row['profile_evidence'][0].update(fail=1)
    raw=json.dumps(value,sort_keys=True).encode();(path/'text.json').write_bytes(raw)
    manifest=json.loads((path/'manifest.json').read_text())
    digest=hashlib.sha256(raw).hexdigest();manifest['profiles']['text']['sha256']=digest
    (path/'manifest.json').write_text(json.dumps(manifest))
    args=args_with_policy(installed);entry=args['settings']['calibration_selection_policy']['variants']['customer-small']
    identity=value['variants']['old']
    entry['native'].update(effort=identity['effort'],reasoning_fields={},reasoning_format='nested',
        transport=identity['transport'],output_allowance=identity['total_output_allowance'])
    entry['evidence_ref']={'profile':'text','revision':'test-installed-r1','variant':'old','snapshot_sha256':digest}
    return args


def matching(*a,**k):
    d=descriptor(**DESC,demand='standard',operation='design',effort_need='low',
        input_status='provided',reference_ids=[],retrieval_source_ids=[],reason='Supplied design')
    return {'message':{'content':json.dumps(d)},'finish_reason':'stop'}


def test_explicit_alias_maps_evidence_and_retains_its_negative(installed):
    args=with_evidence(installed,adverse=True)
    result=service.resolve(request('Design it'),complete=matching,**args)
    assert result['config']['model_name']=='customer-large'
    q=result['trace']['selection']['family_qualification']
    assert q['assessments']['configured-customer-small']['status']=='measured_failure'
    assert 'ADVERSE_OR_UNRESOLVED_EXACT_EVIDENCE' in q['exclusions']['configured-customer-small']


def test_alias_evidence_contract_mismatch_and_changed_source_rejected(installed):
    args=with_evidence(installed)
    service.resolve(request('Design it'),complete=matching,**args)
    args['settings']['calibration_selection_policy']['variants']['customer-small']['native']['effort']='high'
    args['settings']['calibration_selection_policy']['variants']['customer-small']['native']['reasoning_fields']={'reasoning':{'effort':'high'}}
    with pytest.raises(ValueError):service.resolve(request('Design it'),complete=matching,**args)
    args=with_evidence(installed)
    service.resolve(request('Design it'),complete=matching,**args)
    (installed[2]/'text.json').write_text('{}')
    with pytest.raises(ValueError,match='snapshot changed'):service.resolve(request('Design it'),complete=matching,**args)


def test_incomplete_forecasts_do_not_force_catalog_order_and_do_not_transfer_to_history(installed):
    args=with_evidence(installed)
    result=service.resolve(request('Design it'),complete=matching,**args)
    assert result['trace']['selection']['reason']=='CONFIGURED_ELIGIBILITY_COMMON_PRICE_SCENARIO'
    req=request('Design it');req['messages'].insert(0,{'role':'assistant','content':'Earlier plan'})
    result=service.resolve(req,complete=matching,**args)
    assert result['trace']['selection']['economics']['forecast_comparable'] is False
    assert result['config']['model_name']=='customer-small'
