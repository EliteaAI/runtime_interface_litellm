"""Reproduce the local-beta candidate from the sealed calibration export only.

No benchmark answers, retry successes, deployment inventory or per-user mapping
is an input. Run with --source <sealed export> --output routing/v7.
"""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA256 = '869c7592376cf547ebb03c3b293b8284622e47e2727dbb8d9ec1a104340a07dd'
DEMANDS = ('simple', 'standard', 'deep')


def compile_candidate(source, source_sha256):
    if source_sha256 != SOURCE_SHA256 or source.get('production_promotion_allowed') is not False:
        raise ValueError('Expected the original sealed diagnostic calibration export')
    records, variants = [], {}
    seen = set()
    for row in source['profiles']:
        key = (row['variant'], row['family'])
        if key in seen or row['effort'] is not None or row['total_output_allowance'] != 32000:
            raise ValueError('Duplicate or unmeasured preset/output contract')
        seen.add(key)
        tasks = row['tasks']
        counts = {state: sum(t['status'] == state for t in tasks) for state in ('pass', 'fail', 'unknown')}
        if len(tasks) != row['sample_count'] or any(counts[k] != row[k] for k in counts):
            raise ValueError('Calibration count mismatch')
        demands = sorted({t['difficulty'] for t in tasks})
        if demands != row['demand_coverage'] or not set(demands) <= set(DEMANDS):
            raise ValueError('Calibration demand mismatch')
        supported = (row['status'] == 'diagnostic_supported' and len(tasks) in (3, 4)
                     and counts['pass'] == len(tasks) and not counts['fail'] and not counts['unknown'])
        records.append({**{k: row[k] for k in ('variant', 'family', 'sample_count', 'pass', 'fail', 'unknown',
            'attempted', 'wilson95', 'status', 'demand_coverage', 'generation_cost')},
            'eligible_local_beta': supported,
            'tasks': [{k: t[k] for k in ('task_id', 'status', 'difficulty', 'source_sha256')} for t in tasks]})
        identity = {k: row[k] for k in ('model', 'effort', 'actual_effort', 'transport', 'total_output_allowance')}
        existing = variants.setdefault(row['variant'], identity)
        if existing != identity:
            raise ValueError('Variant contract changes between families')
    if len(records) != 100 or len(variants) != 5 or len({r['family'] for r in records}) != 20:
        raise ValueError('Expected five default presets by twenty calibration families')
    return {'schema_version': 1, 'revision': 'v9-local-beta-'+source_sha256[:16],
        'source_revision': source['revision'], 'source_sha256': source_sha256,
        'generation_freeze_sha256': source['generation_freeze_sha256'],
        'production_promotion_allowed': False, 'variants': variants, 'records': records,
        'eligibility_rule': 'Original full family all-pass (3/3 or 4/4), no fail/unknown; exact observed demand only.',
        'operation_coverage': 'Not independently measured; known operation vocabulary is not model qualification.',
        'effort_coverage': 'Provider default only; qualitative demand does not qualify explicit reasoning presets.',
        'limitation': source['limitation'], 'pricing_limitation': source['pricing_limitation']}


def policy_for(candidate, baseline):
    policy = json.loads(json.dumps(baseline))
    policy.setdefault('baseline_revision', baseline['revision'])
    policy.setdefault('baseline_provenance', {key: policy[key] for key in (
        'corpus_sha256', 'records_sha256', 'rubric_revision', 'limitation') if key in policy})
    for key in ('corpus_sha256', 'records_sha256', 'rubric_revision'):
        policy.pop(key, None)
    new = set(candidate['variants'])
    eligible = policy['eligible_by_family']
    for row in candidate['records']:
        family = row['family']
        eligible[family] = [v for v in eligible.get(family, []) if v not in new]
    for row in candidate['records']:
        if row['eligible_local_beta']:
            eligible[row['family']].append(row['variant'])
    policy.update(revision=policy['baseline_revision']+'+'+candidate['revision'],
        calibration_candidate=candidate['revision'], calibration_source_sha256=candidate['source_sha256'],
        calibration_generation_freeze_sha256=candidate['generation_freeze_sha256'],
        eligibility_rule='Retained V7 cohorts unchanged; new V9 contracts require original all-pass full family and exact observed demand, provider-default preset, measured transport and 32000 output allowance.',
        limitation='Retained V7 provenance is baseline_provenance. V9 uses 3/3 or 4/4 original calibration successes, with Wilson 95% lower bounds about 0.44 or 0.51; exact per-cell bounds/counts remain in calibration-v9.json. Both cohorts remain provisional.',
        production_promotion_allowed=False)
    return policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    candidate = compile_candidate(json.loads(raw), hashlib.sha256(raw).hexdigest())
    policy_path = args.output/'qualification-policy.json'
    policy = policy_for(candidate, json.loads(policy_path.read_text()))
    for path, value in ((args.output/'calibration-v9.json', candidate), (policy_path, policy)):
        path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    main()
