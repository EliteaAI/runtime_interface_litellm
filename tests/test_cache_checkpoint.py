"""Cache evidence is advisory, bounded, restart-safe, and replay-idempotent."""
import copy
from types import SimpleNamespace

import pytest

from routing.v7.state import Session, observe_cache, cache_quote


def fixture():
    return Session('scope'), SimpleNamespace(base='stage', project=7), {'model':'a','effort':'high'}, [{'role':'user','content':'task'}]


def record(args, at=1000, read=80):
    session,gateway,variant,messages=args
    observe_cache(session,gateway,'a:high',variant,messages,[],1000,
                  {'usage':{'prompt_tokens':100,'prompt_tokens_details':{'cached_tokens':read}}},now=at)


def quote(args, at=1010):
    session,gateway,variant,messages=args
    return cache_quote(session,gateway,'a:high',variant,messages,[],1000,now=at)


def test_restart_preserves_observed_history_without_extending_expiry():
    args=fixture()
    for i in range(3):
        record(args,1000+i)
    before=quote(args)
    restored=(Session.restore(args[0].checkpoint()),*args[1:])
    assert quote(restored)==before
    assert quote(restored,1183) is None


def test_replaying_same_receipt_does_not_create_additional_hits():
    args=fixture();record(args)
    record(args)
    assert quote(args)['historical_observations']==1
    args=(Session.restore(args[0].checkpoint()),*args[1:])
    record(args)
    assert quote(args)['historical_observations']==1
    record(args,1001)
    assert quote(args)['historical_observations']==2


def test_legacy_checkpoint_remains_cold():
    args=fixture();record(args)
    checkpoint=args[0].checkpoint();checkpoint['version']=1;checkpoint.pop('cache',None)
    assert Session.restore(checkpoint).cache==[]


def test_edit_invalidates_restored_cache():
    args=fixture();args[0].prepare(args[3]);record(args)
    restored=Session.restore(args[0].checkpoint())
    restored.prepare([{'role':'user','content':'different'}])
    assert restored.cache==[]


@pytest.mark.parametrize('at',[float('nan'),float('inf'),True,-1])
def test_invalid_time_is_not_cache_evidence(at):
    args=fixture();record(args,at)
    assert args[0].cache==[]


def test_serialized_cache_has_an_independent_size_bound():
    args=fixture()
    for i in range(100):
        record(args,1000+i)
    checkpoint=args[0].checkpoint()
    assert len(checkpoint['cache'])<=64
    assert len(__import__('json').dumps(checkpoint['cache']).encode())<=64000
    assert len(Session.restore(checkpoint).cache)==len(checkpoint['cache'])


def test_malformed_optional_cache_does_not_break_existing_intent_state():
    args=fixture();record(args)
    checkpoint=args[0].checkpoint()
    checkpoint['cache'][0]['observed_at']='yesterday'
    restored=Session.restore(checkpoint)
    assert restored.cache==[] and restored.id=='scope'


def test_long_call_does_not_refresh_old_prefix_at_completion(monkeypatch):
    from routing.checkpoint import apply_observation
    from routing.v7.state import message_digest
    from routing.v7.retrieval import digest
    from routing import checkpoint
    monkeypatch.setattr(checkpoint.time,'time',lambda:1300)
    session,gateway,variant,messages=fixture()
    answer={'role':'assistant','content':'result'}
    state={'input_count':1,'input_digest':digest(messages),
           'decision':{'selection':{'variant':'a:high'},'budget':{'completion_cap':1000}}}
    receipt={'message_digest':message_digest(answer),'finish_reason':'stop','message_index':1,
             'request_started_at':1000,'completed_at':1299,
             'usage':{'prompt_tokens':100,'prompt_tokens_details':{'cached_tokens':80}}}
    apply_observation(session,state,receipt,messages+[answer,{'role':'user','content':'next'}],
                      gateway,{'variants':{'a:high':variant}},[],'policy')
    assert not session.cache


def test_optional_cache_does_not_overflow_existing_task_checkpoint(monkeypatch):
    import json
    from routing import checkpoint
    args=fixture();record(args)
    decision={'selection':{'variant':'a:high','model':'a','effort':'high'}}
    kwargs=dict(scope_id='scope',project_id=7,user_id=2,gate_revision='g1',policy_revision='p1')
    value=checkpoint.state_value(args[0],decision,args[3],**kwargs)
    value['checkpoint']['cache']=[]
    monkeypatch.setattr(checkpoint,'MAX_CHECKPOINT_BYTES',len(json.dumps(value).encode()))
    result=checkpoint.state_value(args[0],decision,args[3],**kwargs)
    assert result['checkpoint']['cache']==[]
    assert args[0].cache  # Serialization does not mutate the live session.
