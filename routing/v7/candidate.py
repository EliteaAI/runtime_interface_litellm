"""Experimental family qualification before pricing; no production promotion."""
import copy
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
from .task_profile import PROFILE_PROMPT, FAMILY_PROMPT, FAMILIES, apply_profile, normalize_operation
from .qualification import assess
from .task_continuity import PROMPT as CONTINUITY_PROMPT, validate as validate_continuity





@lru_cache(maxsize=2)
def compiled_policy(calibration_revision='v12'):
    """Load one frozen snapshot per process, never per routing request."""
    policy = read_json(ROOT/'qualification-policy.json')
    if calibration_revision == 'v9':
        return policy
    from .catalog import effort_candidate, calibration_candidate
    candidate = effort_candidate()
    replaced = set(calibration_candidate()['variants']) | set(candidate['variants'])
    for family, variants in policy['eligible_by_family'].items():
        policy['eligible_by_family'][family] = [v for v in variants if v not in replaced]
    for row in candidate['records']:
        if row['eligible_local_beta']:
            policy['eligible_by_family'].setdefault(row['family'], []).append(row['variant'])
    policy['revision'] += '+'+candidate['revision']
    return policy


def candidate_policy(candidate, calibration_revision='v12'):
    """Trusted offline candidate overlay; preserve every baseline cohort."""
    policy = copy.deepcopy(compiled_policy(calibration_revision))
    for row in candidate['records']:
        if row['eligible_local_beta']:
            policy['eligible_by_family'].setdefault(row['family'], []).append(row['variant'])
    policy['revision'] += '+' + candidate['revision']
    return policy


class FamilyClassifier(AvailabilityClassifier):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for classifier in (self.text,self.tools):classifier.system_prompt += FAMILY_PROMPT + PROFILE_PROMPT + CONTINUITY_PROMPT

    def classify(self,view):
        if (view.get('required_source_coverage') or {}).get('status') == 'unavailable':
            from .routing import unknown
            return unknown('Required source groups exceed the classifier view; generation retains full history'), {'schema_valid':False,'called':False,'error':'REQUIRED_SOURCE_COVERAGE_UNAVAILABLE'}
        if (view.get('active_instructions') or {}).get('truncated'):
            from .routing import unknown
            return unknown('Active instructions exceed the qualified classifier view; conservative baseline required'), {'schema_valid':False,'called':False,'error':'ACTIVE_INSTRUCTIONS_TRUNCATED'}
        descriptor,info=super().classify(view)
        if info.get('schema_valid'):
            raw=info['response']['message'].get('content') or ''
            raw=re.sub(r'^```(?:json)?\s*|\s*```$', '',raw.strip())
            parsed=json.loads(raw)
            family=parsed.get('task_family','unknown')
            descriptor['task_family']=family if family in FAMILIES else 'unknown'
            try:
                entries={x['id']:x for lane in ('recent','earlier_index','instruction_context') for x in view.get(lane,[])}
                if any(entries.get(r,{}).get('content_truncated') for r in descriptor.get('reference_ids',[])):
                    raise ValueError('SELECTED_SOURCE_CONTENT_INCOMPLETE')
                descriptor=apply_profile(descriptor, parsed.get('work_profile'))
                descriptor=normalize_operation(descriptor)
                continuity=validate_continuity(parsed.get('task_continuity'),view)
                if continuity is not None:descriptor['task_continuity']=continuity
            except ValueError as exc:
                from .routing import unknown
                return unknown(str(exc)), {**info, 'schema_valid': False, 'error': str(exc)}
        return descriptor,info


