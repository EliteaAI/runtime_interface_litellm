"""Compiled in-memory experiment catalog; qualification is not a price label."""
import copy,json
from pathlib import Path
from .routing import CATALOG
from .retrieval import digest
from functools import lru_cache


@lru_cache(maxsize=1)
def calibration_candidate():
    return json.loads((Path(__file__).parent/'calibration-v9.json').read_text())


@lru_cache(maxsize=1)
def effort_candidate():
    return json.loads((Path(__file__).parent/'calibration-v12.json').read_text())


def compile_catalog(calibration_revision='v12', *, candidate=None):
    if calibration_revision not in {'v9', 'v12'}:
        raise ValueError('Unknown calibration revision')
    value=copy.deepcopy(CATALOG);value['revision']='v6-effort-profiles-1'
    value['calibration_revision'] = calibration_revision
    value['qualification']='Provisional task ceilings; effort transport verified on three fixtures per preset; full-workload qualification still measured separately'
    for vid,base,effort,demand in [('gpt54-low','gpt54-medium','low','standard'),('gpt54-high','gpt54-medium','high','deep'),('mini-medium','mini-low','medium','standard')]:
        value['variants'][vid]={**copy.deepcopy(value['variants'][base]),'effort':effort,'max_demand':demand,
            'price_key':base,'qualification_key':vid+':text-tools-v1','transport_evidence':'trajectory-v6/evidence/effort-probe.jsonl'}
    # These are exact frozen transport contracts, not prefix or price inference.
    for variant in value['variants'].values():
        variant['cache_write_mode'] = ('ordinary_input' if variant['model'] in {'gpt-5.4', 'gpt-5.4-mini'} else 'separate')
    base = effort_candidate() if calibration_revision == 'v12' else calibration_candidate()
    _apply_candidate(value, base)
    if candidate is not None:
        if set(candidate['variants']) & set(value['variants']):
            raise ValueError('Candidate variants must not replace frozen baseline identities')
        _apply_candidate(value, candidate)
    return value


def compile_uniform_catalog(snapshot, *, classifier_variant, baseline_variant):
    """Compile a complete V14 pool without inheriting legacy capability grants.

    This is an explicit offline-candidate entry point. The configured product
    continues to load its existing snapshot until calibration and release gates
    authorize replacing it. An empty cell disables qualification; it never
    restores an old grant for the same model.
    """
    from .task_profile import FAMILIES
    from .qualification import uniform_assessment

    policy = snapshot.get('qualification_policy', {})
    if policy.get('revision') != 'uniform-profile-v1':
        raise ValueError('A uniform qualification snapshot is required')
    identities = snapshot['variants']
    if not identities or classifier_variant not in identities or baseline_variant not in identities:
        raise ValueError('Classifier and baseline must belong to the same snapshot')
    if any(identity.get('cache_write_mode') not in {'ordinary_input', 'separate'} for identity in identities.values()):
        raise ValueError('Every uniform variant needs its measured cache write contract')
    records = snapshot['records']
    pairs = [(r['variant'], r['family']) for r in records]
    expected = {(vid, family) for vid in identities for family in FAMILIES}
    if len(pairs) != len(set(pairs)) or set(pairs) != expected:
        raise ValueError('Every variant needs exactly one record per workload family')

    # Recompute admission rather than trusting a stale family-level flag from a
    # previous compiler. This also prevents a passing aggregate hiding a failed
    # narrow profile. Pools must carry leave-template-out validation evidence.
    candidate = copy.deepcopy(snapshot)
    for row in candidate['records']:
        demands = set()
        contract = {'qualification_policy': policy, 'revision': snapshot['revision'],
                    'source_sha256': snapshot['source_sha256'],
                    'family_evidence': {row['family']: row}}
        for pool in row.get('validated_pools', []):
            if (pool.get('pooling_validation',{}).get('method')!='leave-template-out-v1'
                    or not pool['pooling_validation'].get('source_sha256')):
                raise ValueError('Pool has no compatible validation certificate')
        for cell in row.get('profile_evidence', []):
            eligible_demands = []
            for demand in cell['demand_coverage']:
                result = uniform_assessment(contract, {'task_family': row['family'],
                    'work_profile': cell['work_profile'], 'needs_context': False,
                    'relation': 'independent'}, demand)
                if result['eligible']:
                    eligible_demands.append(demand)
            cell['eligible_local_beta'] = bool(eligible_demands)
            demands.update(eligible_demands)
        row['eligible_local_beta'] = bool(demands)
        row['demand_coverage'] = sorted(demands, key={'simple': 0, 'standard': 1, 'deep': 2}.get)

    # Only non-capability metadata is established here. _apply_candidate starts
    # from an empty pool, so missing/new/old identities receive identical rules.
    value = {'revision': 'uniform-catalog-v14', 'calibration_revision': snapshot['revision'],
             'uniform_qualification': True, 'classifier_variant': classifier_variant,
             'baseline': baseline_variant, 'variants': {},
             'qualification': 'Uniform measured work profiles; no inherited legacy grants',
             'preferences': {operation: list(identities) for operation in
                             ('greeting', 'creative', 'transform', 'analysis', 'design', 'other')}}
    _apply_candidate(value, candidate)
    value['switching_policies']=copy.deepcopy(snapshot.get('switching_policies',[]))
    return value


