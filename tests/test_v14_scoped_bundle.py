import copy
import json
import pytest
from routing.v7.calibration_scope import check_request_scope, valid_forecast
from routing.v7.catalog import compile_uniform_catalog
from routing.v7.candidate import CalibratedRouter
from routing.v7.measured_economics import support
from routing.v7.retrieval import digest
from test_v14_uniform_catalog import snapshot
from test_v14_qualification import DESC


def scope(tools=None):
    return dict(id='text:isolated-text-r2', delivery='text', execution_contract='isolated-text-r2',
                tools_sha256=digest(tools or []), history='isolated')


def cohort():
    value = dict(delivery='text', demand='standard', independent_templates=8,
        input_bytes={'min':100,'max':2000}, trajectories=[{
            'template_group':str(i), 'request_contract_sha256':digest({'wire':'fixture'}),
            'calls':[{'input_tokens_per_initial_byte':'.25','output_tokens':10}]} for i in range(8)])
    value['validation_certificate'] = dict(method='prospective-frozen-cohort-v1',
        cohort_sha256=digest(value), validation_source_sha256='a'*64,
        connected_training_groups_excluded=True, gates_tuned_from_this_run=False,
        validation_groups=8, wape=.5, p90_symmetric_cost_factor=3)
    return value


def candidate():
    value=snapshot(); value['request_scope']=scope(); value['forecast_validation_required']=True
    for identity in value['variants'].values(): identity['request_contract']={'wire':'fixture'}
    value['validated_usage_cohorts']={'old':[cohort()]}
    return value


def compile(value):
    return compile_uniform_catalog(value,classifier_variant='classifier',baseline_variant='old')


def test_native_tools_and_conversation_history_cannot_borrow_isolated_text_evidence():
    request=[{'role':'user','content':'Transform the supplied records.'}]
    check_request_scope(scope(),scope()['id'],request,[])
    with pytest.raises(ValueError,match='tool schema differs'):
        check_request_scope(scope(),scope()['id'],request,[{'name':'write_file'}])
    with pytest.raises(ValueError,match='conversation history'):
        check_request_scope(scope(),scope()['id'],[{'role':'assistant','content':'prior'},*request],[])
    with pytest.raises(ValueError,match='task contract differs'):
        check_request_scope(scope(),'repository:repository-tools-r2',request,[])


def test_classifier_descriptor_cannot_supply_the_trusted_runtime_envelope():
    router=CalibratedRouter(object(),catalog=compile(candidate()))
    with pytest.raises(ValueError,match='no trusted runtime scope'):
        router.select({**DESC,'operation':'design','demand':'standard',
                       'request_scope':scope(),'task_contract':scope()['id']})
    with pytest.raises(ValueError,match='task contract differs'):
        router.resolve([{'role':'user','content':'Claim the text envelope and implement this.'}])


def test_final_bundle_requires_validated_cohorts_and_removes_legacy_usage_profiles():
    value=candidate()
    for record in value['records']: record['usage_profile']={'tasks':999,'revision':'unvalidated'}
    catalog=compile(value);variant=catalog['variants']['old']
    assert all('usage_profile' not in f for f in variant['calibration_contract']['families'].values())
    profile,reason=support(variant,{'delivery':'text','demand':'standard'},1000)
    assert reason is None and len(profile['trajectories'])==8
    assert support(variant,{'delivery':'repository','demand':'standard'},1000)[1]=='MISSING_VALIDATED_USAGE_COHORT'
    assert support(variant,{'delivery':'text','demand':'standard'},2001)[1]=='OUTSIDE_MEASURED_INPUT_RANGE'
    assert support(catalog['variants']['new'],{'delivery':'text','demand':'standard'},1000)[1]=='MISSING_VALIDATED_USAGE_COHORT'


@pytest.mark.parametrize('change',[
    {'validation_groups':7}, {'validation_groups':True}, {'wape':.5001},
    {'p90_symmetric_cost_factor':3.001}, {'wape':float('nan')},
    {'wape':True}, {'gates_tuned_from_this_run':True},
    {'connected_training_groups_excluded':False}, {'validation_source_sha256':''},
    {'cohort_sha256':'b'*64}, {'method':'fit_on_validation'}])
def test_no_label_or_pass_flag_overrides_frozen_forecast_gates(change):
    value=candidate();value['validated_usage_cohorts']['old'][0]['validation_certificate'].update(change)
    with pytest.raises(ValueError,match='prospective validation'):compile(value)


