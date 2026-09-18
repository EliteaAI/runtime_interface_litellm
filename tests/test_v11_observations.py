"""Evidence-only export contracts, with no workspace dependency or inference."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from routing.v7.catalog import calibration_candidate, compile_catalog


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('compile_v11', ROOT/'scripts/compile_v11_observations.py')
compiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compiler)
DATA = json.loads((ROOT/'routing/v7/calibration-v11-observations.json').read_text())


def inputs():
    """Rebuild a complete synthetic protocol fixture from the shipped compact rows."""
    rows = [{**task, 'preset': cell['preset'], 'family': cell['family']}
            for cell in DATA['observations'] for task in cell['tasks']]
    first = next(iter(DATA['presets']))
    tasks = [{**r, 'id': r['task_id'], 'scored': True, 'split': 'calibration', 'cap': 32000}
             for r in rows if r['preset'] == first]
    dataset = {'production_promotion_allowed': False, 'sessions': [{'steps': [t]} for t in tasks],
               'presets': {p: {k: c[k] for k in ('model', 'effort')} for p, c in DATA['presets'].items()}}
    summary = {'production_promotion_allowed': False, 'completed_outcomes': 1500, 'rows': rows,
        'presets': {p: {'outcomes': c['original_outcomes'], 'generation': c['generation']} for p, c in DATA['presets'].items()},
        'costs': {k: DATA['costs'][k] for k in ('generation', 'judges', 'readiness')},
        'recovery': DATA['recovery'], 'limits': []}
    audit = {'frozen_counts': DATA['original_outcomes'], 'primary_results_modified': False,
             'cases': [], 'categories': {}}
    regrade = {'parent_results_unchanged': True, 'replayed_judgments': 4,
               'results': DATA['targeted_regrades'], 'cost': DATA['costs']['targeted_regrade_judges']}
    return copy.deepcopy([summary, audit, regrade, dataset, [], {}])


def test_shipped_contract_counts_and_original_regrade_separation():
    assert DATA['runtime_eligibility_granted'] is DATA['production_promotion_allowed'] is False
    assert len(DATA['presets']) == 20 and len(DATA['observations']) == 400
    assert DATA['original_outcomes'] == {'pass': 1278, 'fail': 210, 'unknown': 12}
    assert DATA['targeted_regrade_outcomes'] == {'pass': 1281, 'fail': 210, 'unknown': 9}
    remaining, = [r for r in DATA['targeted_regrades'] if not r['judge_valid']]
    assert (remaining['preset'], remaining['task_id'], remaining['secondary_status']) == (
        'sol-default', 'calibration-content_creation-03', 'unknown')
    assert len(DATA['source_shape_ambiguities']) == 19
    assert DATA['audit_categories']['judge_disagreement'] == 161
    assert DATA['audit_categories']['provider_refusal'] == 27
    assert sum(sum(c['original_outcomes'].values()) for c in DATA['observations']) == 1500


def test_deterministic_compilation_and_matched_fresh_default():
    args = inputs()
    result = compiler.compile_observations(*args)
    assert result == compiler.compile_observations(*args)
    assert result['original_outcomes'] == DATA['original_outcomes']
    assert result['targeted_regrade_outcomes'] == DATA['targeted_regrade_outcomes']
    assert result['paired_comparisons_original'] == DATA['paired_comparisons_original']
    assert result['paired_comparisons_targeted_regrade'] == DATA['paired_comparisons_targeted_regrade']
    assert len(result['paired_comparisons_original']) == 15
    assert all(sum(p['paired_status'].values()) == p['paired_tasks'] == 75
               for p in result['paired_comparisons_original'])


def test_unobserved_demand_is_empty_not_supported():
    security = [c for c in DATA['observations'] if c['family'] == 'security_review']
    assert all(c['support_by_difficulty_original']['deep'] == {'pass': 0, 'fail': 0, 'unknown': 0}
               for c in security)
    assert all(sum(c['original_outcomes'].values()) in (3, 4) for c in DATA['observations'])
    all_pass = [c for c in DATA['observations'] if c['original_outcomes']['pass'] == c['sample_count']]
    assert all(c['original_pass_wilson95'][0] < .6 for c in all_pass)


def test_actual_requested_transport_is_not_realized_effort():
    assert {p['actual_effort'] for p in DATA['presets'].values()} == {'not_reported'}
    for p in DATA['presets'].values():
        assert p['total_output_allowance'] == 32000
        if p['effort'] is None:
            assert p['requested_reasoning_fields'] == {}
        elif p['transport'] == 'anthropic_messages':
            assert p['requested_reasoning_fields']['thinking'] == {'type': 'adaptive', 'display': 'summarized'}
            assert p['requested_reasoning_fields']['output_config']['effort'] == p['effort']
        else:
            assert p['requested_reasoning_fields'] == {'reasoning': {'effort': p['effort']}}


def test_supplementary_code_never_overwrites_original_outcome():
    assert len(DATA['supplementary_code']) == 11
    assert sum(c['evaluation']['checks'] for c in DATA['supplementary_code']) == 2841
    for c in DATA['supplementary_code']:
        original, = [t for cell in DATA['observations'] if cell['preset'] == c['preset']
                     for t in cell['tasks'] if t['task_id'] == c['task_id']]
        assert original['status'] == 'unknown'
        assert original['source_sha256'] == c['source_sha256']
        assert c['source_repaired'] is False and c['evaluation']['passed'] is True


def test_costs_count_setup_and_tool_calls_once_and_keep_unknowns():
    result = compiler.compile_observations(*inputs())
    assert result['costs']['generation'] == {'calls': 2334, 'known_usd': '28.041065825', 'unknown_charges': 2}
    assert result['costs']['targeted_regrade_judges']['calls'] == 4
    assert all('generation' not in cell for cell in result['observations'])
    assert all(cell['cost_reference'] == 'presets.'+cell['preset']+'.generation' for cell in result['observations'])


@pytest.mark.parametrize('mutation,match', [
    ('duplicate', '1500 unique'), ('heldout', 'held-out'), ('allowance', 'held-out'),
    ('missing_effort', 'twenty presets'), ('regrade_source', 'Regrade source'),
    ('invalid_pass', 'Invalid judgment'), ('cost', 'cost double count'),
])
def test_rejects_unmatched_or_relabelled_evidence(mutation, match):
    args = inputs()
    if mutation == 'duplicate': args[0]['rows'][-1] = args[0]['rows'][0]
    elif mutation == 'heldout': args[3]['sessions'][0]['steps'][0]['split'] = 'benchmark'
    elif mutation == 'allowance': args[3]['sessions'][0]['steps'][0]['cap'] = 8000
    elif mutation == 'missing_effort': args[3]['presets'].pop('sol-low')
    elif mutation == 'regrade_source': args[2]['results'][0]['source_sha256'] = '0'*64
    elif mutation == 'invalid_pass': args[2]['results'][0]['judge_valid'] = False
    elif mutation == 'cost': args[0]['costs']['generation']['calls'] += 1
    with pytest.raises(ValueError, match=match): compiler.compile_observations(*args)


def test_rejects_changed_sealed_input(tmp_path):
    (tmp_path/'SUMMARY.json').write_text('{}')
    with pytest.raises(ValueError, match='Sealed input changed'): compiler.load_export(tmp_path)


def test_compiler_provenance_is_evidence_only():
    manifest = json.loads((ROOT/'routing/v7/SOURCE-MANIFEST.json').read_text())
    entry = manifest['observation_compilers']['v11']
    assert entry['runtime_eligibility_granted'] is False
    assert compiler.sha((ROOT/entry['path']).read_bytes()) == entry['sha256']
    assert entry['sha256'] == DATA['provenance']['compiler_sha256']
    assert entry['source_sha256'] == DATA['provenance']['sources']['SUMMARY.json']


def test_runtime_loads_compiled_candidate_not_raw_observations(monkeypatch):
    original_read = Path.read_text
    def guarded_read(path, *args, **kwargs):
        assert path.name != 'calibration-v11-observations.json', 'Evidence was read as runtime policy'
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', guarded_read)
    calibration_candidate.cache_clear()
    active = compile_catalog('v9')
    assert len(active['variants']) == 13
    measured = [v for v in active['variants'].values() if v.get('calibration_contract')]
    assert len(measured) == 5 and all(v['effort'] is None for v in measured)
    policy = json.loads((ROOT/'routing/v7/qualification-policy.json').read_text())
    explicit = {p for p, c in DATA['presets'].items() if c['effort'] is not None}
    assert not explicit & {v for vs in policy['eligible_by_family'].values() for v in vs}
    from routing.v7.catalog import effort_candidate
    effort_candidate.cache_clear()
    assert len(compile_catalog()['variants']) == 28
