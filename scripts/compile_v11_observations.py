"""Export sealed V11 observations without granting runtime eligibility.

No model calls or runtime imports. --source-root is the model-efforts-v11
experiment directory; --output is a single evidence JSON file.
"""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path


SOURCES = {
    'SUMMARY.json': 'f7d41aff3cd17021d0ae4aa216e3ba60b9f03e2f51a46e477371fddeb6de9ca0',
    'evidence/audit-r1/2026-09-18T085312595807+0000-audit.json': 'f83ecc3ffcb561b0a6867aa52da73bd7fafe59911852d06708117a3893697ee9',
    'evidence/regrade-r1/summary.json': '6ae9d44308ea9405b861c9471bfadd48b83293acb112e852a3c3d8b95cd23b5d',
    'evidence/recovery-r1/dataset.json': 'c897828659543f880186933148f409a5cb56c20971d1b30e5752b067b88f411d',
    'evidence/recovery-r1/freeze.json': '3dec96ce14ea159e65042800897d24b7ada0479e3cf8f485b257c6c5e8d911a1',
}
AUDIT = 'evidence/audit-r1/2026-09-18T085312595807+0000-audit.json'
STATES = ('pass', 'fail', 'unknown')
DEMANDS = ('simple', 'standard', 'deep')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def counts(rows):
    return {s: sum(r['status'] == s for r in rows) for s in STATES}


def wilson(passed, total):
    z = 1.959963984540054
    p = passed / total
    center = (p + z*z/(2*total)) / (1 + z*z/total)
    half = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / (1 + z*z/total)
    return [round(max(0, center-half), 6), round(min(1, center+half), 6)]


def paired(rows, presets):
    indexed = {(r['preset'], r['task_id']): r['status'] for r in rows}
    result = []
    for name, contract in sorted(presets.items()):
        if contract['effort'] is None:
            continue
        baseline, = [n for n, c in presets.items()
                     if c['model'] == contract['model'] and c['effort'] is None]
        selected = [r for r in rows if r['preset'] == name]
        transitions = Counter(indexed[baseline, r['task_id']]+'->'+r['status'] for r in selected)
        result.append({'preset': name, 'baseline': baseline, 'paired_tasks': len(selected),
                       'paired_status': dict(sorted(transitions.items())),
                       'lost_passes': sorted(r['task_id'] for r in selected
                           if indexed[baseline, r['task_id']] == 'pass' and r['status'] != 'pass'),
                       'gained_passes': sorted(r['task_id'] for r in selected
                           if indexed[baseline, r['task_id']] != 'pass' and r['status'] == 'pass')})
    return result


