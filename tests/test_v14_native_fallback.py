"""Administrative native fallback is not fabricated calibration evidence."""
import copy
import json

import pytest

from routing import bundle, service
from routing.fallback_config import VARIANT, install, validate
from routing.inventory import model_binding
from routing.v7.candidate import CalibratedRouter
from test_auto_routing_service import fixture, request
from test_v14_installed_bundle import installed
from test_v14_coverage_fallback import descriptor
from test_v14_uniform_catalog import compile, snapshot


def native(model='customer-workhorse'):
    return {'model_binding': {'name': model, 'project_id': 7, 'fingerprint': 'f'*64},
            'effort': 'medium', 'transport': 'chat_completions',
            'reasoning_fields': {'reasoning_effort': 'medium'}, 'reasoning_format': 'top_level',
            'output_allowance': 8000, 'cache_write_mode': 'ordinary_input'}


def configured(installed):
    value, deployment, _ = installed
    args = fixture(); args.pop('router')
    names = [v['model'] for v in value['variants'].values()]+['customer-workhorse']
    args['models'] = [dict(name=n, project_id=7, context_window=128000,
        max_output_tokens=16000, openai_compatible=True, supports_reasoning=True) for n in names]
    setting = native(); setting['model_binding'] = model_binding(args['models'][-1])
    deployment['auto_routing_calibration_profiles']['7']['coverage_fallback_native'] = setting
    args['settings'] = bundle.runtime_settings(args['settings'], deployment, 7)
    args['price_snapshot']['entries'] = [dict(model_name=n, input_cost_per_token='.000001',
        output_cost_per_token='.000002', cache_read_input_token_cost='.0000001') for n in names]
    return args


def complete(*a, **kw):
    d = descriptor(demand='deep', input_status='provided', reference_ids=[], retrieval_source_ids=[],
        reason='Supplied task', task_continuity={'action':'independent','task_id':None,'summary':''})
    return {'message': {'content': json.dumps(d)}, 'finish_reason': 'stop'}


def test_native_contract_independent_of_measured_pool_and_classifier(installed):
    args = configured(installed)
    result = service.resolve(request('Design this component.'), complete=complete, **args)
    cfg = result['config'];trace=result['trace']
    assert cfg['model_name']=='customer-workhorse' and cfg['reasoning_effort']=='medium'
    assert cfg['routing_reasoning_fields']=={'reasoning_effort':'medium'} and cfg['max_tokens']==8000
    assert trace['selection']['family_qualification']['qualification_granted'] is False
    assert trace['calibration_contract']['cell'] is None
    assert trace['calibration_contract']['output_allowance']['measured'] is None
    assert trace['selection']['economics']['forecast_comparable'] is False
    router = service.configured_router(args['settings'])
    assert router.classifier.variant != VARIANT
    assert 'calibration_contract' not in router.catalog['variants'][VARIANT]


@pytest.mark.parametrize('kind', ['tools', 'history', 'tool_history'])
def test_outside_measured_envelope_gets_only_native_fallback(installed, kind):
    args = configured(installed);req=request('Continue with the supplied component.')
    if kind=='tools':
        req['tools']=[{'type':'function','function':{'name':'read_file','description':'Read local file',
            'parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path']}}}]
    else:
        req['messages'].insert(0, {'role':'assistant','content':'Earlier context.'})
        if kind=='tool_history':req['messages'].insert(0, {'role':'tool','content':'Saved result.'})
    result=service.resolve(req,complete=complete,**args)
    assert result['trace']['selection']['variant']==VARIANT
    assert result['trace']['selection']['family_qualification']['coverage_gap'].startswith('Unmeasured execution envelope:')


@pytest.mark.parametrize('change', ['removed','disabled','fingerprint','owner','reasoning','context','output','explicit_effort'])
def test_native_binding_cannot_escape_current_runtime_guards(installed, change):
    args=configured(installed);req=request('Design this component.');m=args['models'][-1]
    if change=='removed':args['models'].pop()
    elif change=='disabled':m['available']=False
    elif change=='fingerprint':m['configuration_fingerprint']='0'*64
    elif change=='owner':m['project_id']=8
    elif change=='reasoning':m['supports_reasoning']=False
    elif change=='context':m['context_window']=8000
    elif change=='output':m['max_output_tokens']=7999
    else:req['selection']['reasoning']={'mode':'explicit','preset':'high'}
    with pytest.raises(ValueError):service.resolve(req,complete=complete,**args)


def test_request_cannot_inject_native_fallback(installed):
    args=configured(installed)
    args['settings'].pop('calibration_coverage_fallback_native')
    req=request('Design this component.');req['coverage_fallback_native']=native()
    with pytest.raises(ValueError):service.resolve(req,complete=complete,**args)


def test_unknown_descriptor_keeps_uncertainty_with_explicit_workhorse(installed):
    args=configured(installed)
    result=service.resolve(request('Continue that task.'),complete=lambda *a,**k:{'message':{'content':'invalid'}},**args)
    assert result['config']['model_name']=='customer-workhorse'
    assert result['trace']['descriptor']['needs_context'] is True


def test_same_native_contract_negative_cannot_be_hidden_by_new_variant_name():
    catalog=compile(snapshot());old=catalog['variants']['old'];contract=old['calibration_contract']
    n=native(old['model']);n.update(effort=old['effort'],transport=contract['transport'],
        reasoning_fields={},reasoning_format='nested',output_allowance=contract['output_allowance'])
    for v in catalog['variants'].values():
        for cell in v['calibration_contract']['family_evidence']['development']['profile_evidence']:
            cell['fail']=1
        v['calibration_contract']['reasoning_fields']={}
    router=CalibratedRouter(object(),catalog=install(catalog,n),coverage_fallback_variant=VARIANT)
    from test_v14_qualification import DESC
    with pytest.raises(ValueError):router.select(descriptor(work_profile=DESC['work_profile']))


def test_strict_scopes_stay_strict_and_changed_contract_is_never_accepted(installed):
    args=configured(installed);r=service.configured_router(args['settings'])
    with pytest.raises(ValueError,match='configured task contract differs'):
        r.resolve([{'role':'user','content':'Do this'}],task_contract='forged')


def test_pins_reauthorize_exact_native_contract_and_config_change_revokes(installed):
    args=configured(installed);req=request('Design this component.')
    first=service.resolve(req,complete=complete,**args)
    req['prior_pin']=first['pin'];req['messages'].append({'role':'tool','content':'native result'})
    again=service.resolve(req,complete=lambda *a,**k:pytest.fail('Tool pin reclassified'),**args)
    assert again['config']==first['config']
    args['settings']['calibration_coverage_fallback_native']['effort']='high'
    args['settings']['calibration_coverage_fallback_native']['reasoning_fields']={'reasoning_effort':'high'}
    with pytest.raises(ValueError,match='policy changed'):service.resolve(req,complete=complete,**args)


@pytest.mark.parametrize('field,value', [('model_binding',{}),('transport','arbitrary'),('reasoning_fields',{}),
    ('reasoning_format','arbitrary'),('output_allowance',True),('output_allowance',0),('cache_write_mode','guessed')])
def test_invalid_native_contract_rejected_before_dispatch(field,value):
    n=native();n[field]=value
    with pytest.raises(ValueError):validate(n)
