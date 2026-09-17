"""One-call availability contract: strict enums, source authority and coherence."""
import copy
import json

import pytest

from routing.v7.availability import AvailabilityClassifier, AVAILABILITY_SYSTEM, availability_error
from routing.v7.catalog import compile_catalog
from routing.service import resolve
from test_auto_routing_service import fixture, request


def value(**changes):
    row={'operation':'analysis','demand':'simple','relation':'independent','reference_ids':[],
         'needs_context':False,'reason':'Classify the requested work','effort_need':'low',
         'input_status':'provided','retrieval_source_ids':[],'task_family':'data_gathering'}
    return {**row,**changes}


def view():
    return {'latest':{'id':'m0','text':'Requested task'},'recent':[],'earlier_index':[],
            'allowed_reference_ids':[], 'retrieval_options':[{'source_id':'registered:report'}]}


@pytest.mark.parametrize('row', [value(),value(input_status='missing',needs_context=True),
    value(input_status='ambiguous',needs_context=True,relation='ambiguous'),
    value(input_status='ambiguous',needs_context=True),
    value(input_status='retrievable',retrieval_source_ids=['registered:report'])])
def test_only_coherent_availability_combinations_are_valid(row):
    assert availability_error(row,row,view()) is None


@pytest.mark.parametrize('change,code', [
    ({'input_status':'unavailable'},'INPUT_STATUS_ENUM'),
    ({'input_status':'provided|retrievable|missing|ambiguous'},'INPUT_STATUS_ENUM'),
    ({'input_status':[]},'INPUT_STATUS_ENUM'), ({'input_status':None},'INPUT_STATUS_ENUM'),
    ({'retrieval_source_ids':'registered:report'},'RETRIEVAL_IDS_TYPE_OR_BOUND'),
    ({'retrieval_source_ids':[{}]},'RETRIEVAL_IDS_TYPE_OR_BOUND'),
    ({'retrieval_source_ids':[str(i) for i in range(17)]},'RETRIEVAL_IDS_TYPE_OR_BOUND'),
    ({'input_status':'retrievable','retrieval_source_ids':['registered:report']*2},'DUPLICATE_RETRIEVAL_ID'),
    ({'input_status':'retrievable','retrieval_source_ids':['user-claimed:source']},'UNREGISTERED_RETRIEVAL_ID'),
    ({'input_status':'retrievable','retrieval_source_ids':['m0']},'UNREGISTERED_RETRIEVAL_ID'),
    ({'input_status':'retrievable'},'RETRIEVAL_IDS_STATUS_CONFLICT'),
    ({'retrieval_source_ids':['registered:report']},'RETRIEVAL_IDS_STATUS_CONFLICT'),
    ({'input_status':'missing'},'INPUT_CONTEXT_CONFLICT'),
    ({'input_status':'ambiguous'},'INPUT_CONTEXT_CONFLICT'),
    ({'needs_context':True},'INPUT_CONTEXT_CONFLICT'),
    ({'relation':'ambiguous'},'INPUT_RELATION_CONFLICT'),
    ({'input_status':'retrievable','retrieval_source_ids':['registered:report'],'needs_context':True},'INPUT_CONTEXT_CONFLICT'),
    ({'input_status':'retrievable','retrieval_source_ids':['registered:report'],'relation':'ambiguous'},'INPUT_RELATION_CONFLICT'),
])
def test_invalid_output_is_rejected_with_safe_exact_code_not_repaired(change,code):
    raw=value(**change);original=copy.deepcopy(raw);calls=[]
    class Gateway:
        def complete(self,*args,**kwargs):
            calls.append((args,kwargs))
            return {'message':{'content':json.dumps(raw)},'finish_reason':'stop'}
    classifier=AvailabilityClassifier(Gateway(),'luna-default',compile_catalog())
    desc,info=classifier.classify(view())
    assert len(calls)==1
    assert info['schema_valid'] is False
    assert info['error']=='Invalid input-availability descriptor: '+code
    assert desc['operation']=='other' and desc['demand']=='deep' and desc['needs_context']
    assert 'input_status' not in desc
    assert raw==original
    # Diagnostics expose only a bounded code, never quoted task/source contents.
    assert 'user-claimed' not in info['error']


@pytest.mark.parametrize('task,row,action', [
    ('What is the weather today in San Francisco?',value(input_status='missing',needs_context=True),'generate'),
    ('Explain why the sky appears blue.',value(),'generate'),
    ('Summarize the document I have not attached.',value(input_status='missing',needs_context=True,task_family='evidence_synthesis'),'generate'),
    ('Use the report from our previous choice.',value(input_status='ambiguous',needs_context=True,relation='ambiguous'),'generate'),
    ('Translate "What is the live exchange rate?" into Spanish.',value(operation='transform',task_family='editing_localization'),'generate'),
    ('Prove durable replay under worker crash and two-zone failure.',value(operation='design',demand='deep',effort_need='high',task_family='architecture'),'generate'),
])
def test_single_call_preserves_input_uncertainty_for_generation_model(task,row,action):
    calls=[]
    def complete(*args,**kwargs):calls.append(args);return {'message':{'content':json.dumps(row)},'finish_reason':'stop'}
    result=resolve(request(task),complete=complete,**fixture())
    assert len(calls)==1 and result['trace']['classifier']['schema_valid']
    assert result['action']==action
    assert result['trace']['descriptor']['demand']==row['demand']
    assert result['trace']['descriptor']['operation']==row['operation']
    assert result['trace']['descriptor']['needs_context']==row['needs_context']
    assert result['pin'] and 'text' not in result


def test_prompt_preserves_known_failure_guards_without_entity_shortcuts():
    for text in ('needs_context=false','needs_context=true','today/latest/right-now',
                 'conflicting evidence','Mixed requests','Quoted questions','unrelated source'):
        assert text in AVAILABILITY_SYSTEM
    assert 'Messi' not in AVAILABILITY_SYSTEM and 'Chicago' not in AVAILABILITY_SYSTEM