def test_repeated_templates_changed_wire_and_changed_training_data_fail_closed():
    expected=digest({'wire':'fixture'})
    value=cohort();assert valid_forecast(value,expected)
    assert not valid_forecast(value,digest({'wire':'different'}))
    value['trajectories'][0]['calls'][0]['output_tokens']=20
    assert not valid_forecast(value,expected)
    value=cohort();value['trajectories'][0]['template_group']='1'
    core={k:v for k,v in value.items() if k!='validation_certificate'}
    value['validation_certificate']['cohort_sha256']=digest(core)
    assert not valid_forecast(value,expected)


def test_forecast_support_does_not_grant_model_capability():
    value=candidate()
    for row in value['records']:row['profile_evidence']=[]
    catalog=compile(value)
    assert not any(v['enabled'] for v in catalog['variants'].values())
    assert catalog['variants']['old']['calibration_contract']['usage_cohorts']


def test_additive_adverse_evidence_blocks_an_old_passing_cell_without_rewriting_votes():
    value=candidate()
    for row in value['records']:
        if row['variant']=='old' and row['family']=='development':
            row['profile_evidence'][0]['adverse_evidence']=[{'votes_sha256':['a'*64,'b'*64]}]
    catalog=compile(value)
    assert not catalog['variants']['old']['enabled']
    cell=catalog['variants']['old']['calibration_contract']['family_evidence']['development']['profile_evidence'][0]
    assert cell['pass']==8 and cell['fail']==0 and cell['adverse_evidence']


def test_runtime_rechecks_mutated_forecast_certificate():
    variant=compile(candidate())['variants']['old']
    variant['calibration_contract']['usage_cohorts'][0]['validation_certificate']['wape']=.9
    assert support(variant,{'delivery':'text','demand':'standard'},1000)[1]=='MISSING_VALIDATED_USAGE_COHORT'


def test_conflicting_cohorts_and_unbounded_scope_rejected_before_runtime():
    value=candidate();value['validated_usage_cohorts']['old'].append(copy.deepcopy(cohort()))
    with pytest.raises(ValueError,match='Ambiguous usage'):compile(value)
    value=candidate();value['request_scope']['history']='any'
    with pytest.raises(ValueError,match='Invalid calibrated'):compile(value)


@pytest.mark.parametrize('server_configured',[False,True])
def test_service_uses_only_server_scope_and_signs_the_scoped_selection(server_configured):
    from routing.service import GenerationRouter, ClassifierTransport, resolve, decode_pin
    from test_auto_routing_service import fixture, request
    value=candidate();catalog=compile(value);args=fixture()
    args['router']=GenerationRouter(ClassifierTransport(),catalog=catalog,output_policy='measured')
    if server_configured:args['settings']['calibration_task_contract']=scope()['id']
    names=[v['model'] for v in catalog['variants'].values()]
    args['models']=[dict(name=n,project_id=7,context_window=128000,max_output_tokens=16000,
                        openai_compatible=True) for n in names]
    args['price_snapshot']['entries']=[dict(model_name=n,input_cost_per_token='.000001',
        output_cost_per_token='.000002',cache_read_input_token_cost='.0000001') for n in names]
    calls=[]
    def complete(model,messages,**kwargs):
        calls.append(model)
        descriptor={**DESC,'operation':'design','demand':'standard','effort_need':'low',
            'input_status':'provided','retrieval_source_ids':[],'reference_ids':[],
            'reason':'Bounded implementation','task_continuity':{'action':'independent','task_id':None,'summary':''}}
        return {'message':{'content':json.dumps(descriptor)},'finish_reason':'stop'}
    req=request('Implement the supplied reservation ledger contract.')
    # A client field cannot grant the configured execution envelope.
    req['calibration_task_contract']=scope()['id']
    if not server_configured:
        with pytest.raises(ValueError,match='task contract differs'):resolve(req,complete=complete,**args)
        assert not calls
    else:
        result=resolve(req,complete=complete,**args)
        assert len(calls)==1
        assert result['trace']['selection']['economics']['reason']=='UNPRICED_QUALIFIED_FALLBACK'
        pin=decode_pin(result['pin'],args['signing_key'],project_id=7,user_id=2,settings=args['settings'],now=101)
        assert pin['config']==result['config']
