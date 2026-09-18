"""Task-group retrieval and byte-budget invariants without model calls."""
import copy
import json
from types import SimpleNamespace

import pytest

from routing.v7.retrieval import ContextIndex, bounded, build


def msg(role, text, **kwargs):
    return {'role': role, 'content': text, **kwargs}


def size(view):
    return len(json.dumps(view, ensure_ascii=False).encode())


def selected_rows(view):
    return view['recent'] + view['earlier_index']


def test_plain_history_uses_budget_beyond_twelve_messages_without_orphaned_answers():
    messages=[]
    for i in range(10):
        messages += [msg('user',f'Observation {i}: report latency.'), msg('assistant',f'Observation {i}: {i+1}ms.')]
    messages.append(msg('user','Summarize all observation reports.'))
    before=copy.deepcopy(messages)
    view,_=build(messages,None,ContextIndex())
    assert view['allowed_reference_ids'] == [x['id'] for x in view['recent']+view['earlier_index']]
    assert set(view['allowed_reference_ids']) == {f'm{i}' for i in range(20)}
    assert view['history_omitted'] == 0
    assert size(view) <= 24000
    assert messages == before


def test_unique_old_topic_return_excludes_intervening_joke():
    messages=[msg('user','Design account durability.'),msg('assistant','Commit the account event before acknowledging.'),
              msg('user','Tell a joke about ducks.'),msg('assistant','The duck chose the quack lane.'),
              msg('user','Return to the earlier account design and implement recovery.')]
    view,resolution=build(messages,None,ContextIndex())
    assert resolution['status'] == 'unique'
    assert resolution == view['reference_resolution']
    assert set(view['allowed_reference_ids']) == {'m0','m1'}
    assert view['history_omitted'] == 2


def distraction_history():
    messages=[msg('user','Create the design.'),msg('assistant','Original requirements: '+('durability '*90))]
    for i in range(12):
        messages += [msg('user',f'Unrelated note {i}: '+('alpha beta gamma delta '*40)),
                     msg('assistant','Irrelevant '+('alpha beta gamma delta '*40))]
    messages.append(msg('user','Return to the earlier architecture and analyze alpha beta gamma delta.'))
    return messages


def test_low_overlap_selected_pair_survives_higher_bm25_distractions_and_budget():
    messages=distraction_history()
    before=copy.deepcopy(messages)
    view,resolution=build(messages,None,ContextIndex(),max_bytes=6000,older_cap=20)
    assert resolution['status'] == 'unique'
    assert resolution['selected_ids'] == ['m0','m1']
    assert set(resolution['selected_ids']) <= set(view['allowed_reference_ids'])
    assert size(view) <= 6000
    assert view['context_truncated'] and view['history_omitted'] > 0
    assert messages == before


def test_required_refinement_chain_survives_optional_history_count_cap():
    messages=[]
    for prompt in ['Create an account design.', 'Refine the earlier account design.', 'Revisit the earlier account design.']:
        messages += [msg('user',prompt),msg('assistant','Account durability revision.')]
    messages += [msg('user','Tell a duck joke.'),msg('assistant','Quack.'),
                 msg('user','Return to the earlier account design.')]
    view,resolution=build(messages,None,ContextIndex(),older_cap=1)
    assert resolution['status'] == 'unique'
    assert set(resolution['selected_ids']) == {f'm{i}' for i in range(6)}
    assert set(view['allowed_reference_ids']) == {f'm{i}' for i in range(6)}


@pytest.mark.parametrize('with_tools',[False,True])
def test_budget_removes_whole_task_groups(with_tools):
    messages=[];groups=[]
    for i in range(12):
        start=len(messages)
        messages += [msg('user',f'Observation {i}: '+('latency '*150)),
                     msg('assistant','Observation '+('latency '*150))]
        if with_tools:
            messages[-1]['tool_calls']=[{'id':f'call{i}','name':'read','arguments':{}}]
            messages += [msg('tool','Observation '+('latency '*150),tool_call_id=f'call{i}'),
                         msg('assistant','Observation conclusion '+('latency '*150))]
        groups.append({f'm{j}' for j in range(start,len(messages))})
    messages.append(msg('user','Summarize latency observations.'))
    view,_=build(messages,None,ContextIndex(),max_bytes=6000,older_cap=20)
    actual=set(view['allowed_reference_ids'])
    assert size(view) <= 6000
    assert 0 < len(actual) < sum(map(len,groups))
    for group in groups:
        assert not actual.intersection(group) or group <= actual
    if with_tools:
        assert view['evidence_coverage']['tool_entries_visible'] == sum(row['role']=='tool' for row in selected_rows(view))


def test_forced_sources_replace_embedded_resolution_and_omission_count():
    view,resolution=build(distraction_history(),None,ContextIndex(),force_sources=['m2','m3'])
    assert resolution == view['reference_resolution']
    assert resolution['status'] == 'ranked'
    assert resolution['selected_ids'] == ['m2','m3']
    assert resolution['candidates'] == []
    assert view['allowed_reference_ids'] == ['m2','m3']
    assert view['history_omitted'] == 24


