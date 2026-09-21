"""Behavioral boundaries for profiles, mechanical bypass and expenditure ranking."""
import copy
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from routing.pricing import PriceBook
from routing.service import resolve
from routing.v7.economics import rank
from routing.v7.mechanical import match_mechanical
from routing.v7.state import Session, observe_cache, cache_quote
from routing.v7.task_profile import apply_profile
from test_auto_routing_service import fixture, request


PROFILE = dict(work='create', reasoning='bounded', evidence='ordinary', creativity='open', verification='none')
DESC = dict(operation='creative', task_family='content_creation', demand='simple',
            effort_need='low', relation='independent', reference_ids=[], needs_context=False,
            reason='Create requested content', input_status='provided', retrieval_source_ids=[])


@pytest.mark.parametrize('change,expected', [({}, 'simple'), ({'reasoning':'multi_step'}, 'standard'),
    ({'reasoning':'interacting_constraints'}, 'deep'), ({'verification':'prove'}, 'deep'),
    ({'evidence':'conflicting'}, 'standard')])
def test_properties_raise_only_the_required_floor(change, expected):
    assert apply_profile(DESC, {**PROFILE, **change})['demand'] == expected
    assert apply_profile({**DESC, 'demand':'deep'}, {**PROFILE, **change})['demand'] == 'deep'


def test_new_profile_uses_one_classifier_call_and_is_in_trace():
    calls = []
    def complete(*args, **kwargs):
        calls.append(args)
        return {'message':{'content':json.dumps({**DESC, 'work_profile':PROFILE})}, 'finish_reason':'stop'}
    result = resolve(request('Create a friendly announcement for our fictional release.'), complete=complete, **fixture())
    assert len(calls) == 1
    assert result['trace']['descriptor']['work_profile'] == PROFILE
    assert result['trace']['descriptor']['demand'] == 'simple'


def test_invalid_profile_abstains_without_second_classifier_call():
    calls = []
    def complete(*a, **k):
        calls.append(1)
        return {'message':{'content':json.dumps({**DESC, 'work_profile':{**PROFILE,'work':'arbitrary'}})}, 'finish_reason':'stop'}
    result = resolve(request('Write an announcement.'), complete=complete, **fixture())
    assert len(calls) == 1
    assert result['trace']['descriptor']['operation'] == 'other'
    assert result['action'] == 'generate'  # The model still answers.


@pytest.mark.parametrize('prompt', ['Convert "Hello World" to uppercase.',
    'Change "ABC-123" to lowercase!', 'Sort integers ascending: 3, -7, 2, 2.'])
def test_complete_mechanical_task_skips_classifier(prompt):
    result = resolve(request(prompt), complete=lambda *a, **k:pytest.fail('Mechanical task classified'), **fixture())
    assert not result['trace']['classifier']['called']
    assert result['action'] == 'generate'


@pytest.mark.parametrize('prompt', ['Convert "abc" to uppercase. Then design crash recovery.',
    'Sort integers ascending: 1,2\nAlso prove the complexity.', 'Sort integers ascending: 1000000,2',
    'Sort integers ascending: '+','.join(str(i) for i in range(33)),
    'Convert "αβγ" to uppercase.', 'What is the weather today?', 'Who is Lionel Messi?',
    'Create a distributed Rust architecture.', 'Convert the earlier artifact to uppercase.'])
def test_unbounded_mixed_or_contextual_task_abstains(prompt):
    assert match_mechanical(prompt) is None


def test_agent_requirements_prevent_mechanical_bypass():
    req = request('Convert "abc" to uppercase.')
    instructions='For every request, analyze the supplied code for race conditions.'
    req['runtime_context']={'active_instructions':{'text':instructions,'revision':'r1','total_chars':len(instructions),'truncated':False}}
    calls=[]
    def complete(*a,**k):
        calls.append(1)
        return {'message':{'content':json.dumps(DESC)},'finish_reason':'stop'}
    resolve(req, complete=complete, **fixture())
    assert len(calls)==1


def economics_fixture():
    usage = dict(tasks=4, input_bytes={'min':1,'max':100000}, input_tokens_per_byte=.25,
                 output_tokens={'p90':100}, calls_per_task={'p90':1})
    variants={v:dict(model=v, effort=None, cache_write_mode='ordinary_input',
                    calibration_contract={'families':{'code':{'usage_profile':copy.deepcopy(usage)}}}) for v in ('a','b')}
    prices=PriceBook([dict(model_name=v,input_cost_per_token='0.000001',output_cost_per_token=p,
                          cache_read_input_token_cost='0.0000001') for v,p in [('a','.000002'),('b','.000001')]],source_revision='test')
    gateway=SimpleNamespace(prices=prices,output_caps={'a':1000,'b':2000})
    selection={'reason':'qualified','family_qualification':{'family':'code'},
               'candidates':[{'variant':v,'eligible':True} for v in variants]}
    return selection,[{'role':'user','content':'Implement the supplied design.'}],{'variants':variants},gateway,Session()


