import copy
import pytest
from routing.v7.calibration_scope import check_request_scope, validate_scope
from routing.v7.catalog import compile_uniform_catalog
from routing.v7.retrieval import digest
from test_v14_scoped_bundle import candidate


def scope():
    return {'id':'text:generated-text-history-r26','delivery':'text','execution_contract':'generated-text-history-r26',
            'tools_sha256':digest([]),'history':'bounded_conversation','maximum_user_turns':4}


def conversation(n):
    return [item for i in range(n) for item in [{'role':'user','content':str(i)},{'role':'assistant','content':'Generated reply'}]][:-1]


def test_exact_bound_covers_current_turn_and_preserves_isolated_partition():
    value=scope()
    for n in range(1,5):check_request_scope(value,value['id'],conversation(n),[])
    with pytest.raises(ValueError,match='turn bound'):check_request_scope(value,value['id'],conversation(5),[])
    with pytest.raises(ValueError,match='task contract differs'):check_request_scope(value,'text:isolated-text-r2',conversation(2),[])


def test_prior_tools_cannot_borrow_tool_free_context_evidence():
    value=scope();messages=conversation(2)
    messages.insert(2,{'role':'tool','content':'A very large historical tool result','tool_call_id':'x'})
    with pytest.raises(ValueError,match='prior tool history'):check_request_scope(value,value['id'],messages,[])
    with pytest.raises(ValueError,match='tool schema differs'):check_request_scope(value,value['id'],conversation(2),[{'name':'run_child'}])


@pytest.mark.parametrize('change',[
    {'maximum_user_turns':0},{'maximum_user_turns':True},{'maximum_user_turns':65},
    {'maximum_user_turns':4.0},{'delivery':'repository','id':'repository:generated-text-history-r26'},
    {'tools_sha256':digest([{'name':'write_file'}])},{'history':'conversation_without_bound'}])
def test_no_implicit_expansion_of_measured_context(change):
    with pytest.raises(ValueError):validate_scope({**scope(),**change})


def test_context_scope_is_metadata_not_a_capability_grant():
    value=candidate();value['request_scope']=scope()
    for row in value['records']:row['profile_evidence']=[]
    catalog=compile_uniform_catalog(value,classifier_variant='classifier',baseline_variant='old')
    assert catalog['request_scope']==scope()
    assert not any(v['enabled'] for v in catalog['variants'].values())
    assert not catalog['switching_policies']
