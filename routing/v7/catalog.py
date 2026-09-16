"""Compiled in-memory experiment catalog; qualification is not a price label."""
import copy,json
from pathlib import Path
from .routing import CATALOG
from .retrieval import digest

def compile_catalog():
    value=copy.deepcopy(CATALOG);value['revision']='v6-effort-profiles-1'
    value['qualification']='Provisional task ceilings; effort transport verified on three fixtures per preset; full-workload qualification still measured separately'
    for vid,base,effort,demand in [('gpt54-low','gpt54-medium','low','standard'),('gpt54-high','gpt54-medium','high','deep'),('mini-medium','mini-low','medium','standard')]:
        value['variants'][vid]={**copy.deepcopy(value['variants'][base]),'effort':effort,'max_demand':demand,
            'price_key':base,'qualification_key':vid+':text-tools-v1','transport_evidence':'trajectory-v6/evidence/effort-probe.jsonl'}
    # These are exact frozen transport contracts, not prefix or price inference.
    for variant in value['variants'].values():
        variant['cache_write_mode'] = ('ordinary_input' if variant['model'] in {'gpt-5.4', 'gpt-5.4-mini'} else 'separate')
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
