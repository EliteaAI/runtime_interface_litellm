import copy
from routing.v7.cache_switching import retained_variant


def args():
    scope=dict(family='development',demand='standard',work_profile={'work':'implement'},previous_variant='a',
        switch_variant='b',price_revision='p',input_bytes_min=100,input_bytes_max=10000,ttl_seconds=300)
    policy=dict(scope=scope,method='matched-continuation-groups-v1',enabled=True,passed=True,
        source_sha256='measured',independent_groups=8,minimum_groups=8,saving_interval95_usd=[.01,.04])
    return [policy],dict(task={k:scope[k] for k in ('family','demand','work_profile')},previous='a',chosen='b',
        eligible=['a','b'],size=1000,price_revision='p',quotes={'a':{'cache_evidence':{
            'within_ttl':True,'compatible_prefix':True,'route_identity_matches':True,'observed_read_tokens':100}}})


def test_only_supported_matching_continuation_policy_can_retain_previous():
    policies,kwargs=args()
    assert retained_variant(policies,**kwargs)[0]=='a'
    for change in ('enabled','passed'):
        p=copy.deepcopy(policies);p[0][change]=False
        assert retained_variant(p,**kwargs)==('b',None)
    for change,value in (('price_revision','new'),('size',100000),('eligible',['b'])):
        assert retained_variant(policies,**{**kwargs,change:value})==('b',None)
    for read in (0,None):
        kwargs['quotes']['a']['cache_evidence']['observed_read_tokens']=read
        assert retained_variant(policies,**kwargs)==('b',None)


def test_extension_or_sparse_and_uncertain_saving_cannot_be_enabled():
    policies,kwargs=args()
    policies[0]['scope']['ttl_seconds']=3600
    assert retained_variant(policies,**kwargs)==('b',None)
    policies[0]['scope']['ttl_seconds']=300;policies[0]['saving_interval95_usd']=[-.01,.04]
    assert retained_variant(policies,**kwargs)==('b',None)