def compile_observations(summary, audit, regrade, dataset, supplements, provenance):
    require(summary['production_promotion_allowed'] is False
            and dataset['production_promotion_allowed'] is False, 'Not a diagnostic dataset')
    tasks = [t for s in dataset['sessions'] for t in s['steps'] if t['scored']]
    task_map = {t['id']: t for t in tasks}
    require(len(tasks) == len(task_map) == 75 and all(t['split'] == 'calibration' and t['cap'] == 32000 for t in tasks),
            'Expected 75 unique calibration tasks; held-out input is forbidden')
    families = Counter(t['family'] for t in tasks)
    require(len(families) == 20 and sorted(families.values()) == [3]*5+[4]*15,
            'Expected twenty three/four-sample families')
    presets = dataset['presets']
    models = {c['model'] for c in presets.values()}
    require(len(presets) == 20 and len(models) == 5, 'Expected five models and twenty presets')
    for model in models:
        require(Counter(c['effort'] for c in presets.values() if c['model'] == model)
                == Counter([None, 'low', 'medium', 'high']), 'Incomplete matched effort coverage')
    rows = summary['rows']
    indexed = {(r['preset'], r['task_id']): r for r in rows}
    require(len(rows) == len(indexed) == 1500 and summary['completed_outcomes'] == 1500,
            'Expected 1500 unique original outcomes')
    require(set(indexed) == {(p, t) for p in presets for t in task_map}, 'Unmatched task/preset coverage')
    for r in rows:
        task = task_map[r['task_id']]
        require(r['status'] in STATES and r['family'] == task['family']
                and r['difficulty'] == task['difficulty'] and r['difficulty'] in DEMANDS,
                'Outcome/task mismatch')
        require(len(r['source_sha256']) == 64, 'Missing artifact digest')
    require(counts(rows) == audit['frozen_counts'] and audit['primary_results_modified'] is False,
            'Audit must describe unchanged original outcomes')
    annotations = {}
    for c in audit['cases']:
        key = (c['preset'], c['task_id'])
        require(key not in annotations and c['source_sha256'] == indexed[key]['source_sha256']
                and c['original_status'] == indexed[key]['status'], 'Audit source mismatch')
        annotations[key] = c['category']
    require(dict(Counter(annotations.values())) == audit['categories'], 'Audit category mismatch')
    secondary = {key: dict(r) for key, r in indexed.items()}
    regraded = []
    require(regrade['parent_results_unchanged'] is True and regrade['replayed_judgments'] == 4
            and len(regrade['results']) == 4, 'Expected the four targeted regrades only')
    for r in regrade['results']:
        key = (r['preset'], r['task_id'])
        original = indexed[key]
        require(original['status'] == r['original_status'] == 'unknown'
                and r['source_sha256'] == original['source_sha256']
                and key not in {(v['preset'], v['task_id']) for v in regraded}, 'Regrade source mismatch')
        require(r['secondary_status'] in STATES and
                (r['secondary_status'] != 'pass' or (r['judge_valid'] and r['judge_pass'] is True)),
                'Invalid judgment cannot pass')
        secondary[key]['status'] = r['secondary_status']
        regraded.append({k: r[k] for k in ('preset', 'task_id', 'source_sha256', 'original_status',
            'secondary_status', 'judge_valid', 'judge_pass', 'cost')})
    preset_records = {}
    for name, contract in sorted(presets.items()):
        selected = [r for r in rows if r['preset'] == name]
        detail = summary['presets'][name]
        require(counts(selected) == {s: detail['outcomes'].get(s, 0) for s in STATES}, 'Preset count mismatch')
        native = 'anthropic' in contract['model']
        effort = contract['effort']
        request_fields = ({'thinking': {'type': 'adaptive', 'display': 'summarized'},
                           'output_config': {'effort': effort}} if native else {'reasoning': {'effort': effort}})
        generation = detail['generation']
        preset_records[name] = {**contract, 'actual_effort': 'not_reported',
            'transport': 'anthropic_messages' if native else 'chat_completions',
            'requested_reasoning_fields': request_fields if effort is not None else {},
            'total_output_allowance': 32000, 'output_allowance_semantics': 'total_thinking_plus_answer',
            'original_outcomes': counts(selected), 'generation': generation,
            'known_generation_usd_per_original_pass': str(Decimal(generation['known_usd']) / counts(selected)['pass'])
                if counts(selected)['pass'] else None}
    require(sum(v['generation']['calls'] for v in preset_records.values()) == summary['costs']['generation']['calls']
            and sum(v['generation']['unknown_charges'] for v in preset_records.values())
                == summary['costs']['generation']['unknown_charges']
            and sum((Decimal(v['generation']['known_usd']) for v in preset_records.values()), Decimal(0))
                == Decimal(summary['costs']['generation']['known_usd']), 'Generation cost double count/mismatch')
    observations = []
    for name in sorted(presets):
        for family in sorted(families):
            selected = sorted((r for r in rows if r['preset'] == name and r['family'] == family), key=lambda r: r['task_id'])
            original = counts(selected)
            observations.append({'preset': name, 'family': family, 'sample_count': len(selected),
                'original_outcomes': original, 'original_pass_wilson95': wilson(original['pass'], len(selected)),
                'targeted_regrade_outcomes': counts([secondary[name, r['task_id']] for r in selected]),
                'support_by_difficulty_original': {d: counts([r for r in selected if r['difficulty'] == d]) for d in DEMANDS},
                'support_by_difficulty_targeted_regrade': {d: counts([secondary[name, r['task_id']] for r in selected
                    if r['difficulty'] == d]) for d in DEMANDS},
                'audit_flags': dict(sorted(Counter(annotations[name, r['task_id']] for r in selected
                    if (name, r['task_id']) in annotations).items())),
                'cost_reference': 'presets.'+name+'.generation',
                'tasks': [{k: r[k] for k in ('task_id', 'difficulty', 'status', 'source_sha256')} for r in selected]})
    for s in supplements:
        require(s['source_sha256'] == indexed[s['preset'], s['task_id']]['source_sha256']
                and s['source_repaired'] is False, 'Supplement must reference unchanged source')
    return {'schema_version': 1, 'revision': 'v11-observations-'+SOURCES['SUMMARY.json'][:16],
        'purpose': 'measured_effort_observations', 'runtime_eligibility_granted': False,
        'production_promotion_allowed': False, 'provenance': provenance,
        'assessment_contract': {
            'objective': 'Exact semantic values and required tool/source behavior; raw formatting reported separately.',
            'subjective': 'Both Terra and native Sonnet 4.6 judges valid, each relevance/completeness/correctness_grounding >=3 of4, no material error; required independent code/allocation checks also pass.',
            'judge_qualification': 'Provisional inherited qualification; valid disagreement is retained, not adjudicated into success.',
            'sample_support': 'Only three or four matched calibration tasks per family/preset. Wilson bounds describe observed original successes, not runtime qualification.'},
        'presets': preset_records, 'observations': observations,
        'original_outcomes': counts(rows), 'targeted_regrade_outcomes': counts(list(secondary.values())),
        'targeted_regrades': sorted(regraded, key=lambda r: (r['preset'], r['task_id'])),
        'paired_comparisons_original': paired(rows, presets),
        'paired_comparisons_targeted_regrade': paired(list(secondary.values()), presets),
        'audit_categories': audit['categories'],
        'source_shape_ambiguities': [{'preset': p, 'task_id': t, 'source_sha256': indexed[p, t]['source_sha256']}
            for (p, t), category in sorted(annotations.items()) if category == 'source_shape_ambiguity'],
        'supplementary_code': supplements,
        'costs': {**summary['costs'], 'targeted_regrade_judges': regrade['cost']},
        'recovery': summary['recovery'],
        'limitations': [*summary['limits'],
            'No runtime consumer reads this file; it adds no model, effort or family eligibility.',
            'Original strict outcomes, targeted regrades and offline extraction evidence are separate; no best-answer selection.',
            'Dual-provider judgments must both pass the unchanged semantic rubric; valid disagreement remains a failure in the original score.',
            'Source-shape ambiguity annotations do not overwrite the nineteen original failures.',
            'Native Anthropic measurements do not qualify OpenAI-compatible/disabled-thinking transport.',
            'Preset generation costs include paid setup/tool calls once; rubric cells reference rather than duplicate them. Unknown charges are not zero.',
            'Known cost per original pass excludes unknown charges and separate grading/readiness; it is not an invoice or a runtime ranking.',
            'Supplementary code checks verify unchanged implementations, not generated test correctness or overall judged quality.']}


