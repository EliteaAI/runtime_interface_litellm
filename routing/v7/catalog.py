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


def compile_catalog(calibration_revision='v12'):
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
    candidate = effort_candidate() if calibration_revision == 'v12' else calibration_candidate()
    value['revision'] += '+'+candidate['revision']
    for vid, identity in candidate['variants'].items():
        families = {r['family']: {key: copy.deepcopy(r[key]) for key in (
                    'sample_count', 'pass', 'fail', 'unknown', 'wilson95', 'demand_coverage')}
                    for r in candidate['records']
                    if r['variant'] == vid and r['eligible_local_beta']}
        demands = {d for r in families.values() for d in r['demand_coverage']}
        for row in candidate['records']:
            if row['variant'] == vid and row['family'] in families and row.get('usage_profile'):
                families[row['family']]['usage_profile'] = copy.deepcopy(row['usage_profile'])
        value['variants'][vid] = {
            'model': identity['model'], 'effort': identity['effort'], 'enabled': True,
            # These are the selector's legal known operations, not independent
            # model capability claims. CalibratedRouter requires the exact
            # measured family/demand cell before this generic selector runs.
            'tasks': ['creative', 'transform', 'analysis', 'design'],
            'max_demand': max(demands, key={'simple':0, 'standard':1, 'deep':2}.get),
            'cost_band': 2, 'cache_write_mode': 'separate',
            'calibration_contract': {'revision': candidate['revision'],
                'source_sha256': candidate['source_sha256'], 'families': families,
                'transport': identity['transport'], 'output_allowance': identity['total_output_allowance'],
                'production_promotion_allowed': False}}
        if 'requested_reasoning_fields' in identity:
            value['variants'][vid]['calibration_contract']['reasoning_fields'] = copy.deepcopy(identity['requested_reasoning_fields'])
    return value


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
