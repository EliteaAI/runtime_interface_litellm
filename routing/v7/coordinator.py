"""One opt-in candidate shared by lab, trajectory runner and SDK bridge.

Retains the established descriptor/selector and fixed-binding contracts. New
invocation state uses ContextVar; per-session state has independent ownership.
"""
import copy,time,json,re
from contextvars import ContextVar
from pathlib import Path
from .routing import Coordinator,Classifier,CLASSIFIER_SYSTEM,DEMAND,unknown,CATALOG
from .retrieval import build,bounded,digest
from .protocol import SYSTEM
from .lexical import guarded_rules
from .state import Session,observe_cache
from .economics import rank
from .catalog import compile_catalog,QualificationSnapshot
from .social import REVISION as SOCIAL_REVISION

EFFORT_SYSTEM='''
Also include effort_need="low|medium|high". This describes a requested reasoning
preset within a model family, not comparable intelligence across providers.
Use low for straightforward extraction/formatting, medium for substantive design
or analysis, high for multi-constraint proof, adversarial failure analysis or
complex verification. Source length or technical nouns alone do not imply high.
'''


class DispatchClassifier:
    def __init__(self,gateway,variant,catalog):
        self.variant=variant
        self.text=Classifier(gateway,variant,CLASSIFIER_SYSTEM+EFFORT_SYSTEM,catalog)
        self.tools=Classifier(gateway,variant,SYSTEM+EFFORT_SYSTEM,catalog)
    def classify(self,view):
        descriptor,info=(self.tools if 'evidence_coverage' in view else self.text).classify(view)
        if info.get('schema_valid'):
            raw=(info['response']['message'].get('content')or'').strip()
            if raw.startswith('```'):raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw)
            requested=json.loads(raw).get('effort_need')
            descriptor['effort_need']=requested if requested in {'low','medium','high'} else ('medium' if descriptor['demand']=='deep' else 'low')
        return descriptor,info