def ranked(args, **kwargs):
    selection,messages,catalog,gateway,session=args
    return rank(selection,messages,catalog,gateway=gateway,session=session,cap=1000,**kwargs)


def test_expenditure_ranking_does_not_treat_output_limit_as_expected_usage():
    args=economics_fixture()
    result=ranked(args)
    assert result['variant']=='b' and result['economics']['forecast_comparable']
    assert result['economics']['output_range']==[0,2000]
    assert result['economics']['quotes']['b']['usage_forecast']['output_tokens']==100
    assert Decimal(result['economics']['quotes']['b']['ranking_usd']) < Decimal(result['economics']['quotes']['b']['upper_usd'])


@pytest.mark.parametrize('missing', ['absent','out_of_range','small_support'])
def test_missing_usage_for_one_candidate_falls_back_for_all(missing):
    args=economics_fixture();cell=args[2]['variants']['a']['calibration_contract']['families']['code']
    if missing=='absent':cell.pop('usage_profile')
    elif missing=='out_of_range':cell['usage_profile']['input_bytes']['max']=1
    else:cell['usage_profile']['tasks']=2
    result=ranked(args)
    assert not result['economics']['forecast_comparable']
    assert all(q['usage_forecast'] is None for q in result['economics']['quotes'].values())


def test_no_arbitrary_five_percent_premium_for_previous_model():
    args=economics_fixture()
    args[3].prices=PriceBook([dict(model_name=v,input_cost_per_token='0',output_cost_per_token=p,
                                 cache_read_input_token_cost='0') for v,p in [('a','.00000104'),('b','.000001')]],source_revision='test')
    assert ranked(args, previous='a')['variant']=='b'


def test_observed_cache_mix_counts_misses_and_keeps_cold_bound():
    args=economics_fixture();_,messages,catalog,gateway,session=args
    for read in (0,100,100):
        observe_cache(session,gateway,'a',catalog['variants']['a'],messages,None,1000,
                      {'usage':{'prompt_tokens':200,'prompt_tokens_details':{'cached_tokens':read}}})
    result=ranked(args);quote=result['economics']['quotes']['a']
    assert quote['cache_observed_mix']=={'hits':2,'observations':3,'future_hit_probability_known':False}
    assert quote['cache_evidence']['hit_probability_range']==[0,1]
    assert Decimal(quote['upper_usd'])>Decimal(quote['ranking_usd'])
    altered=[{'role':'user','content':'An unrelated edited task'}]
    assert cache_quote(session,gateway,'a',catalog['variants']['a'],altered,None,1000) is None
    assert cache_quote(session,gateway,'a',{**catalog['variants']['a'],'effort':'high'},messages,None,1000) is None
    assert cache_quote(session,gateway,'a',catalog['variants']['a'],messages,None,1000,now=10**12) is None


def test_unpriced_forecast_tier_falls_back_without_free_cost():
    args=economics_fixture();actual=args[3].prices
    class MissingLowTier:
        revision=actual.revision
        def quote(self,model,tokens):
            return {'usd':None} if 0<tokens.output<200 else actual.quote(model,tokens)
    args[3].prices=MissingLowTier()
    result=ranked(args)
    assert result['economics']['forecast_fallback']=='UNPRICED_FORECAST_TIER'
    assert not result['economics']['forecast_comparable']
    assert all(Decimal(q['ranking_usd'])>0 for q in result['economics']['quotes'].values())


def test_unreported_cache_usage_is_not_counted_as_a_miss():
    _,messages,catalog,gateway,session=economics_fixture()
    observe_cache(session,gateway,'a',catalog['variants']['a'],messages,None,1000,
                  {'usage':{'prompt_tokens':200,'completion_tokens':50}})
    assert not session.cache


@pytest.mark.parametrize('value',[None,False,'',-1])
def test_invalid_or_null_cache_usage_is_not_an_observed_miss(value):
    _,messages,catalog,gateway,session=economics_fixture()
    observe_cache(session,gateway,'a',catalog['variants']['a'],messages,None,1000,
                  {'usage':{'prompt_tokens':200,'prompt_tokens_details':{'cached_tokens':value}}})
    assert not session.cache
