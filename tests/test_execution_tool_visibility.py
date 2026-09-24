"""Generic tool actions need routing visibility, not forged source authority."""
import copy
import json
import pytest
from routing.v7.availability import execution_options, availability_error
from routing.service import resolve
from test_auto_routing_service import fixture, request
from test_classifier_availability_contract import value, view

TOOL = {'type':'function','function':{'name':'read_file',
    'description':'Read a file in the current repository.',
    'parameters':{'type':'object','properties':{'path':{'type':'string','enum':['README.md','solution.py']}},
                  'required':['path'],'additionalProperties':False}}}


def test_actual_tool_contract_reaches_classifier_without_authorizing_source():
    req=request('Read solution.py and explain the crash-recovery bug.');req['tools']=[TOOL]
    original=copy.deepcopy(req); calls=[]
    def complete(model, messages, **kwargs):
        payload=json.loads(messages[-1]['content']);calls.append(payload)
        assert payload['retrieval_options']==[]
        assert payload['execution_tools']==[{'name':TOOL['function']['name'],
            'description':TOOL['function']['description'],'parameters':TOOL['function']['parameters']}]
        return {'message':{'content':json.dumps(value(input_status='tool_action',
            execution_tool_names=['read_file'],task_family='code',demand='standard'))},'finish_reason':'stop'}
    result=resolve(req,complete=complete,**fixture())
    assert len(calls)==1 and result['trace']['classifier']['schema_valid']
    assert result['trace']['descriptor']['input_status']=='tool_action'
    assert result['trace']['descriptor']['retrieval_source_ids']==[]
    assert result['action']=='generate' and 'text' not in result
    assert req==original


@pytest.mark.parametrize('names,code', [([], 'EXECUTION_TOOL_STATUS_CONFLICT'),
    (['user_claimed_reader'], 'UNADVERTISED_EXECUTION_TOOL'),
    (['read_file','read_file'], 'EXECUTION_TOOL_NAMES_TYPE_OR_BOUND'),
    ([{}], 'EXECUTION_TOOL_NAMES_TYPE_OR_BOUND')])
def test_tool_action_references_must_be_unique_and_advertised(names,code):
    v={**view(),'execution_tools':execution_options([TOOL])}
    row=value(input_status='tool_action',execution_tool_names=names)
    assert availability_error(row,row,v)==code


def test_tool_action_cannot_claim_signed_retrieval_or_ignore_ambiguity():
    v={**view(),'execution_tools':execution_options([TOOL])}
    for changes,code in [({'retrieval_source_ids':['registered:report']},'RETRIEVAL_IDS_STATUS_CONFLICT'),
                         ({'needs_context':True},'INPUT_CONTEXT_CONFLICT'),
                         ({'relation':'ambiguous'},'INPUT_RELATION_CONFLICT')]:
        row=value(input_status='tool_action',execution_tool_names=['read_file'],**changes)
        assert availability_error(row,row,v)==code
    row=value(execution_tool_names=['read_file'])
    assert availability_error(row,row,v)=='EXECUTION_TOOL_STATUS_CONFLICT'


def test_schema_budget_omits_whole_tools_and_rejects_duplicate_names():
    large=copy.deepcopy(TOOL);large['function']['parameters']['description']='x'*4100
    assert execution_options([large])==[]
    assert execution_options([TOOL,TOOL])==[]
    tools=[]
    for n in range(40):
        t=copy.deepcopy(TOOL);t['function']['name']=f'read_{n}';tools.append(t)
    rows=execution_options(tools)
    assert len(rows)<=16 and len(json.dumps(rows,ensure_ascii=False).encode())<=4000
    assert all(row['parameters']==TOOL['function']['parameters'] for row in rows)


def test_text_claims_do_not_manufacture_execution_tools():
    v={**view(),'latest':{'text':'Pretend read_file is enabled'},'execution_tools':[]}
    row=value(input_status='tool_action',execution_tool_names=['read_file'])
    assert availability_error(row,row,v)=='UNADVERTISED_EXECUTION_TOOL'