def _apply_candidate(value, candidate):
    """Compile trusted evidence through the same path for baseline and candidate."""
    value['revision'] += '+'+candidate['revision']
    for vid, identity in candidate['variants'].items():
        evidence = {r['family']: {key: copy.deepcopy(r[key]) for key in (
                    'sample_count', 'pass', 'fail', 'unknown', 'wilson95', 'demand_coverage',
                    'quality_fail', 'refused', 'disagreement', 'delivery_failure',
                    'profile_evidence', 'validated_pools') if key in r}
                    for r in candidate['records'] if r['variant'] == vid}
        families = {r['family']: {key: copy.deepcopy(r[key]) for key in (
                    'sample_count', 'pass', 'fail', 'unknown', 'wilson95', 'demand_coverage')}
                    for r in candidate['records']
                    if r['variant'] == vid and r['eligible_local_beta']}
        demands = {d for r in families.values() for d in r['demand_coverage']}
        for row in candidate['records']:
            if row['variant'] == vid and row['family'] in families and row.get('usage_profile'):
                families[row['family']]['usage_profile'] = copy.deepcopy(row['usage_profile'])
        value['variants'][vid] = {
            'model': identity['model'], 'effort': identity['effort'], 'enabled': bool(demands),
            # These are the selector's legal known operations, not independent
            # model capability claims. CalibratedRouter requires the exact
            # measured family/demand cell before this generic selector runs.
            'tasks': ['creative', 'transform', 'analysis', 'design'],
            'max_demand': max(demands, key={'simple':0, 'standard':1, 'deep':2}.get) if demands else 'simple',
            'cost_band': 2, 'cache_write_mode': identity.get('cache_write_mode', 'separate'),
            'calibration_contract': {'revision': candidate['revision'],
                'source_sha256': candidate['source_sha256'], 'families': families,
                'family_evidence': evidence,
                'transport': identity['transport'], 'output_allowance': identity['total_output_allowance'],
                'production_promotion_allowed': False}}
        if 'requested_reasoning_fields' in identity:
            value['variants'][vid]['calibration_contract']['reasoning_fields'] = copy.deepcopy(identity['requested_reasoning_fields'])
        if 'qualification_policy' in candidate:
            value['variants'][vid]['calibration_contract']['qualification_policy']=copy.deepcopy(candidate['qualification_policy'])


class QualificationSnapshot:
    """Lookup-only, compiled once by a trusted application configuration owner.

    A failed/expired qualified contract can narrow a cohort. Missing evidence is
    explicit; this prototype retains the separately labeled provisional pool.
    """
    def __init__(self,records=(),revision='provisional'):
        records=copy.deepcopy(list(records))
        self.revision=revision+'-'+digest(records)[:12]
        self.records={(r['variant'],r['task_family'],r['contract']):r for r in records}
    def allowed(self,variants,family,contract):
        return [v for v in variants if self.records.get((v,family,contract),{}).get('status')not in {'rejected','stale'}]
    def describe(self,variants,family,contract):
        return copy.deepcopy({v:self.records.get((v,family,contract),{'status':'provisional','sample_count':0})for v in variants})