class CalibratedRouter(FixedV6Router):
    def __init__(self,*args,policy=None,coverage_fallback_variant=None,**kwargs):
        if coverage_fallback_variant is not None and (not isinstance(coverage_fallback_variant, str)
                or not coverage_fallback_variant or len(coverage_fallback_variant) > 256):
            raise ValueError('Invalid calibration coverage fallback')
        catalog = kwargs.get('catalog')
        if catalog and catalog.get('uniform_qualification') and 'classifier_variant' not in kwargs and len(args) < 2:
            kwargs['classifier_variant'] = catalog['classifier_variant']
        super().__init__(*args,**kwargs)
        if coverage_fallback_variant is not None and not self.catalog.get('uniform_qualification'):
            raise ValueError('Coverage fallback requires a uniform calibration catalog')
        if coverage_fallback_variant is not None and coverage_fallback_variant not in self.catalog['variants']:
            raise ValueError('Coverage fallback is not in the installed profile')
        self.coverage_fallback_variant = coverage_fallback_variant
        if self.catalog.get('uniform_qualification'):
            if policy is not None:
                raise ValueError('Uniform catalog owns its qualification policy')
            self.policy = {'revision': self.catalog['revision'], 'eligible_by_family': {
                family: [vid for vid, value in self.catalog['variants'].items()
                         if family in value['calibration_contract']['families']]
                for family in FAMILIES}}
        else:
            self.policy=policy if policy is not None else compiled_policy(self.catalog.get('calibration_revision', 'v12'))
        self.classifier=FamilyClassifier(self.gateway,self.classifier.variant,self.catalog)
        self.revision += '-family-qualification-'+sha({'policy':self.policy,'prompt':FAMILY_PROMPT+PROFILE_PROMPT+CONTINUITY_PROMPT})[:12]
        if coverage_fallback_variant is not None:
            self.revision += '-coverage-fallback-1-' + sha(coverage_fallback_variant)[:12]

    def _coverage_baseline(self, descriptor, permitted, assessments, previous, min_demand):
        baseline = self.coverage_fallback_variant
        assessment = assessments.get(baseline, {})
        if (baseline is None or baseline not in permitted
                or assessment.get('status') not in {
                    'unmeasured_work_profile', 'insufficient_independent_templates',
                    'insufficient_quality_confidence'}):
            raise ValueError('No eligible configured model; do not silently escape the pool')
        # Retain the ordinary operation, demand, effort and qualification-record
        # guards. Only this explicit baseline can serve an evidence gap; never
        # substitute an arbitrary survivor or manufacture a measured pass.
        result = super().select(descriptor, [baseline], previous, min_demand)
        result.update(reason='UNMEASURED_CONFIGURED_FALLBACK',
            quality_status='Configured fallback; exact task quality and savings are unmeasured',
            family_qualification={
                'family': descriptor.get('task_family'), 'eligible': [],
                'status': 'unmeasured_configured_fallback', 'qualification_granted': False,
                'optimization_eligible': False, 'promotion': False,
                'policy_revision': self.revision, 'fallback_variant': baseline,
                'coverage_gap': assessment['status'], 'assessments': assessments})
        return result

    def select(self,descriptor,allowed=None,previous=None,min_demand='simple'):
        # V9 defaults are admitted only by the original full-family calibration
        # cell, including its observed demand bands. Unknown/ambiguous work must
        # never reach one through the insufficient-evidence fallback.
        from .routing import DEMAND
        if self.catalog.get('request_scope'):
            ctx = self.local.get() or {}
            if ctx.get('task_contract') != self.catalog['request_scope']['id']:
                raise ValueError('Unmeasured execution envelope: no trusted runtime scope')
        permitted = list(self.catalog['variants']) if allowed is None else list(allowed)
        runtime_permitted = list(permitted)
        demand = max(descriptor['demand'], min_demand, key=DEMAND.get)
        assessments = {}
        for vid in list(permitted):
            contract = self.catalog['variants'][vid].get('calibration_contract')
            if not contract:
                continue
            assessment = assess(contract, descriptor, demand)
            assessments[vid] = assessment
            if not assessment['eligible']:
                permitted.remove(vid)
        evidence_trace = {'assessments': assessments} if assessments else {}
        if not permitted:
            return self._coverage_baseline(descriptor, runtime_permitted, assessments, previous, min_demand)
        allowed = permitted
        original=super().select(descriptor,allowed,previous,min_demand)
        family=descriptor.get('task_family','unknown')
        # Preserve pre-existing narrowly validated standalone rules. No label
        # from the held-out corpus or caller gold is accepted here.
        if family=='unknown' and descriptor.get('operation')=='greeting':
            original['family_qualification']={'status':'existing_exact_greeting_rule','promotion':False,**evidence_trace}
            return original
        approved=set(self.policy['eligible_by_family'].get(family,[]))
        eligible=[r['variant'] for r in original.get('candidates',[]) if r['eligible'] and r['variant'] in approved]
        if eligible:
            result=super().select(descriptor,eligible,previous,min_demand)
            result['economic_task']={'family':family,'demand':demand,
                                     'work_profile':copy.deepcopy(descriptor.get('work_profile'))}
            if self.catalog.get('request_scope'):
                result['economic_task']['delivery'] = self.catalog['request_scope']['delivery']
            result['family_qualification']={'family':family,'eligible':eligible,'status':'diagnostic_calibration_only','policy_revision':self.policy['revision'],'promotion':False,**evidence_trace}
            return result
        # All variants can lack evidence. Use the configured baseline family,
        # retaining operation/demand/effort filters and explicitly admitting that
        # this fallback is not a measured qualification.
        candidates=[r['variant'] for r in original.get('candidates',[]) if r['eligible']]
        model=self.catalog['variants'][self.catalog['baseline']]['model']
        baseline=[v for v in candidates if self.catalog['variants'][v]['model']==model]
        selected=(self.catalog['baseline'] if self.catalog['baseline'] in baseline else baseline[0] if baseline else original['variant'])
        result={**original,**self.catalog['variants'][selected],'variant':selected,'reason':'UNCERTAIN_TASK_BASELINE',
                'family_qualification':{'family':family,'status':'insufficient_evidence_baseline','policy_revision':self.policy['revision'],'promotion':False,**evidence_trace}}
        return result
