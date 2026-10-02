import copy
import hashlib
import json
import pytest
from routing import bundle, service
from test_v14_scoped_bundle import candidate
from test_v14_qualification import DESC
from test_auto_routing_service import fixture, request


@pytest.fixture
def installed(tmp_path,monkeypatch):
    value=candidate();value.update(classifier_variant='classifier',baseline_variant='old')
    raw=json.dumps(value,sort_keys=True).encode();(tmp_path/'text.json').write_bytes(raw)
    manifest={'revision':'test-installed-r1','profiles':{'text':{'file':'text.json','sha256':hashlib.sha256(raw).hexdigest()}}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(bundle,'ROOT',tmp_path)
    deployment={'auto_routing_calibration_profiles':{'7':{'profile':'text','revision':'test-installed-r1'}}}
    return value,deployment,tmp_path


def test_opt_in_preserves_platform_project_gate_and_is_project_exact(installed):
    _,deployment,_=installed
    original={'enabled':False,'revision':'gate'}
    selected=bundle.runtime_settings(original,deployment,7)
    assert selected['enabled'] is False and selected['revision']!='gate'
    assert selected['calibration_task_contract']=='text:isolated-text-r2'
    assert bundle.runtime_settings(original,deployment,8)==original
    assert original=={'enabled':False,'revision':'gate'}


def test_absent_opt_in_retains_legacy_router(installed):
    assert service.configured_router({'enabled':True,'revision':'gate'}) is service.compiled_router()


def test_installed_snapshot_is_rechecked_even_when_router_is_cached(installed):
    _,deployment,path=installed
    settings=bundle.runtime_settings({'enabled':True,'revision':'gate'},deployment,7)
    one=service.configured_router(settings)
    assert service.configured_router(settings) is one
    (path/'text.json').write_text('{}')
    with pytest.raises(ValueError,match='snapshot changed'):service.configured_router(settings)


@pytest.mark.parametrize('binding',[{'profile':'unknown','revision':'test-installed-r1'},
    {'profile':'text','revision':'stale'}, {'profile':'text'}, 'text'])
def test_missing_stale_or_malformed_binding_does_not_fall_back(installed,binding):
    _,deployment,_=installed
    deployment['auto_routing_calibration_profiles']['7']=binding
    with pytest.raises(ValueError):bundle.runtime_settings({'enabled':True,'revision':'gate'},deployment,7)


def test_installed_service_path_compiles_before_signing_without_injected_router(installed):
    value,deployment,_=installed
    args=fixture();args.pop('router')
    args['settings']=bundle.runtime_settings(args['settings'],deployment,7)
    names=[v['model'] for v in value['variants'].values()]
    args['models']=[dict(name=n,project_id=7,context_window=128000,max_output_tokens=16000,openai_compatible=True) for n in names]
    args['price_snapshot']['entries']=[dict(model_name=n,input_cost_per_token='.000001',output_cost_per_token='.000002',cache_read_input_token_cost='.0000001') for n in names]
    calls=[]
    def complete(model,messages,**kw):
        calls.append(model)
        descriptor={**DESC,'operation':'design','demand':'standard','effort_need':'low','input_status':'provided',
            'retrieval_source_ids':[],'reference_ids':[],'reason':'Bounded supplied implementation',
            'task_continuity':{'action':'independent','task_id':None,'summary':''}}
        return {'message':{'content':json.dumps(descriptor)},'finish_reason':'stop'}
    req=request('Implement the supplied reservation ledger contract.')
    req['calibration_profile']='untrusted-request-profile'
    result=service.resolve(req,complete=complete,**args)
    assert len(calls)==1
    assert result['trace']['selection']['family_qualification']['eligible']
    pin=service.decode_pin(result['pin'],args['signing_key'],project_id=7,user_id=2,settings=args['settings'],now=101)
    assert pin['policy_revision']==service.configured_router(args['settings']).revision
    with pytest.raises(service.RoutingUnavailable):
        service.decode_pin(result['pin'],args['signing_key'],project_id=7,user_id=2,settings={'enabled':True,'revision':'gate'},now=101)
