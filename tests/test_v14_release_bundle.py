"""Exercise reviewed release data through the actual installed routing path."""
import json
import pytest
from routing import bundle,service
from routing.v7.qualification import cell_status,assess
from routing.v7.calibration_scope import check_request_scope
from routing.v7.retrieval import digest
from test_auto_routing_service import fixture,request

REVISION='v14-application-r35'

def configured(name):
    return bundle.runtime_settings({'enabled':True,'revision':'unit-gate'},
        {'auto_routing_calibration_profiles':{'7':{'profile':name,'revision':REVISION}}},7)

@pytest.mark.parametrize('name,cells,cohorts',[
    ('isolated-text',179,17),('repository-fixture',22,0),('bounded-text-conversation',3,0),
    ('long-text-conversation',2,0)])
def test_release_profiles_recompile_and_preserve_uniform_admission(name,cells,cohorts):
    router=service.configured_router(configured(name));catalog=router.catalog
    found=0
    for v in catalog['variants'].values():
        contract=v['calibration_contract']
        for family,row in contract['family_evidence'].items():
            for cell in row['profile_evidence']:
                for demand in cell['demand_coverage']:
                    expected=cell_status(cell,contract['qualification_policy'])=='provisional_qualified'
                    desc={'task_family':family,'work_profile':cell['work_profile'],'relation':'independent','needs_context':False}
                    assert assess(contract,desc,demand)['eligible']==expected
                    found+=expected
    assert found==cells
    assert sum(len(v['calibration_contract']['usage_cohorts']) for v in catalog['variants'].values())==cohorts


def test_release_context_rejects_longer_and_tool_histories():
    scope=service.configured_router(configured('bounded-text-conversation')).catalog['request_scope']
    assert scope['maximum_user_turns']==4 and scope['tools_sha256']==digest([])
    check_request_scope(scope,scope['id'],[{'role':'user','content':'x'}]*4,[])
    with pytest.raises(ValueError,match='turn bound'):
        check_request_scope(scope,scope['id'],[{'role':'user','content':'x'}]*5,[])
    with pytest.raises(ValueError,match='prior tool history'):
        check_request_scope(scope,scope['id'],[{'role':'user','content':'x'},{'role':'tool','content':'receipt'}],[])


def test_reviewed_long_context_scope_and_unknown_admission_veto():
    router=service.configured_router(configured('long-text-conversation'))
    scope=router.catalog['request_scope']
    assert scope['maximum_user_turns']==14
    check_request_scope(scope,scope['id'],[{'role':'user','content':'x'}]*14,[])
    with pytest.raises(ValueError,match='turn bound'):
        check_request_scope(scope,scope['id'],[{'role':'user','content':'x'}]*15,[])
    held=[]
    for variant in router.catalog['variants'].values():
        contract=variant['calibration_contract']
        for family,record in contract['family_evidence'].items():
            for cell in record['profile_evidence']:
                if cell.get('unknown',0):
                    held.append(cell)
                    desc=dict(task_family=family,work_profile=cell['work_profile'],relation='independent',needs_context=False)
                    assert not assess(contract,desc,cell['demand_coverage'][0])['eligible']
    assert held


def test_real_installed_context_service_admits_qualified_transform_without_forecast():
    settings=configured('bounded-text-conversation');args=fixture();args.pop('router');args['settings']=settings
    value,_=bundle.snapshot('bounded-text-conversation',REVISION)
    names=[v['model'] for v in value['variants'].values()]
    args['models']=[dict(name=n,project_id=7,context_window=200000,max_output_tokens=128000,openai_compatible='anthropic' not in n) for n in names]
    args['price_snapshot']['entries']=[dict(model_name=n,input_cost_per_token='.000001',output_cost_per_token='.000002',cache_read_input_token_cost='.0000001',cache_creation_input_token_cost='.00000125') for n in names]
    calls=[]
    def complete(model,messages,**kw):
        calls.append(model)
        desc=dict(operation='transform',task_family='transformation',demand='simple',effort_need='low',input_status='provided',
            needs_context=False,relation='independent',retrieval_source_ids=[],reference_ids=[],reason='Bounded supplied text transformation',
            work_profile=dict(work='transform',reasoning='bounded',evidence='supplied',creativity='none',verification='check'),
            task_continuity=dict(action='independent',task_id=None,summary=''))
        return {'message':{'content':json.dumps(desc)},'finish_reason':'stop'}
    result=service.resolve(request('Convert supplied entries alpha=1 and beta=2 to the requested JSON object.'),complete=complete,**args)
    assert len(calls)==1
    assert result['trace']['selection']['family_qualification']['eligible']
    pin=service.decode_pin(result['pin'],args['signing_key'],project_id=7,user_id=2,settings=settings,now=101)
    assert pin['policy_revision']==service.configured_router(settings).revision
    assert not service.configured_router(settings).catalog['variants']['m09-default']['calibration_contract']['usage_cohorts']
