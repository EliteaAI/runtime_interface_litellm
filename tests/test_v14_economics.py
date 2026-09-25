import copy
from decimal import Decimal
from types import SimpleNamespace
from routing.pricing import PriceBook
from routing.v7.economics import rank
from routing.v7.measured_economics import estimate
from routing.v7.state import Session

PROFILE={'work':'implement','reasoning':'multi_step','evidence':'supplied','creativity':'none','verification':'check'}


def fixture():
    usage={'revision':'v14-usage-trajectories-1','profiles':[{
        'work_profile':PROFILE,'demand':'standard','minimum_templates':3,'independent_templates':3,
        'input_bytes':{'min':100,'max':10000},'trajectories':[
            {'template_group':str(i),'calls':[{'input_tokens_per_initial_byte':'.25','output_tokens':100},
                 {'input_tokens_per_initial_byte':'.5','output_tokens':50}]} for i in range(3)]}]}
    variants={v:dict(model=v,effort=None,cache_write_mode='ordinary_input',
        calibration_contract={'families':{'development':{'usage_profile':copy.deepcopy(usage)}}}) for v in ('a','b')}
    prices=PriceBook([dict(model_name=v,input_cost_per_token='.000001',output_cost_per_token=p,
                         cache_read_input_token_cost='.0000001') for v,p in [('a','.000002'),('b','.000001')]],source_revision='test')
    gateway=SimpleNamespace(prices=prices,output_caps={'a':1000,'b':100000},generation_input_bytes=1000)
    selection={'reason':'qualified','variant':'a','economic_task':{'family':'development','demand':'standard','work_profile':PROFILE},
               'candidates':[{'variant':v,'eligible':True} for v in variants]}
    return selection,{'variants':variants,'uniform_qualification':True,'baseline':'a'},gateway


def ranked(args,**kw):
    selection,catalog,gateway=args
    return rank(selection,[{'role':'user','content':'Fix the supplied repository.'}],catalog,
                session=Session(),gateway=gateway,cap=1000,**kw)


def test_output_capacity_and_internal_message_metadata_do_not_distort_cost():
    args=fixture();result=ranked(args)
    assert result['variant']=='b' and result['economics']['forecast_comparable']
    assert Decimal(result['economics']['quotes']['b']['ranking_usd'])==Decimal('.0009')
    args[2].output_caps['b']=200000
    assert ranked(args)['economics']['quotes']['b']['ranking_usd']==result['economics']['quotes']['b']['ranking_usd']
    altered=rank(args[0],[{'role':'user','content':'x','internal_metadata':'x'*100000}],args[1],
                 session=Session(),gateway=args[2],cap=1000)
    assert altered['economics']['input_size_proxy']==1000
    assert altered['economics']['quotes']['b']['ranking_usd']==result['economics']['quotes']['b']['ranking_usd']


def test_missing_competitor_uses_explicit_fallback_not_full_capacity():
    args=fixture();args[1]['variants']['a']['calibration_contract']['families']['development'].clear()
    result=ranked(args)
    assert result['variant']=='a' and result['reason']=='UNPRICED_QUALIFIED_FALLBACK'
    assert result['economics']['missing_support']=={'a':'MISSING_USAGE_PROFILE'}
    assert 'a' not in result['economics']['quotes']
    assert ranked(args,previous='b')['variant']=='b'


def test_tool_calls_priced_once_and_template_clones_do_not_dominate():
    args=fixture();variant=args[1]['variants']['b'];profile=variant['calibration_contract']['families']['development']['usage_profile']['profiles'][0]
    profile['trajectories'][0]['calls'][1]['output_tokens']=500
    base=estimate(args[2].prices,variant,profile,1000,None)
    profile['trajectories']+=copy.deepcopy([profile['trajectories'][0]]*10)
    assert estimate(args[2].prices,variant,profile,1000,None)['ranking_usd']==base['ranking_usd']


def test_warm_first_call_does_not_invent_later_hits_or_change_ranking():
    args=fixture();variant=args[1]['variants']['b'];profile=variant['calibration_contract']['families']['development']['usage_profile']['profiles'][0]
    cold=estimate(args[2].prices,variant,profile,1000,None)
    warm=estimate(args[2].prices,variant,profile,1000,{'read_token_upper_bound':250})
    assert warm['ranking_usd']==cold['ranking_usd']
    assert Decimal(warm['warm_scenario_usd'])<Decimal(warm['expected_cold_usd'])
    assert not warm['cache_ranking_enabled']


def test_out_of_range_and_changed_output_contract_abstain_from_forecast():
    args=fixture();args[2].generation_input_bytes=10001
    assert set(ranked(args)['economics']['missing_support'].values())=={'OUTSIDE_MEASURED_INPUT_RANGE'}
    args=fixture();args[2].output_caps['b']=90
    assert ranked(args)['economics']['missing_support']['b']=='OUTPUT_CONTRACT_MISMATCH'
