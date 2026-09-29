from types import SimpleNamespace
import pytest
from routing.v7.state import Session, observe_cache, cache_quote


@pytest.mark.parametrize('write', [None, 0, 20])
def test_missing_write_remains_distinct_from_observed_zero_after_restore(write):
    session=Session('scope');gateway=SimpleNamespace(base='test',project=1)
    variant={'model':'a','effort':None};messages=[{'role':'user','content':'input'}]
    details={'cached_tokens':80}
    if write is not None:details['cache_creation_tokens']=write
    observe_cache(session,gateway,'a',variant,messages,[],1000,
                  {'usage':{'prompt_tokens':100,'prompt_tokens_details':details}},now=1000)
    restored=Session.restore(session.checkpoint())
    quote=cache_quote(restored,gateway,'a',variant,messages,[],1000,now=1010)
    assert quote['observed_write_tokens'] is write
    assert quote['observed_read_tokens']==80
    assert cache_quote(restored,gateway,'a',variant,messages,[],1000,now=1300) is None


def test_v2_imputed_cache_is_discarded_but_task_state_is_retained():
    session=Session('scope');session.intents={'pending':{'status':'awaiting_user'}}
    state=session.checkpoint();state['version']=2
    state['cache']=[{'observed_write_tokens':0}]
    restored=Session.restore(state)
    assert restored.cache==[] and restored.intents==session.intents


def test_unknown_read_is_not_an_observed_miss():
    session=Session('scope');gateway=SimpleNamespace(base='test',project=1)
    variant={'model':'a','effort':None};messages=[{'role':'user','content':'input'}]
    observe_cache(session,gateway,'a',variant,messages,[],1000,
                  {'usage':{'prompt_tokens':100,'prompt_tokens_details':{'cache_creation_tokens':80}}},now=1000)
    quote=cache_quote(Session.restore(session.checkpoint()),gateway,'a',variant,messages,[],1000,now=1010)
    assert quote['observed_read_tokens'] is None
    assert quote['historical_observations']==0
    assert quote['observed_write_tokens']==80