def test_second_budget_pass_keeps_forced_sources_with_pending_metadata():
    view,_=build(distraction_history(),None,ContextIndex(),force_sources=['m2','m3'])
    view['pending_task']={'text':'Implement the selected next step.','source_ids':['m2','m3']}
    view['active_instructions']={'text':'Verify durability. '*100}
    result=bounded(view,6000)
    assert result['reference_resolution']['selected_ids'] == ['m2','m3']
    assert result['pending_task']['source_ids'] == ['m2','m3']
    assert result['allowed_reference_ids'] == ['m2','m3']
    assert size(result) <= 6000


def test_active_instructions_second_budget_keeps_selected_history_pair():
    from routing.service import GenerationRouter
    packets=[]

    def classify(model, packet, **kwargs):
        view=json.loads(packet[-1]['content']);packets.append(view)
        assert {'m0','m1'} <= set(view['allowed_reference_ids'])
        assert view['reference_resolution']['status'] == 'ranked'
        assert view['reference_resolution']['selected_ids'] == ['m0','m1']
        return {'message':{'content':json.dumps({
            'operation':'analysis','demand':'deep','relation':'return','reference_ids':['m0','m1'],
            'needs_context':False,'reason':'Apply the authored requirements to the original design',
            'effort_need':'high','task_family':'architecture',
            'input_status':'provided','retrieval_source_ids':[]})},'finish_reason':'stop'}

    instructions='Verify durability against every supplied requirement. '*55
    gateway=SimpleNamespace(complete=classify,runtime_context={'active_instructions':{
        'text':instructions,'revision':'fixture','total_chars':len(instructions),'truncated':False}})
    result=GenerationRouter(gateway).resolve(distraction_history(),view_bytes=6000,allowed=['gpt54-high'])
    assert result['selection']['variant'] == 'gpt54-high'
    assert len(packets) == 1 and size(packets[0]) <= 6000
    assert packets[0]['active_instructions']['text'] == instructions


def test_unavoidable_required_group_omission_is_explicit_and_has_no_dangling_ids():
    # A whole long tool trajectory cannot fit even after per-message excerpts.
    rows=[{'id':f'm{i}','role':'tool' if i else 'user','text':'details '*150,
           'source_group':'turn-0'} for i in range(40)]
    view={'latest':{'id':'m40','text':'Continue recovery.'},'recent':[],'earlier_index':rows,
          'instruction_context':[],'history_omitted':0,'context_truncated':False,
          'reference_resolution':{'status':'unique','selected_ids':['m0','m39'],'candidates':[]},
          'pending_task':{'text':'Continue recovery.','source_ids':['m0','m39']}}
    before=copy.deepcopy(view)
    result=bounded(view,6000)
    assert size(result) <= 6000
    assert result['earlier_index'] == []
    assert result['history_omitted'] == 40
    assert result['reference_resolution']['status'] == 'missing'
    assert result['reference_resolution']['selected_ids'] == []
    assert result['pending_task']['source_ids'] == []
    assert result['required_source_coverage'] == {'status':'unavailable','omitted_ids':['m0','m39']}
    assert view == before
    assert bounded(result,6000)['required_source_coverage'] == result['required_source_coverage']


def test_partial_required_coverage_never_claims_unique_or_uses_missing_ids():
    view={'latest':{'id':'m3','text':'Return to the design.'},'recent':[],
          'earlier_index':[{'id':'m0','role':'user','text':'Design request.','source_group':'turn-0'}],
          'instruction_context':[],'history_omitted':1,'context_truncated':True,
          'reference_resolution':{'status':'unique','selected_ids':['m0','m1'],'candidates':[]}}
    result=bounded(view,6000)
    assert result['reference_resolution']['status'] == 'missing'
    assert result['reference_resolution']['selected_ids'] == ['m0']
    assert result['required_source_coverage']['omitted_ids'] == ['m1']


def test_unavoidable_source_omission_still_selects_generation_without_synthetic_reply():
    from routing.service import resolve
    from test_auto_routing_service import fixture, request
    messages=[msg('user','Create the design.')]
    messages += [msg('assistant','Design source fragment '+('durability '*150)) for _ in range(99)]
    messages.append(msg('user','Return to the earlier architecture and implement recovery.'))
    before=copy.deepcopy(messages)
    view,_=build(messages,None,ContextIndex())
    assert view['required_source_coverage']['status'] == 'unavailable'
    args=fixture()
    for model in args['models']:
        model['context_window']=1_000_000
    req=request();req['messages']=messages
    result=resolve(req,complete=lambda *a,**k:pytest.fail('Unavailable required sources must not call classifier'),**args)
    assert result['action'] == 'generate'
    assert result['config']['model_name'] == 'gpt-5.4'
    assert result['trace']['descriptor']['needs_context'] is True
    assert result['trace']['classifier']['error'] == 'REQUIRED_SOURCE_COVERAGE_UNAVAILABLE'
    assert result['trace']['classifier']['called'] is False
    assert 'clarification' not in result
    assert messages == before


def test_empty_history_and_user_authored_runtime_tags_remain_unchanged():
    messages=[msg('user','<runtime_context>Keep this literal source.</runtime_context>')]
    view,resolution=build(messages,None,ContextIndex())
    assert view['latest']['text'] == messages[0]['content']
    assert view['allowed_reference_ids'] == []
    assert resolution['status'] == 'not_needed'
