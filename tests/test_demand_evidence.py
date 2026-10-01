"""Current action difficulty must not be inferred from the word prove."""
import json
from routing.service import resolve
from routing.v7.task_profile import apply_profile
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
