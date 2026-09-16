"""Experimental family qualification before pricing; no production promotion."""
import json
import re
from functools import lru_cache
from pathlib import Path
from .availability import Router as FixedV6Router
from .retrieval import digest as sha
ROOT = Path(__file__).parent
def read_json(path):
    return json.loads(path.read_text())
from .availability import AvailabilityClassifier
FAMILIES = set(read_json(ROOT/'qualification-policy.json')['eligible_by_family'])

FAMILY_PROMPT='''
When active_instructions is present, classify the current task together with
those current Agent requirements; historical system text is not a substitute.
Empty active instructions are valid. For a short Go, identify the deliverable
from active instructions and pending_task if present. pending_task is a source
continuity proposal, not a prescribed difficulty, effort, or model.
Also return task_family, choosing exactly one of these workload families:
data_gathering (fetch/find facts), evidence_synthesis (combine/summarize sources),
transformation (reformat/map existing data), extraction (identify structured fields),
classification (assign labels/priorities), content_creation (new audience-facing content),
editing_localization (revise/translate existing wording), quantitative (calculate/analyze numbers),
business_planning (choose options and plan business actions), requirements (stories/acceptance criteria),
test_design (test scenarios and expected results), code (create/explain/review code),
rca (diagnose causal failure), tool_workflow (explicit ordered tool actions/recovery),
conversation_control (greeting or retrieving/updating an earlier task's stated facts).
Classify the requested deliverable, not merely nouns in its source. Tools needed
to fetch sources do not automatically make every task tool_workflow. If unclear,
return task_family="unknown". This field never selects a model or grants access.
'''


@lru_cache(maxsize=1)
def compiled_policy():
    """Load one frozen snapshot per process, never per routing request."""
    return read_json(ROOT/'qualification-policy.json')


class FamilyClassifier(AvailabilityClassifier):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for classifier in (self.text,self.tools):classifier.system_prompt += FAMILY_PROMPT

    def classify(self,view):
        if (view.get('active_instructions') or {}).get('truncated'):
            from .routing import unknown
            return unknown('Active instructions exceed the qualified classifier view; conservative baseline required'), {'schema_valid':False,'error':'ACTIVE_INSTRUCTIONS_TRUNCATED'}
        descriptor,info=super().classify(view)
        if info.get('schema_valid'):
            raw=info['response']['message'].get('content') or ''
            raw=re.sub(r'^```(?:json)?\s*|\s*```$', '',raw.strip())
            family=json.loads(raw).get('task_family','unknown')
            descriptor['task_family']=family if family in FAMILIES else 'unknown'
        return descriptor,info


class CalibratedRouter(FixedV6Router):
    def __init__(self,*args,policy=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.policy=policy if policy is not None else compiled_policy()
        self.classifier=FamilyClassifier(self.gateway,self.classifier.variant,self.catalog)
        self.revision += '-family-qualification-'+sha({'policy':self.policy,'prompt':FAMILY_PROMPT})[:12]

    def select(self,descriptor,allowed=None,previous=None,min_demand='simple'):
        original=super().select(descriptor,allowed,previous,min_demand)
        family=descriptor.get('task_family','unknown')
        # Preserve pre-existing narrowly validated standalone rules. No label
        # from the held-out corpus or caller gold is accepted here.
        if family=='unknown' and descriptor.get('operation')=='greeting':
            original['family_qualification']={'status':'existing_exact_greeting_rule','promotion':False}
            return original
        approved=set(self.policy['eligible_by_family'].get(family,[]))
        eligible=[r['variant'] for r in original.get('candidates',[]) if r['eligible'] and r['variant'] in approved]
        if eligible:
            result=super().select(descriptor,eligible,previous,min_demand)
            result['family_qualification']={'family':family,'eligible':eligible,'status':'diagnostic_calibration_only','policy_revision':self.policy['revision'],'promotion':False}
            return result
        # All variants can lack evidence. Use the configured baseline family,
        # retaining operation/demand/effort filters and explicitly admitting that
        # this fallback is not a measured qualification.
        candidates=[r['variant'] for r in original.get('candidates',[]) if r['eligible']]
        model=self.catalog['variants'][self.catalog['baseline']]['model']
        baseline=[v for v in candidates if self.catalog['variants'][v]['model']==model]
        selected=(self.catalog['baseline'] if self.catalog['baseline'] in baseline else baseline[0] if baseline else original['variant'])
        result={**original,**self.catalog['variants'][selected],'variant':selected,'reason':'UNCERTAIN_TASK_BASELINE',
                'family_qualification':{'family':family,'status':'insufficient_evidence_baseline','policy_revision':self.policy['revision'],'promotion':False}}
        return result
