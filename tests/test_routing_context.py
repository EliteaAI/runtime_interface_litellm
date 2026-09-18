"""Intent coverage and worker-derived retrieval authority; no model calls."""
import copy
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from routing.context import issue_context, read_context
from routing.service import resolve
from test_auto_routing_service import fixture, request, classifier_design


def context(text=''):
    return {'active_instructions':{'text':text,'revision':'revision','total_chars':len(text),'truncated':False},
            'retrieval_options':[]}


def signed_request():
    args=fixture();req=request('Prepare acceptance criteria from the configured requirements')
    req['tools']=[{'type':'function','function':{'name':'read_project_context','parameters':{'type':'object','properties':{}}}}]
    req['runtime_context']=context()
    req['runtime_context']['retrieval_options']=[{'source_id':'project-context:revision','tool_name':'read_project_context','arguments':{},'description':'Requirements for worker durability'}]
    req['runtime_context_token']=issue_context(req['runtime_context'],req['tools'],key=args['signing_key'],
        project_id=7,user_id=2,scope_id=req['scope_id'],invocation_id=req['invocation_id'],now=100)
    return req,args


def test_registered_project_source_reaches_classifier_without_exporting_contents():
    req,args=signed_request();views=[]
    def complete(model,messages,**kwargs):
        views.append(json.loads(messages[-1]['content']))
        row=json.loads(classifier_design()['message']['content'])
        row.update(task_family='requirements',input_status='retrievable',retrieval_source_ids=['project-context:revision'])
        return {'message':{'content':json.dumps(row)},'finish_reason':'stop'}
    result=resolve(req,complete=complete,**args)
    assert len(views)==1
    assert views[0]['retrieval_options']==req['runtime_context']['retrieval_options']
    assert result['action']=='generate'
    assert result['trace']['descriptor']['input_status']=='retrievable'


@pytest.mark.parametrize('mutation',['project','user','scope','invocation','tools','context','signature','unsigned','expired'])
def test_runtime_attestation_cannot_be_replayed_or_modified(mutation):
    req,args=signed_request();params={'key':args['signing_key'],'project_id':7,'user_id':2,'now':101}
    if mutation in {'project','user'}:params[mutation+'_id']+=1
    elif mutation in {'scope','invocation'}:req[mutation+'_id']='f'*64
    elif mutation=='tools':req['tools'][0]['function']['description']='changed'
    elif mutation=='context':req['runtime_context']['retrieval_options'][0]['description']='changed'
    elif mutation=='signature':req['runtime_context_token']+='x'
    elif mutation=='unsigned':req.pop('runtime_context_token')
    elif mutation=='expired':params['now']=1001
    with pytest.raises(ValueError):read_context(req,**params)


@pytest.mark.parametrize('task,instructions',[
    ('Go','Design durability and prove recovery under duplicate delivery. Include acceptance tests.'),
    ('Hi','For any task, analyze worker failures and verify recovery invariants.'),
    ('Review this implementation','Analyze assumptions. '+('noncritical description '*150)+' Verify crash recovery and idempotence at the end.'),
    ('Design durability and recovery from a Rust NATS Postgres worker specification',''),
])
def test_active_instruction_contract_reaches_single_classifier_call(task,instructions):
    req=request(task);req['runtime_context']=context(instructions)
    req['messages']=[{'role':'system','content':'irrelevant historical material '*1000},
                     {'role':'system','content':'another historical persona '*1000}]+req['messages']
    views=[]
    def complete(model,messages,**kwargs):
        views.append(json.loads(messages[-1]['content']));return classifier_design()
    result=resolve(req,complete=complete,**fixture())
    assert len(views)==1 and result['action']=='generate'
    assert views[0]['active_instructions']['text']==instructions
    assert views[0]['instruction_context']==[]


