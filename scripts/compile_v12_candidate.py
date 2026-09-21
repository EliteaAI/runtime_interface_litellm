"""Compile original V11 observations into a provisional runtime effort profile.

No model calls, repaired artifacts, adjudicated votes or held-out outcomes. Raw
generation sources are optional for reproducible expenditure distributions; each
must match its sealed observation digest before any usage is extracted.
"""
import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

OBSERVATION_SHA = '0d78c57251f7e03964d7e06d88b5f94b982f1fa7a0fc96b8fd5e701cee2e0efc'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def distribution(values):
    ordered = sorted(values)
    return {'count': len(ordered), 'min': ordered[0], 'max': ordered[-1],
            'mean': round(sum(ordered) / len(ordered), 4),
            'p90': ordered[math.ceil(.9 * len(ordered)) - 1]}


def usage_profile(tasks, preset, source_root):
    inputs, outputs, input_bytes, calls_per_task = [], [], [], []
    if source_root is None:
        return None
    for task in tasks:
        matches = list((source_root / 'evidence/recovery-r1/generation' / preset).glob('*/' + task['task_id'] + '.json'))
        if len(matches) != 1 or sha(matches[0].read_bytes()) != task['source_sha256']:
            raise ValueError('Generation source does not match original observation')
        row = json.loads(matches[0].read_text())
        if not row['operational_complete']:
            return None
        count = 0
        for attempt in row['attempts']:
            result = attempt.get('result')
            if not result or attempt.get('error'):
                return None  # Never treat an unknown failed call as free.
            usage = result.get('usage') or {}
            inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
            if any(type(x) is not int or x < 0 for x in (inp, out)):
                return None
            inputs.append(inp)
            outputs.append(out)
            request = result.get('request') or {}
            if not isinstance(request.get('messages'), list):
                return None
            framing = {k: request.get(k) for k in ('messages', 'tools', 'system')}
            input_bytes.append(len(json.dumps(framing, ensure_ascii=False).encode()) + 64*len(request['messages']))
            count += 1
        if not count:
            return None
        calls_per_task.append(count)
    return {'tasks': len(tasks), 'input_tokens': distribution(inputs),
            'input_bytes': distribution(input_bytes),
            'input_tokens_per_byte': round(sum(inputs)/sum(input_bytes), 8),
            'output_tokens': distribution(outputs), 'calls_per_task': distribution(calls_per_task),
            'scope': 'Observed scored-task calls, including tools; excludes earlier setup turns',
            'prediction_guarantee': False}


def compile_candidate(observations, source_sha, source_root=None):
    if source_sha != OBSERVATION_SHA:
        raise ValueError('Expected original sealed V11 observations')
    if observations['original_outcomes'] != {'pass': 1278, 'fail': 210, 'unknown': 12}:
        raise ValueError('Original outcomes changed')
    if len(observations['presets']) != 20 or len(observations['observations']) != 400:
        raise ValueError('Incomplete measured preset/family coverage')
    variants = {name: {key: value[key] for key in (
        'model', 'effort', 'transport', 'requested_reasoning_fields',
        'total_output_allowance', 'output_allowance_semantics')}
        for name, value in observations['presets'].items()}
    records = []
    seen = set()
    for cell in observations['observations']:
        key = (cell['preset'], cell['family'])
        if key in seen or cell['preset'] not in variants:
            raise ValueError('Duplicate or unknown preset/family')
        seen.add(key)
        tasks = cell['tasks']
        counts = {status: sum(t['status'] == status for t in tasks) for status in ('pass', 'fail', 'unknown')}
        if counts != cell['original_outcomes'] or len(tasks) != cell['sample_count'] or len(tasks) < 3:
            raise ValueError('Original cell support changed')
        if len({t['task_id'] for t in tasks}) != len(tasks):
            raise ValueError('Duplicate calibration task')
        eligible = counts['pass'] == len(tasks)
        demands = sorted({t['difficulty'] for t in tasks}) if eligible else []
        records.append({'variant': cell['preset'], 'family': cell['family'],
            'sample_count': len(tasks), **counts, 'wilson95': cell['original_pass_wilson95'],
            'demand_coverage': demands, 'eligible_local_beta': eligible,
            'support_by_difficulty': cell['support_by_difficulty_original'],
            'usage_profile': usage_profile(tasks, cell['preset'], source_root) if eligible else None,
            'source_artifacts': [t['source_sha256'] for t in tasks]})
    return {'schema_version': 1, 'revision': 'v12-effort-candidate-' + source_sha[:16],
            'source_sha256': source_sha, 'source_revision': observations['revision'],
            'production_promotion_allowed': False,
            'eligibility_rule': 'Original all-pass full family, exact observed demand, effort, transport and total output allowance; provisional beta only',
            'original_outcomes': observations['original_outcomes'],
            'variants': variants, 'records': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.observations.read_bytes()
    value = compile_candidate(json.loads(raw), sha(raw), args.source_root)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'presets': len(value['variants']), 'cells': len(value['records']),
                      'eligible_cells': sum(r['eligible_local_beta'] for r in value['records']),
                      'sha256': sha(args.output.read_bytes())}))


if __name__ == '__main__':
    main()
