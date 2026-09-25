import copy
import pytest
from routing.v7.qualification import assess

PROFILE=dict(work='implement',reasoning='multi_step',evidence='supplied',creativity='none',verification='check')
DESC=dict(work_profile=PROFILE,task_family='development',needs_context=False,relation='independent')
POLICY=dict(revision='uniform-profile-v1',minimum_templates=3,minimum_validation_templates=3,minimum_wilson_lower=.5)
CELL=dict(work_profile=PROFILE,demand_coverage=['standard'],sample_count=8,independent_templates=8,
          **{'pass':8},fail=0,unknown=0,disagreement=0,wilson95=[.67,1],compatible_contracts=True)


def contract(cell=CELL):
    return dict(revision='v14-fixture',source_sha256='fixture',qualification_policy=POLICY,
        family_evidence={'development':{'profile_evidence':[copy.deepcopy(cell)]}})


@pytest.mark.parametrize('model',['opus-4-7','opus-5-5','sonnet-5','luna','terra','sol'])
def test_model_identity_cannot_change_qualification(model):
    row=contract();row['model']=model
    assert assess(row,DESC,'standard')==assess(contract(),DESC,'standard')
    assert assess(row,DESC,'standard')['eligible']


def test_numeric_clones_do_not_supply_independent_support():
    row=contract({**CELL,'sample_count':300,'independent_templates':1})
    assert assess(row,DESC,'standard')['status']=='insufficient_independent_templates'


def pool():
    return {**CELL,'work_profile':None,'profile_scope':{k:[v] for k,v in PROFILE.items()},
            'pooling_validation':{'passed':True,'independent_templates':3}}


def test_pool_must_be_validated_and_cannot_erase_an_exact_failure():
    row=contract({**CELL,'fail':1,'pass':7})
    row['family_evidence']['development']['validated_pools']=[pool()]
    assert assess(row,DESC,'standard')['status']=='measured_failure'
    row['family_evidence']['development']['profile_evidence']=[]
    assert assess(row,DESC,'standard')['evidence_basis']=='validated_profile_pool'
    row['family_evidence']['development']['validated_pools'][0]['pooling_validation']['passed']=False
    assert assess(row,DESC,'standard')['status']=='unmeasured_work_profile'


@pytest.mark.parametrize('changes,status',[
    ({'compatible_contracts':False},'incompatible_measurement_contract'),
    ({'wilson95':[.2,1]},'insufficient_quality_confidence'),
    ({'unknown':1},'unresolved_measurement'),
    ({'disagreement':1},'disputed_assessment'),
])
def test_provenance_uncertainty_and_disagreement_remain_gates(changes,status):
    assert assess(contract({**CELL,**changes}),DESC,'standard')['status']==status
