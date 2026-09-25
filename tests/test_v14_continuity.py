"""Pending-task dependencies across turns, tools, edits and signed-state restore."""
import copy
import json
from types import SimpleNamespace

import pytest

from routing.service import GenerationRouter
from routing.v7.retrieval import ContextIndex, build
from routing.v7.state import Session
from routing.v7.task_continuity import validate


def message(role, text, **fields):
    return dict(role=role, content=text, **fields)


def link(action='defer', task_id=None, summary='Implement crash recovery for the worker.'):
    return dict(action=action, task_id=task_id, summary=summary)


def record(session, messages, relation, revision='policy'):
    session.prepare(messages)
    answer=message('assistant','The requested revision is recorded.')
    session.record_pending(messages, {'descriptor':{'task_continuity':relation}},
                           {'message':answer,'finish_reason':'stop'},revision)
    messages.append(answer)
    return next(reversed(session.intents),None)


@pytest.mark.parametrize('detours',[1,8,16,32])
def test_semantic_deferral_and_amendments_survive_detours_and_restore(detours):
    session=Session()
    messages=[message('user','When I give the signal later, implement recovery. For now, outline the worker.')]
    target=record(session,messages,link())
    messages.append(message('user','Also store a payload digest; reject reuse with a different payload.'))
    record(session,messages,link('amend',target,''))
    for i in range(detours):
        messages += [message('user',f'Unrelated note {i}'),message('assistant','Done.')]
    messages.append(message('user','Proceed'))
    session=Session.restore(session.checkpoint())
    session.prepare(messages)
    pending=session.pending(messages,'policy')
    assert pending['action']=='resume'
    assert pending['intent']['source_ids']==['m0','m1','m2','m3']
    before=copy.deepcopy(messages)
    view,_=build(messages,None,session.index,force_sources=pending['intent']['source_ids'])
    assert view['allowed_reference_ids']==['m0','m1','m2','m3']
    assert 'payload digest' in json.dumps(view)
    assert messages==before


def test_new_task_does_not_consume_deferred_task_or_inherit_wait():
    session=Session();messages=[message('user','Outline a worker; wait for my confirmation to implement it.')]
    target=record(session,messages,link())
    messages.append(message('user','Implement a standalone Python settings validator now.'))
    record(session,messages,link('independent',None,''))
    assert session.intents[target]['status']=='awaiting_user'
    assert session.intents[target]['source_ids']==['m0','m1']


@pytest.mark.parametrize('action',['cancel','replace'])
def test_semantic_cancel_and_supersession_never_revive_old_task(action):
    session=Session();messages=[message('user','Defer the worker implementation.')]
    old=record(session,messages,link())
    messages.append(message('user','Withdraw that worker. Defer the exporter instead.'))
    new=record(session,messages,link(action,old,'Implement the exporter.' if action=='replace' else ''))
    assert session.intents[old]['status']==('cancelled' if action=='cancel' else 'superseded')
    messages.append(message('user','Go'));session.prepare(messages)
    pending=session.pending(messages,'policy')
    if action=='cancel':assert pending['action']=='clarify'
    else:assert pending['intent']['id']==new and new!=old


def test_edit_and_policy_revision_invalidate_semantic_index():
    session=Session();messages=[message('user','Defer recovery.')]
    record(session,messages,link());messages.append(message('user','Go'))
    assert session.pending_index(messages,'new-policy')==([],0)
    messages[0]['content']='Different task';session.prepare(messages)
    assert session.pending_index(messages,'policy')==([],0)
    assert session.pending(messages,'policy')['action']=='clarify'


def test_tool_text_and_incomplete_answers_do_not_register_tasks():
    session=Session()
    decision={'descriptor':{'task_continuity':link()}}
    for role,reason in [('tool','stop'),('assistant','stop'),('user','length'),('user','tool_calls')]:
        session.record_pending([message(role,'Wait for my Go to implement it.')],decision,
            {'message':message('assistant','Defer it.'),'finish_reason':reason},'policy')
    assert session.intents=={}


def test_forced_task_sources_include_complete_tool_dependency_groups():
    messages=[message('user','Diagnose the ledger and defer the repair.'),
              message('assistant','',tool_calls=[{'id':'read1','name':'read','arguments':{}}]),
              message('tool','Released-key mismatch defect.',tool_call_id='read1'),
              message('assistant','Repair is pending.')]
    for i in range(16):messages += [message('user',f'Joke {i}'),message('assistant','Done.')]
    messages.append(message('user','Go'))
    view,_=build(messages,None,ContextIndex(),force_sources=['m0','m3'])
    assert view['allowed_reference_ids']==['m0','m1','m2','m3']
    assert view['evidence_coverage']['tool_entries_visible']==1


@pytest.mark.parametrize('value',[
    link('amend','forged',''),link('resume','forged',''),link('cancel','forged',''),
    link('independent','known',''),link('defer',None,''),link('unknown'),
])
def test_task_link_is_validated_against_emitted_index(value):
    with pytest.raises(ValueError):validate(value,{'allowed_reference_ids':[], 'pending_tasks':[]})


def test_semantic_resume_requires_full_known_dependency_coverage():
    view={'pending_tasks':[{'id':'known','source_ids':['m0','m1','m2','m3']}],
          'allowed_reference_ids':['m0','m1']}
    with pytest.raises(ValueError,match='SOURCE_COVERAGE'):validate(link('resume','known',''),view)
    view['allowed_reference_ids']+=['m2','m3']
    assert validate(link('resume','known',''),view)['action']=='resume'


def test_real_router_single_call_receives_pending_index_and_keeps_typed_link():
    calls=[]
    def complete(model,packet,**kwargs):
        calls.append(packet)
        return {'message':{'content':json.dumps(dict(operation='analysis',demand='standard',
            relation='independent',reference_ids=[],needs_context=False,reason='Prepare the requested outline.',
            effort_need='medium',task_family='architecture',input_status='provided',retrieval_source_ids=[],
            task_continuity=link()))},'finish_reason':'stop'}
    router=GenerationRouter(SimpleNamespace(complete=complete))
    session=Session();messages=[message('user','When I give the signal later, implement recovery. First outline it.')]
    decision=router.resolve(messages,session=session,mode='llm')
    assert len(calls)==1
    assert 'task_continuity' in calls[0][0]['content']
    assert decision['descriptor']['task_continuity']==link()
    record(session,messages,decision['descriptor']['task_continuity'],router.revision)
    messages.append(message('user','Go'));session.prepare(messages)
    packets=[]
    def resume(model,packet,**kwargs):
        view=json.loads(packet[-1]['content']);packets.append(view)
        target=view['pending_tasks'][0]['id']
        return {'message':{'content':json.dumps(dict(operation='analysis',demand='deep',
            relation='return',reference_ids=['m0','m1'],needs_context=False,reason='Implement the agreed recovery.',
            effort_need='high',task_family='development',input_status='provided',retrieval_source_ids=[],
            task_continuity=link('resume',target,'')))},'finish_reason':'stop'}
    router.gateway.complete=resume
    result=router.resolve(messages,session=session,mode='llm')
    assert len(packets)==1 and packets[0]['pending_task']['source_ids']==['m0','m1']
    assert result['descriptor']['demand']=='deep'
    assert session.intents[result['pending_intent_id']]['status']=='running'
