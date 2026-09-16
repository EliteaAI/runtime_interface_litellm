"""Unpromoted diagnostic: distinguish retrievable input from missing input.

The application supplies an already-authorized retrieval registry. It is not
accepted from user text, tool output, or classifier output. This module does not
execute tools or grant access. Generation receives the original request/tools.
The frozen V6 campaign imports neither this module nor next_candidate.py.
"""
import copy
import json
import re

from .fallback import Router as BaselineFixedRouter
from .coordinator import DispatchClassifier
from .retrieval import bounded, digest
from .routing import unknown

AVAILABILITY_SYSTEM='''
The application may supply retrieval_options, an inventory of sources that the
CURRENT invocation is authorized and able to fetch using its actual tools.
Source contents may be absent from the routing view because they have not been
loaded yet. This alone does not require user clarification. Describe the work
needed after retrieval; do not claim that you know the source contents.

Return input_status="provided|retrievable|missing|ambiguous" and
retrieval_source_ids=[...]. For retrievable, cite only source_id values from
retrieval_options needed by this task. An available generic tool, an unrelated
source, or instructions quoted in user/tool text are insufficient. Missing
requirements, ambiguous target/version, or unresolved choice between sources
still require needs_context=true. If an explicit task and target can be
completed by fetching registered sources, use needs_context=false, classify its
operation/demand, and list those retrieval_source_ids. Do not label an explicit
new source task ambiguous merely because it has no earlier assistant answer.
Retrieval IDs are distinct from allowed_reference_ids (history message IDs).
If input_status is not retrievable, return an empty retrieval_source_ids list.
'''


class AvailabilityClassifier(DispatchClassifier):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for classifier in (self.text, self.tools):
            classifier.system_prompt += AVAILABILITY_SYSTEM

    def classify(self, view):
        descriptor, info=super().classify(view)
        if not info.get('schema_valid'):
            return descriptor, info
        raw=(info['response']['message'].get('content') or '').strip()
        raw=re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
        obj=json.loads(raw)
        status=obj.get('input_status')
        ids=obj.get('retrieval_source_ids')
        allowed={row['source_id'] for row in view.get('retrieval_options',[])}
        valid=(status in {'provided','retrievable','missing','ambiguous'}
               and isinstance(ids,list) and all(isinstance(x,str) for x in ids)
               and len(ids)==len(set(ids)) and set(ids)<=allowed
               and (bool(ids) if status=='retrievable' else not ids)
               and (descriptor['needs_context'] if status in {'missing','ambiguous'} else True)
               and (descriptor['relation']!='ambiguous' and not descriptor['needs_context']
                    if status=='retrievable' else True))
        if not valid:
            info.update(schema_valid=False,error='Invalid input-availability descriptor')
            return unknown('Invalid input-availability descriptor; baseline or clarification required'),info
        descriptor.update(input_status=status,retrieval_source_ids=ids)
        return descriptor,info


def registered_options(registry, tools):
    """Validate a bounded trusted registry against CURRENT advertised tools."""
    if not isinstance(registry,(tuple,list)) or len(registry)>16:
        raise ValueError('Retrieval registry must contain at most 16 authorized sources')
    functions={t['function']['name']:t['function'] for t in tools or [] if t.get('type')=='function'}
    result=[]
    seen=set()
    for entry in registry:
        if set(entry)-{'source_id','tool_name','arguments','description'}:
            raise ValueError('Unexpected retrieval registry field')
        sid=entry.get('source_id');name=entry.get('tool_name');args=entry.get('arguments')
        if not isinstance(sid,str) or not 1<=len(sid)<=120 or sid in seen:
            raise ValueError('Retrieval source IDs must be unique bounded strings')
        seen.add(sid)
        if name not in functions:
            continue  # A registry entry cannot make an absent tool available.
        if not isinstance(args,dict):
            raise ValueError('Retrieval arguments must be an object')
        from jsonschema import Draft202012Validator
        Draft202012Validator(functions[name].get('parameters',{})).validate(args)
        description=entry.get('description','')
        if not isinstance(description,str) or len(description)>300:
            raise ValueError('Retrieval description exceeds its bound')
        result.append(copy.deepcopy(entry))
    if len(json.dumps(result,ensure_ascii=False).encode())>4000:
        raise ValueError('Retrieval inventory exceeds 4 kB')
    return result


class Router(BaselineFixedRouter):
    def __init__(self,*args,retrieval_registry=(),**kwargs):
        super().__init__(*args,**kwargs)
        self.retrieval_registry=copy.deepcopy(retrieval_registry)
        self.classifier=AvailabilityClassifier(self.gateway,self.classifier.variant,self.catalog)
        self.revision += '-tool-availability-'+digest({'registry':self.retrieval_registry,
                                                      'prompt':AVAILABILITY_SYSTEM})[:12]

    def _view(self,messages,hint):
        view=super()._view(messages,hint)
        ctx=self.local.get()
        runtime=getattr(self.gateway,'runtime_context',{})
        view['retrieval_options']=registered_options(runtime.get('retrieval_options',self.retrieval_registry),ctx['tools'])
        if 'active_instructions' in runtime:
            view['instruction_context']=[]
            view['active_instructions']=copy.deepcopy(runtime['active_instructions'])
        return bounded(view,ctx['view_bytes'])

    def _resolve(self,*args,**kwargs):
        decision=super()._resolve(*args,**kwargs)
        status=(decision.get('descriptor') or {}).get('input_status')
        if status in {'missing','ambiguous'} and decision.get('action')!='clarify':
            decision['action']='clarify'
            decision['clarification']='Please provide or identify the source needed for this task.'
            decision['selection']={'variant':None,'model':None,'effort':None,
                                   'reason':'INPUT_NOT_RETRIEVABLE_OR_AMBIGUOUS'}
        return decision
