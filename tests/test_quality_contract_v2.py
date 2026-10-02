"""Prospective absolute-quality and paired-loss requirements, not winner ranking."""
import copy
import hashlib
import json
import pytest
from routing.quality import frontier, validate
from routing import service
from routing.inventory import model_binding
from test_auto_routing_service import request
from test_configured_selection_policy import args_with_policy
from test_v14_installed_bundle import installed
from test_v14_native_fallback import complete

SCOPE={'id':'test-scope','user_turns':1,'has_tools':False,'has_history':False}

def quality(n=400):
    return {'version':2,'minimum_groups':4,'minimum_success_lower_bound':.95,
        'maximum_paired_loss_upper_bound':.02,'cohorts':{d:{
            'source':'Independent prospective test groups','source_sha256':'c'*64,
            'groups':[f'group-{i}' for i in range(n)],'reference_variant':'reference',
            'evaluation_scope':{'request_scope_ids':['test-scope'],'maximum_user_turns':1,'allow_tools':False,'allow_history':False},
            'outcomes':{'cheap':['pass']*n,'reference':['pass']*n}}
            for d in ('simple','standard','deep')}}

def screen(q,variants=('cheap','reference'),**scope):
    validate(q,variants)
    return frontier(q,variants,'simple',request_scope={**SCOPE,**scope})


def test_adequate_paired_evidence_allows_both_choices():
    r=screen(quality());assert set(r['eligible'])=={'cheap','reference'}
    assert r['assessments']['cheap']['paired_loss_upper_bound']<.02
    assert r['assessments']['cheap']['success_lower_bound']>.95
    assert r['quality_guaranteed'] is False and r['approximate_bounds'] is True


def test_equal_success_rates_can_hide_different_paired_loss():
    q=quality(1000)
    for c in q['cohorts'].values():
        c['outcomes']['reference'][:15]=['fail']*15
        c['outcomes']['cheap'][:15]=['fail']*15
    assert 'cheap' in screen(q)['eligible']
    for c in q['cohorts'].values():
        c['outcomes']['cheap']=['pass']*15+['fail']*15+['pass']*970
    r=screen(q)
    assert r['assessments']['cheap']['pass_rate']==r['assessments']['reference']['pass_rate']
    assert r['assessments']['cheap']['status']=='paired_quality_loss_not_supported'
    assert r['eligible']==['reference']


def test_point_estimate_alone_does_not_satisfy_absolute_target():
    q=quality()
    for c in q['cohorts'].values():c['outcomes']['cheap'][:20]=['fail']*20
    row=screen(q)['assessments']['cheap']
    assert row['pass_rate']==.95 and row['status']=='below_absolute_quality_bound'


def test_small_perfect_sample_is_not_paired_equivalence():
    r=screen(quality(100));assert r['assessments']['cheap']['paired_loss_upper_bound']>.02
    assert 'cheap' not in r['eligible']


def test_unknowns_preserved_and_worst_case_against_reference():
    q=quality()
    for c in q['cohorts'].values():c['outcomes']['cheap'][0]='unknown';c['outcomes']['reference'][0]='unknown'
    row=screen(q)['assessments']['cheap'];assert row['unknown']==1 and row['paired_adverse_groups']==1
    for c in q['cohorts'].values():c['outcomes']['reference'][0]='fail'
    assert screen(q)['assessments']['cheap']['paired_adverse_groups']==0


def test_self_reference_needs_absolute_quality_even_without_paired_loss():
    q=quality()
    for c in q['cohorts'].values():c['outcomes']['reference']=['fail']*400
    r=screen(q);assert 'reference' not in r['eligible']
    assert r['assessments']['reference']['paired_loss_upper_bound']==0


@pytest.mark.parametrize('scope',[{'id':'different'},{'user_turns':2},{'has_tools':True},{'has_history':True}])
def test_first_turn_evidence_does_not_transfer_to_other_execution(scope):
    r=screen(quality(),**scope);assert r['eligible']==[]
    assert r['status']=='outside_quality_evaluation_scope'


def test_missing_scope_does_not_authorize_quality():
    assert frontier(quality(),['cheap','reference'],'simple')['eligible']==[]


def test_pool_availability_does_not_change_the_statistical_threshold():
    q=quality();a=screen(q);b=frontier(q,['cheap'],'simple',request_scope=SCOPE)
    assert a['assessments']['cheap']==b['assessments']['cheap']
    assert a['adjusted_comparisons']==b['adjusted_comparisons']==9


def test_no_policy_or_evidence_mutation():
    q=quality();old=copy.deepcopy(q);screen(q);assert q==old