def load_export(root):
    values = {}
    provenance = {'experiment': 'model-efforts-v11', 'sources': dict(SOURCES),
                  'compiler_sha256': sha(Path(__file__).read_bytes())}
    for name, expected in SOURCES.items():
        raw = (root/name).read_bytes()
        require(sha(raw) == expected, 'Sealed input changed: '+name)
        values[name] = json.loads(raw)
    freeze = values['evidence/recovery-r1/freeze.json']
    require(freeze['dataset_sha256'] == SOURCES['evidence/recovery-r1/dataset.json'], 'Dataset freeze mismatch')
    require(values['SUMMARY.json']['freeze_sha256'] == SOURCES['evidence/recovery-r1/freeze.json']
            == values[AUDIT]['freeze_sha256'], 'Campaign freeze mismatch')
    provenance['generation_source_hashes'] = freeze['sources']
    provenance['prices_sha256'] = freeze['prices_sha256']
    supplements = []
    for path in sorted((root/'evidence/audit-r1/code').rglob('*.json')):
        raw = path.read_bytes()
        value = json.loads(raw)
        require(sha(value['code'].encode()) == value['code_sha256'], 'Supplement code hash mismatch')
        name = str(path.relative_to(root))
        provenance['sources'][name] = sha(raw)
        supplements.append({'preset': path.parent.name, 'task_id': path.stem.split('--')[0],
            **{k: value[k] for k in ('source_sha256', 'code_sha256', 'source_repaired', 'evaluation')},
            'container_normalization': value.get('container_normalization'),
            'receipt_path': name, 'receipt_sha256': sha(raw),
            'isolation': value['execution']['isolation'], 'exit_code': value['execution']['exit_code']})
    require(len(supplements) == 11, 'Expected the eleven unchanged-code supplementary receipts')
    return compile_observations(values['SUMMARY.json'], values[AUDIT], values['evidence/regrade-r1/summary.json'],
        values['evidence/recovery-r1/dataset.json'], supplements, provenance)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = load_export(args.source_root)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    main()
