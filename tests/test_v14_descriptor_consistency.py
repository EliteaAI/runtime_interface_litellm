import copy
import json
from types import SimpleNamespace

import pytest

from routing.v7.candidate import FamilyClassifier
from routing.v7.catalog import compile_catalog
from routing.v7.routing import validate_descriptor
from routing.v7.task_profile import apply_profile, normalize_operation
from test_v14_continuity import message, record, link
from routing.v7.state import Session
from routing.v7.retrieval import build

PROFILE=dict(work='implement',reasoning='bounded',evidence='supplied',creativity='none',verification='check')
DESC=dict(operation='other',demand='simple',task_family='code',needs_context=False,relation='independent',reference_ids=[],reason='Implement a bounded validator.')


def test_valid_implementation_no_longer_becomes_unknown_deep_work():
    original=apply_profile(DESC,PROFILE);before=copy.deepcopy(original)
    fixed=normalize_operation(original)
    assert fixed['operation']=='design' and fixed['demand']=='simple'
    assert fixed['operation_normalization']['from']=='other' and original==before


@pytest.mark.parametrize('changes',[{'needs_context':True},{'relation':'ambiguous'},{'task_family':'unknown'},{'operation':'analysis'}])
def test_normalization_never_overrides_real_uncertainty_or_known_operation(changes):
    row=apply_profile({**DESC,**changes},PROFILE)
    assert normalize_operation(row)==row


def test_actual_classifier_adapter_normalizes_recorded_response_shape_without_new_inference():
    payload={**DESC,'work_profile':PROFILE,'input_status':'provided','retrieval_source_ids':[],
             'effort_need':'low','task_continuity':link('independent',None,'')}
    gateway=SimpleNamespace(complete=lambda *a,**k:{'message':{'content':json.dumps(payload)},'finish_reason':'stop'})
    classifier=FamilyClassifier(gateway,'luna-default',compile_catalog())
    view=dict(latest={'id':'m0','text':'Implement a standalone Python settings validator.'},recent=[],earlier_index=[],
              allowed_reference_ids=[])
    descriptor,info=classifier.classify(view)
    assert info['schema_valid'] and descriptor['operation']=='design' and descriptor['demand']=='simple'


def test_six_amendments_keep_all_dependencies_beyond_legacy_eight_reference_limit():
    session=Session();messages=[message('user','Outline a worker; defer its implementation.')]
    target=record(session,messages,link())
    for i in range(6):
        messages.append(message('user',f'Add invariant {i} to the pending worker.'))
        record(session,messages,link('amend',target,''))
    for i in range(24):messages += [message('user',f'Unrelated joke {i}'),message('assistant','Quack.')]
    session=Session.restore(session.checkpoint());messages.append(message('user','Proceed'));session.prepare(messages)
    sources=session.pending(messages,'policy')['intent']['source_ids']
    view,_=build(messages,None,session.index,force_sources=sources)
    assert len(sources)==14 and set(sources)<=set(view['allowed_reference_ids'])
    row={**DESC,'operation':'design','relation':'followup','reference_ids':sources}
    assert validate_descriptor(row,view)['reference_ids']==sources
    for bad in [sources+['m999999'],sources[:-1]+[sources[0]]]:
        with pytest.raises(ValueError):validate_descriptor({**row,'reference_ids':bad},view)