@pytest.mark.parametrize('defect',['missing_reference','unpaired','boolean_version','mixed_legacy','bad_scope','bad_margin','unknown_vote'])
def test_invalid_v2_policy_rejected(defect):
    q=quality();c=q['cohorts']['simple']
    if defect=='missing_reference':c['reference_variant']='not-configured'
    elif defect=='unpaired':c['outcomes']['cheap'].pop()
    elif defect=='boolean_version':q['version']=True
    elif defect=='mixed_legacy':q['maximum_quality_gap']=.1
    elif defect=='bad_scope':c['evaluation_scope']['maximum_user_turns']=True
    elif defect=='bad_margin':q['maximum_paired_loss_upper_bound']=float('nan')
    else:c['outcomes']['cheap'][0]='probably pass'
    with pytest.raises(ValueError):validate(q,['cheap','reference'])


def v2_args(installed):
    args=args_with_policy(installed);policy=args['settings']['calibration_selection_policy'];q=quality()
    for c in q['cohorts'].values():
        c['outcomes']={'customer-small':c['outcomes']['cheap'],'customer-large':c['outcomes']['reference']}
        c['reference_variant']='customer-large'
        c['evaluation_scope']['request_scope_ids']=policy['request_scope_ids']
    policy['quality']=q;policy['revision']='prospective-quality-v2-test'
    return args


def test_gateway_uses_price_between_models_meeting_v2_contract(installed):
    args=v2_args(installed);r=service.resolve(request('Design it'),complete=complete,**args)
    assert r['config']['model_name']=='customer-small'
    assert len(r['trace']['selection']['quality_screen']['eligible'])==2
    assert r['trace']['selection']['economics']['expected_cost'] is None
    assert r['config']['routing_output_mode']=='provider_default'


def test_gateway_preserves_explicit_unmeasured_fallback_for_insufficient_quality(installed):
    args=v2_args(installed)
    for c in args['settings']['calibration_selection_policy']['quality']['cohorts'].values():
        c['outcomes']={v:['unknown']*400 for v in c['outcomes']}
    r=service.resolve(request('Design it'),complete=complete,**args)
    assert r['config']['model_name']=='customer-workhorse'
    assert r['trace']['selection']['reason']=='UNMEASURED_CONFIGURED_FALLBACK'


@pytest.mark.parametrize('role',['user','assistant','tool'])
def test_gateway_cannot_use_first_turn_quality_for_history(installed,role):
    args=v2_args(installed);req=request('Design it');req['messages'].insert(0,{'role':role,'content':'A previous request or result'})
    r=service.resolve(req,complete=complete,**args)
    assert r['trace']['selection']['quality_screen']['status']=='outside_quality_evaluation_scope'
    assert r['trace']['selection']['reason']=='UNMEASURED_CONFIGURED_FALLBACK'


def test_price_cannot_buy_a_failed_v2_quality_screen(installed):
    args=v2_args(installed)
    for c in args['settings']['calibration_selection_policy']['quality']['cohorts'].values():
        c['outcomes']['customer-small']=['fail']*400
    r=service.resolve(request('Design it'),complete=complete,**args)
    assert r['config']['model_name']=='customer-large'


def test_missing_price_is_not_a_free_quality_eligible_choice(installed):
    args=v2_args(installed)
    args['price_snapshot']['entries']=[p for p in args['price_snapshot']['entries'] if p['model_name']!='customer-small']
    r=service.resolve(request('Design it'),complete=complete,**args)
    assert r['config']['model_name']=='customer-large'


@pytest.mark.parametrize('provider',['anthropic','openai','gemini'])
def test_restricted_pool_and_custom_aliases_keep_configured_transport(installed,provider):
    # Names are deployment labels, not transport or quality inference rules.
    value,_,path=installed
    for vid,v in value['variants'].items():v['model']=f'{provider}-alias-{vid}'
    raw=json.dumps(value,sort_keys=True).encode();(path/'text.json').write_bytes(raw)
    manifest=json.loads((path/'manifest.json').read_text())
    manifest['profiles']['text']['sha256']=hashlib.sha256(raw).hexdigest()
    (path/'manifest.json').write_text(json.dumps(manifest))
    args=v2_args(installed);renamed={}
    for model in args['models']:
        old=model['name']
        if old.startswith('customer-'):model['name']=f'{provider}-alias-{old}'
        renamed[old]=model
        assert model['openai_compatible'] is True
    for price in args['price_snapshot']['entries']:price['model_name']=renamed[price['model_name']]['name']
    policy=args['settings']['calibration_selection_policy']
    for v in policy['variants'].values():
        v['native']['model_binding']=model_binding(renamed[v['native']['model_binding']['name']])
    fallback=args['settings']['calibration_coverage_fallback_native']
    fallback['model_binding']=model_binding(renamed[fallback['model_binding']['name']])
    calls=[]
    def classify(model,*a,**kw):
        calls.append(model);return complete(model,*a,**kw)
    r=service.resolve(request('Design it'),complete=classify,**args)
    assert calls and all(m.startswith(provider+'-alias-') for m in calls)
    assert r['config']['model_name']==f'{provider}-alias-customer-small'
    assert r['config']['routing_transport']=='chat_completions'
    assert r['config']['routing_output_mode']=='provider_default'