class Router(Coordinator):
    supports_request_contract=True
    def __init__(self,gateway,classifier_variant='luna-default',*,catalog=None,qualifications=None):
        super().__init__(gateway,classifier_variant,catalog=copy.deepcopy(catalog or compile_catalog()))
        self.qualifications=qualifications or QualificationSnapshot()
        self.revision='v6-'+digest({'algorithm_revision':4,'social_revision':SOCIAL_REVISION,'catalog':self.catalog,'qualifications':self.qualifications.revision,
            'classifier_protocol':CLASSIFIER_SYSTEM+SYSTEM+EFFORT_SYSTEM})[:12]
        self.local=ContextVar('routing_request_'+str(id(self)),default=None)
        self.view_builder=self._view;self.preprocessor=self._preprocess;self.rule_engine=guarded_rules
        self.classifier=DispatchClassifier(gateway,classifier_variant,self.catalog)

    def select(self,descriptor,allowed=None,previous=None,min_demand='simple'):
        ctx=self.local.get()or{};allowed=list(allowed)if allowed is not None else list(self.catalog['variants'])
        need=descriptor.get('effort_need','medium' if descriptor['demand']=='deep' else 'low')
        order={'low':1,'medium':2,'high':3}
        allowed=[v for v in allowed if self.catalog['variants'][v]['effort']is None or order[self.catalog['variants'][v]['effort']]>=order[need]]
        family=ctx.get('task_family',descriptor['operation']);contract=ctx.get('task_contract','text-tools-v1')
        allowed=self.qualifications.allowed(allowed,family,contract)
        selected=super().select(descriptor,allowed,previous,min_demand)
        selected['qualification_records']=self.qualifications.describe(allowed,family,contract)
        selected['effort_need']=need
        return selected

    def _view(self,messages,hint):
        ctx=self.local.get()
        view,resolution=build(messages,hint,ctx['session'].index,epoch=ctx['session'].epoch,
            max_bytes=ctx['view_bytes'],force_sources=ctx.get('force_sources',()))
        if ctx.get('pending_task'):
            view['pending_task']=copy.deepcopy(ctx['pending_task'])
        if (getattr(self.gateway,'runtime_context',{}).get('active_instructions') or {}).get('text'):
            resolution={'status':'ranked','selected_ids':[],'candidates':[],'method':'Active Agent instructions require classification'}
        ctx['resolution']=resolution
        return view

    def _preprocess(self,messages,view):
        return view,self.local.get()['resolution']

    def resolve(self,messages,*,mode='economic',binding=None,hint=None,previous=None,scope=None,allowed=None,
                min_demand='simple',session=None,output_cap=None,tools=None,view_bytes=24000,
                access_revision='local-authorized-v1',trusted_role=None,task_family=None,task_contract='text-tools-v1'):
        hint=copy.deepcopy(hint or {'kind':'chat_turn'})
        cap=output_cap if output_cap is not None else hint.get('generation_output_cap',8000)
        if type(cap)is not int or not 256<=cap<=32000:raise ValueError('Completion ceiling must be 256–32000')
        if type(view_bytes)is not int or not 6000<=view_bytes<=64000:raise ValueError('Classifier view budget outside bounds')
        session=session or getattr(scope,'routing_session',None) or Session()
        if scope is not None and not hasattr(scope,'routing_session'):scope.routing_session=session
        if not (scope and scope.decision) and (not binding or binding.get('mode')!='fixed'):
            session.prepare(messages,access_revision)
        ctx={'session':session,'cap':cap,'tools':tools,'view_bytes':view_bytes,'trusted_role':trusted_role,'task_contract':task_contract}
        if task_family:ctx['task_family']=task_family
        token=self.local.set(ctx)
        try:
            if scope and scope.decision and (binding or {}).get('mode','auto')!='fixed':
                pinned_variant=scope.decision['selection']['variant']
                family=ctx.get('task_family',(scope.decision.get('descriptor')or{}).get('operation','other'))
                if not self.qualifications.allowed([pinned_variant],family,task_contract):
                    raise ValueError('Pinned model no longer qualified for this task contract')
            decision=super().resolve(messages,mode=mode,binding=binding,hint=hint,previous=previous,
                scope=scope,allowed=allowed,min_demand=min_demand)
            pinned=decision.get('budget',{}).get('completion_cap',cap)
            if cap>pinned:raise ValueError('Child completion ceiling exceeds the root pin contract')
            decision.setdefault('budget',{'completion_cap':cap,'classifier_view_bytes':view_bytes})
            decision['policy_revision']=self.revision;decision['session_id']=session.id
            # The full manifest stays in session/checkpoint state. The trace is
            # compact; generation still receives every original tool response.
            if messages and messages[-1].get('role')=='user':
                manifest=session.evidence_manifest(messages)
                if manifest:decision['evidence_set']={'id':manifest['id'],'count':len(manifest['reference_ids']),'epoch':manifest['epoch']}
            return decision
        finally:self.local.reset(token)

    def _resolve(self,messages,mode,binding,hint,previous,allowed,min_demand):
        ctx=self.local.get();session=ctx['session'];pending=session.pending(messages,self.revision)
        if pending and pending['action']=='clarify':
            return {'action':'clarify','clarification':'Which pending task should I continue? Please identify the task.',
                'selection':{'variant':None,'model':None,'effort':None,'reason':'NO_UNIQUE_PENDING_TASK'},
                'descriptor':unknown(pending['reason']),'classifier':None,'view':None,'binding':binding,
                'budget':{'completion_cap':ctx['cap'],'classifier_view_bytes':ctx['view_bytes']}}
        if pending and 'intent' in pending:
            intent=pending['intent'];ctx['force_sources']=intent['source_ids']
            # A pending source is continuity evidence, not a qualified next-stage
            # descriptor. A new external Go must classify its changed deliverable.
            ctx['pending_task']={'text':intent['next_task'],'source_ids':intent['source_ids']}
        # Only the caller's typed application metadata can select this path.
        if ctx['trusted_role']=='read_only_manifest_loader':
            hint={**hint,'task_role':'transform','demand':'standard'};effective_mode='metadata'
            min_demand=max(min_demand,'standard',key=DEMAND.get)
        else:effective_mode='tuned' if mode=='economic' else mode
        active=(getattr(self.gateway,'runtime_context',{}).get('active_instructions') or {}).get('text')
        if ctx.get('pending_task') or active:
            effective_mode='llm'
        decision=super()._resolve(messages,effective_mode,binding,hint,previous,allowed,min_demand)
        decision['budget']={'completion_cap':ctx['cap'],'classifier_view_bytes':ctx['view_bytes']}
        if decision.get('action')!='clarify' and mode=='economic':
            decision['selection']=rank(decision['selection'],messages,self.catalog,session=session,gateway=self.gateway,
                cap=ctx['cap'],tools=ctx['tools'],previous=previous)
        if pending and 'intent' in pending and decision.get('action')!='clarify':
            if not session.claim_pending(pending['intent']['id']):raise ValueError('Pending task is already claimed')
            decision['pending_intent_id']=pending['intent']['id']
        return decision
