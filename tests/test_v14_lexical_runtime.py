import copy
from types import SimpleNamespace
import pytest
from routing.v7.coordinator import Router
from routing.v7.catalog import compile_catalog

DESC=dict(operation='analysis',demand='simple',relation='independent',reference_ids=[],needs_context=False,
          reason='Test descriptor',effort_need='low')


class Proposal:
    def __init__(self,approved=False):self.artifact={'revision':'test-only','promotion_allowed':approved};self.flags=[]
    def propose(self,text,flags):
        self.flags.append(flags)
        return {'accepted':not any(flags.values()),'descriptor':copy.deepcopy(DESC)}


def router(mode='shadow',approved=False):
    scorer=Proposal(approved);r=Router(SimpleNamespace(),catalog=compile_catalog(),lexical_scorer=scorer,lexical_mode=mode)
    called=[]
    def classify(view):called.append(view);return copy.deepcopy(DESC),{'schema_valid':True}
    r.classifier=SimpleNamespace(classify=classify,variant='luna-default')
    return r,scorer,called


def test_shadow_records_proposal_but_still_calls_classifier():
    r,s,called=router()
    decision=r.resolve([{'role':'user','content':'Explain a Python tuple.'}],mode='hybrid')
    assert len(called)==1 and decision['lexical_proposal']['accepted']
    assert not decision['lexical_proposal']['used']


def test_enabled_requires_explicit_validation_and_keeps_context_guard():
    with pytest.raises(ValueError):router('enabled')
    r,s,called=router('enabled',True)
    decision=r.resolve([{'role':'user','content':'Explain a Python tuple.'}],mode='hybrid')
    assert not called and decision['lexical_proposal']['used']
    r,s,called=router('enabled',True)
    decision=r.resolve([{'role':'user','content':'Plan work.'},{'role':'assistant','content':'Plan ready.'},
                        {'role':'user','content':'Explain a Python tuple.'}],mode='hybrid')
    assert called and not decision['lexical_proposal']['used'] and s.flags[-1]['has_history']