def test_truncated_active_contract_uses_explicit_conservative_fallback():
    req=request('Go');req['runtime_context']=context('head\n[omitted middle]\ntail')
    req['runtime_context']['active_instructions'].update(truncated=True,total_chars=100000)
    result=resolve(req,complete=lambda *a,**k:pytest.fail('Do not confidently classify incomplete instructions'),**fixture())
    assert result['action']=='generate'
    assert result['trace']['descriptor']['needs_context'] is True
    assert 'exceed' in result['trace']['descriptor']['reason']
    assert result['config']['model_name']!='global.openai.gpt-5.6-luna'


def test_prior_pin_is_same_run_only_even_with_same_conversation():
    args=fixture();first=resolve(request(),complete=classifier_design,**args)
    req=request('new task');req['invocation_id']='b'*64;req['prior_pin']=first['pin']
    with pytest.raises(ValueError):resolve(req,complete=classifier_design,**args)


def test_search_query_change_does_not_invalidate_source_or_instruction_revision():
    from routing.context import context_revision
    source=context('instructions');source['retrieval_options']=[{'source_id':'collection:one','tool_name':'search','arguments':{'query':'architecture'},'description':'Worker docs'}]
    other=copy.deepcopy(source);other['retrieval_options'][0]['arguments']['query']='implement recovery'
    assert context_revision(source)==context_revision(other)
    other['retrieval_options'][0]['source_id']='collection:two'
    assert context_revision(source)!=context_revision(other)


@pytest.mark.parametrize('value', [['invalid'],{'active_instructions':['invalid']},{'unexpected':'field'}])
def test_invalid_context_envelope_is_a_validation_error(value):
    from routing.context import validate_context
    with pytest.raises(ValueError):validate_context(value,[])


def test_attestation_is_registered_only_on_existing_restricted_worker_bus():
    import ast
    from types import SimpleNamespace
    from unittest.mock import Mock
    path=Path(__file__).resolve().parents[1]/'methods/init.py'
    tree=ast.parse(path.read_text())
    worker=SimpleNamespace(rpc_node=Mock()); owner=SimpleNamespace(sign_routing_context=object())
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
           and 'worker_client.rpc_node.' in ast.unparse(n.value)]
    assert len(calls)==2
    for call in calls:
        exec(compile(ast.Module(body=[call],type_ignores=[]),str(path),'exec'),{'worker_client':worker,'self':owner})
    worker.rpc_node.register.assert_called_once_with(owner.sign_routing_context,name='restricted_sign_routing_context')
    worker.rpc_node.unregister.assert_called_once_with(owner.sign_routing_context,name='restricted_sign_routing_context')
    method=(path.parent/'routing_context.py').read_text()
    assert '@web.rpc' not in method and '@web.route' not in method
    assert not (path.parents[1]/'rpc/routing_context.py').exists()


def test_plain_chat_greeting_typed_projection_restores_zero_classifier_path():
    from routing.v7.routing import validate_descriptor
    raw='<runtime_context>\n  <user_id>3</user_id>\n  <project_id>2</project_id>\n</runtime_context>. Hi'
    value={'operation':'greeting','demand':'simple','relation':'independent','reference_ids':[],
           'needs_context':False,'reason':'User greeting'}
    with pytest.raises(ValueError,match='standalone greeting'):
        validate_descriptor(value,{'latest':{'text':raw},'recent':[],'earlier_index':[]})
    req=request('Hi');req['runtime_context']=context('')
    # The ordinary generation system remains present, but typed active authored
    # instructions are empty. No classifier is justified by scaffolding alone.
    req['messages'].insert(0,{'role':'system','content':'Platform tool/disclosure scaffolding'})
    args=fixture()
    for price in args['price_snapshot']['entries']:
        if 'luna' in price['model_name']:
            price['input_cost_per_token']='.0000002';price['output_cost_per_token']='.0000012'
    result=resolve(req,complete=lambda *a,**k:pytest.fail('Plain greeting needs no classifier'),**args)
    assert result['trace']['selection']['variant']=='luna-default'
    assert result['trace']['classifier']['called'] is False
