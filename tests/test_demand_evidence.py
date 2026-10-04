"""Current action difficulty must not be inferred from the word prove."""
import json
from routing.service import resolve
from routing.v7.task_profile import apply_profile, PROFILE_SEMANTICS, PROFILE_REVIEW_GUIDE
import pytest
from test_auto_routing_service import fixture,request
from test_v12_algorithm import DESC,PROFILE


def test_small_proof_does_not_force_deep():
    d=apply_profile(DESC,{**PROFILE,'verification':'prove'})
    assert d['demand']=='simple' and d['profile_demand_floor']=='simple'
    assert not d['profile_demand_adjustment']['raised']


def test_multistep_proof_is_standard_and_raw_deep_stays_deep():
    p={**PROFILE,'verification':'prove','reasoning':'multi_step'}
    assert apply_profile(DESC,p)['demand']=='standard'
    assert apply_profile({**DESC,'demand':'deep'},p)['demand']=='deep'


def test_short_coupled_guarantees_keep_deep_floor():
    d=apply_profile(DESC,{**PROFILE,'reasoning':'interacting_constraints'})
    assert d['demand']=='deep'
    assert d['profile_demand_adjustment']=={'revision':'reasoning-evidence-2','input_demand':'simple','floor_reasons':['interacting_constraints'],'raised':True}


def test_trace_separates_classifier_floor_and_final_bucket():
    def complete(*a,**k):
        return {'message':{'content':json.dumps({**DESC,'work_profile':{**PROFILE,'reasoning':'multi_step'}})},'finish_reason':'stop'}
    r=resolve(request('Create a plan from these supplied constraints.'),complete=complete,**fixture())
    d=r['trace']['difficulty']
    assert d['raw_classifier_demand']=='simple' and d['policy_floor']=='standard'
    assert d['final_demand']=='standard' and d['source']=='classifier'


def test_invalid_classifier_does_not_look_like_a_measured_deep_label():
    r=resolve(request('Analyze the supplied design.'),complete=lambda *a,**k:{'message':{'content':'invalid'},'finish_reason':'stop'},**fixture())
    d=r['trace']['difficulty'];assert d['raw_classifier_demand'] is None
    assert d['source']=='conservative_default'


@pytest.mark.parametrize('verification', ['none', 'check', 'prove'])
def test_actual_classifier_receives_shared_semantics_and_preserves_independent_axes(verification):
    calls=[]
    profile={**PROFILE,'verification':verification,'reasoning':'bounded'}
    def complete(model,messages,**kwargs):
        calls.append(messages)
        return {'message':{'content':json.dumps({**DESC,'work_profile':profile})},'finish_reason':'stop'}
    result=resolve(request('Compute the value from the supplied table.'),complete=complete,**fixture())
    assert len(calls)==1
    assert calls[0][0]['role']=='system'
    assert calls[0][0]['content'].count(PROFILE_SEMANTICS)==1
    assert calls[0][0]['content'].count(PROFILE_REVIEW_GUIDE)==1
    descriptor=result['trace']['descriptor']
    assert descriptor['work_profile']==profile
    assert descriptor['demand']=='simple'
    assert not descriptor['profile_demand_adjustment']['raised']


@pytest.mark.parametrize('evidence', ['ordinary', 'supplied'])
@pytest.mark.parametrize('verification', ['none', 'check'])
def test_provided_availability_does_not_overwrite_profile_or_inflate_local_work(evidence, verification):
    profile={**PROFILE,'work':'implement','evidence':evidence,'verification':verification,
             'reasoning':'bounded','creativity':'none'}
    response={**DESC,'operation':'design','task_family':'code','input_status':'provided',
              'work_profile':profile,'demand':'simple'}
    result=resolve(request('Supply the requested local function.'),
        complete=lambda *a,**k:{'message':{'content':json.dumps(response)},'finish_reason':'stop'},**fixture())
    assert result['trace']['descriptor']['work_profile']==profile
    assert result['trace']['descriptor']['demand']=='simple'
    assert result['trace']['descriptor']['input_status']=='provided'


def test_both_classifier_lanes_receive_same_review_guide():
    from routing.v7.candidate import FamilyClassifier
    from routing.v7.catalog import calibration_candidate
    classifier=FamilyClassifier(object(), 'm09-default', calibration_candidate())
    for lane in (classifier.text,classifier.tools):
        assert lane.system_prompt.count(PROFILE_REVIEW_GUIDE)==1
        assert lane.system_prompt.count(PROFILE_SEMANTICS)==1


def test_prompt_revision_changes_router_identity(monkeypatch):
    from routing.v7 import candidate
    before=candidate.CalibratedRouter(object()).revision
    monkeypatch.setattr(candidate, 'PROFILE_PROMPT', candidate.PROFILE_PROMPT+'\nChanged guide.')
    after=candidate.CalibratedRouter(object()).revision
    assert after != before


@pytest.mark.parametrize('evidence,floor', [('supplied', 'simple'), ('conflicting', 'standard')])
def test_conflict_observation_does_not_conflate_raw_demand_and_policy_floor(evidence, floor):
    profile={**PROFILE, 'work':'explain', 'reasoning':'bounded', 'evidence':evidence,
             'creativity':'none', 'verification':'none'}
    descriptor={**DESC, 'demand':'simple', 'work_profile':profile}
    result=resolve(request('Describe the mismatch between the supplied totals.'),
        complete=lambda *a,**k:{'message':{'content':json.dumps(descriptor)},'finish_reason':'stop'}, **fixture())
    trace=result['trace']
    assert trace['difficulty']['raw_classifier_demand']=='simple'
    assert trace['difficulty']['policy_floor']==floor
    assert trace['difficulty']['final_demand']==floor
    assert trace['descriptor']['work_profile']==profile
