"""#6722: the offline builder reproduces the reviewed-demand numbers; the shipped table is pinned.

The dataset itself is never committed. ``fixtures/admission-view-sample.jsonl.gz`` is a field-reduced
extract (no request or answer text, hashed ids) holding every row of the eight heads discussed in the
review plus a few rows of each excluded kind, so the same builder code runs on it.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing.v7 import build_admission_table as builder

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / 'routing' / 'v7' / 'admission-table.json'
SAMPLE = ROOT / 'tests' / 'fixtures' / 'admission-view-sample.jsonl.gz'
DATASET = Path(os.environ.get('R81_DATASET', '/Users/romanmitusov/Documents/Work/.claude/autorouting_impl/'
                                              'dataset-r81/dataset.jsonl'))
# Changing the table changes this digest, the policy revision and every pin issued before it.
SHIPPED_TABLE_SHA256 = '5eef4a1a250aa781c0a11d7b52ffd2eb494301860d9eb1e0f5c4cb4413cf8d31'
SHIPPED_DATASET_SHA256 = 'f5509ecf25fa1e4d49079c8f9f677c4c132d549078079c94ec4adefa548d826d'

LUNA, LUNA_H = 'openai/gpt-5-6-luna|default', 'openai/gpt-5-6-luna|high'
TERRA, SOL, SOL_H = 'openai/gpt-5-6-terra|default', 'openai/gpt-5-6-sol|default', 'openai/gpt-5-6-sol|high'
SONNET, OPUS, OPUS_H = ('anthropic/claude-sonnet-5-5|default', 'anthropic/claude-opus-5-5|default',
                        'anthropic/claude-opus-5-5|high')


@pytest.fixture(scope='module')
def sample():
    return builder.build(SAMPLE, generated_at='test')


def cell(table, head, demand):
    return table['heads'][head]['cells'][demand]


# --- section I of the review: simple -------------------------------------------------------------------------
def test_simple_best_is_luna_default_and_every_current_variant_is_eligible(sample):
    best = cell(sample, LUNA, 'simple')
    assert (best['pass'], best['n']) == (162, 162) and best['best_head'] == LUNA
    for head, low, high in [(LUNA_H, 87, 90), (TERRA, 159, 162), (SOL_H, 87, 90), (SONNET, 87, 90), (OPUS_H, 159, 162)]:
        row = cell(sample, head, 'simple')
        assert low <= row['pass'] <= high and row['tier'] == 'eligible', head
        assert row['adverse'] <= 3 and row['adverse'] / row['paired_n'] <= 0.026, head
        assert 0.949 <= row['lb90'] <= 0.992, head


# --- standard ------------------------------------------------------------------------------------------------
def test_standard_numbers(sample):
    luna = cell(sample, LUNA, 'standard')
    assert (luna['pass'], luna['n'], luna['tier']) == (74, 75, 'eligible')
    assert round(luna['lb90'], 3) == 0.959 and (luna['adverse'], luna['paired_n']) == (1, 21)
    terra = cell(sample, TERRA, 'standard')
    assert (terra['pass'], terra['n'], terra['tier']) == (72, 75, 'eligible')
    assert (terra['adverse'], terra['paired_n']) == (1, 21)
    for head in (SOL_H, SONNET, LUNA_H):
        row = cell(sample, head, 'standard')
        assert (row['pass'], row['n'], row['tier']) == (21, 21, 'eligible'), head
    opus = cell(sample, OPUS_H, 'standard')
    assert (opus['adverse'], opus['paired_n']) == (2, 21) and opus['tier'] == 'out'
    # The best head ties at 21/21; first appearance in the dataset picks Sol default.
    assert luna['best_head'] == SOL


# --- deep ----------------------------------------------------------------------------------------------------
def test_deep_numbers_and_tiers(sample):
    sol = cell(sample, SOL_H, 'deep')
    assert (sol['pass'], sol['n'], sol['tier']) == (38, 38, 'eligible') and round(sol['lb90'], 3) == 0.965
    expected = {TERRA: (63, 65, 2, 'provisional'), SONNET: (36, 38, 2, 'provisional'),
                OPUS_H: (59, 65, 3, 'provisional'), LUNA_H: (35, 38, 3, 'out'), LUNA: (59, 65, 4, 'out')}
    for head, (passed, n, adverse, tier) in expected.items():
        row = cell(sample, head, 'deep')
        assert (row['pass'], row['n'], row['adverse'], row['paired_n'], row['tier']) == (passed, n, adverse, 38, tier), head
    assert round(cell(sample, LUNA_H, 'deep')['lb90'], 3) == 0.849  # fails the 0.85 floor by 0.001
    assert round(cell(sample, LUNA, 'deep')['lb90'], 3) == 0.853
    assert round(cell(sample, OPUS_H, 'deep')['lb90'], 3) == 0.853
    assert round(cell(sample, TERRA, 'deep')['lb90'], 3) == 0.931
    assert round(cell(sample, SONNET, 'deep')['lb90'], 3) == 0.883


def test_opus_default_is_out_at_deep_and_standard(sample):
    assert cell(sample, OPUS, 'deep')['tier'] == 'out'
    assert cell(sample, OPUS, 'standard')['tier'] == 'out'


def test_expected_output_p90_is_recorded_per_cell(sample):
    for head, demand in [(LUNA, 'simple'), (TERRA, 'deep'), (SOL_H, 'deep')]:
        row = cell(sample, head, demand)
        assert row['expected_output_tokens_p90'] > 0 and row['output_n'] == row['n']
    assert sample['output_field'] == 'usage.output'


# --- the sample agrees with the shipped table and the full dataset ----------------------------------------------
def test_sample_cells_equal_the_shipped_table_cells(sample):
    shipped = json.loads(TABLE.read_text())
    for head, entry in sample['heads'].items():
        assert entry['cells'] == shipped['heads'][head]['cells'], head


def test_shipped_table_digest_is_pinned():
    assert hashlib.sha256(TABLE.read_bytes()).hexdigest() == SHIPPED_TABLE_SHA256
    table = json.loads(TABLE.read_text())
    assert table['dataset_sha256'] == SHIPPED_DATASET_SHA256 and table['rule_revision'] == builder.RULE_REVISION


def test_shipped_table_holds_counts_and_rates_only():
    def walk(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                assert key not in {'request_text', 'answer_text', 'judges', 'task_id', 'lineage_ids'}, key
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)
        elif isinstance(value, str):
            assert len(value) < 120
    walk(json.loads(TABLE.read_text()))


@pytest.mark.skipif(not DATASET.exists(), reason='offline R81 dataset not available on this machine')
def test_full_dataset_rebuild_reproduces_the_shipped_table_byte_for_byte():
    shipped = json.loads(TABLE.read_text())
    rebuilt = builder.build(DATASET, generated_at=shipped['generated_at'])
    assert builder.dumps(rebuilt) == TABLE.read_text()
    assert rebuilt['dataset_sha256'] == SHIPPED_DATASET_SHA256


def test_build_is_deterministic(sample):
    assert builder.dumps(sample) == builder.dumps(builder.build(SAMPLE, generated_at='test'))


# --- the view: what is kept and what is excluded ----------------------------------------------------------------
def write(tmp_path, rows):
    path = tmp_path / 'rows.jsonl'
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return path


def row(task, passed=True, *, head=('global.openai.gpt-5.6-luna', None), demand='standard', wave='v14-r65',
        arm='a', outcome=None, reviewed=True, family='analysis', lineage=None, output=100, authored=None):
    return {'wave': wave, 'task_id': task, 'arm': arm, 'model': head[0], 'effort': head[1],
            'outcome': outcome or ('pass' if passed else 'fail'),
            'reviewed_demand': demand if reviewed else None, 'authored_difficulty': authored,
            'family': family, 'lineage': lineage, 'usage': {'output': output}}


def test_view_excludes_unknown_template_difficulty_routed_arms_and_unmodelled_rows(tmp_path):
    rows = [row('t1'), row('t2', outcome='unknown'), row('t3', reviewed=False, authored='standard', wave='v13-benchmark'),
            row('t4', wave='v14-r53', arm='candidate'), row('t5', wave='v14-r53', arm='retained'),
            row('t6', head=(None, None)), row('t7', reviewed=False, authored='deep', wave='v14-r80'),
            row('t8', reviewed=False, authored='deep', wave='v14-r65')]
    table = builder.build(write(tmp_path, rows), generated_at='x')
    assert table['rows_kept'] == 2  # t1 and the authored-difficulty row of an authored wave
    assert table['heads'][LUNA]['cells']['standard']['n'] == 1
    assert table['heads'][LUNA]['cells']['deep']['n'] == 1


def test_refusal_and_incomplete_count_as_non_pass(tmp_path):
    rows = [row('a'), row('b', outcome='refusal'), row('c', outcome='incomplete'), row('d', outcome='fail')]
    cell_ = builder.build(write(tmp_path, rows), generated_at='x')['heads'][LUNA]['cells']['standard']
    assert (cell_['pass'], cell_['n']) == (1, 4)


def test_arms_merge_by_canonical_head(tmp_path):
    rows = [row('a', arm='fixed-luna', head=('global.openai.gpt-5.6-luna', None)),
            row('b', arm='luna-default', head=('openai/gpt-5.6-luna@default', 'default')),
            row('c', arm='opus55-high', head=('global.anthropic.claude-opus-5-5', 'high')),
            row('d', arm='fixed-opus55-high', head=('eu.anthropic.claude-opus-5-5', 'high'))]
    heads = builder.build(write(tmp_path, rows), generated_at='x')['heads']
    assert heads[LUNA]['cells']['standard']['n'] == 2 and heads[OPUS_H]['cells']['standard']['n'] == 2


def test_canonical_head_keys_match_the_lab_helpers():
    cases = {('global.openai.gpt-5.6-luna', None): LUNA, ('global.openai.gpt-5.6-sol', 'high'): SOL_H,
             ('global.anthropic.claude-opus-5-5', 'high'): OPUS_H, ('gpt-5.4', 'medium'): 'openai/gpt-5-4|medium',
             ('eu.anthropic.claude-haiku-4-5-20251001-v1:0', None): 'anthropic/claude-haiku-4-5|default',
             ('gpt-5.4-mini', 'low'): 'openai/gpt-5-4-mini|low'}
    for (model, effort), key in cases.items():
        assert builder.head_key(model, effort) == key
    assert builder.head_key('unknown-vendor-model', None) is None and builder.head_key(None, None) is None


# --- the statistics ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize('passed,n,expected', [(38, 38, 0.965), (21, 21, 0.938), (162, 162, 0.992),
                                               (63, 65, 0.931), (35, 38, 0.849), (0, 10, 0.0)])
def test_jeffreys_lower_bound_matches_reference_values(passed, n, expected):
    assert round(builder.jeffreys_lower(passed, n), 3) == expected


def test_nearest_rank_percentile():
    assert builder.nearest_rank(list(range(1, 11)), 0.9) == 9 and builder.nearest_rank([5], 0.9) == 5
    assert builder.nearest_rank([], 0.9) is None


# --- tiers and the paired adverse count ---------------------------------------------------------------------------
def against_best(tmp_path, demand, n, failures):
    """The best head (Sol high) passes all `n` items; Luna default fails the first `failures` of them."""
    rows = [row(f't{i}', head=('global.openai.gpt-5.6-sol', 'high'), demand=demand) for i in range(n)]
    rows += [row(f't{i}', i >= failures, demand=demand) for i in range(n)]
    return builder.build(write(tmp_path, rows), generated_at='x')['heads'][LUNA]['cells'][demand]


@pytest.mark.parametrize('failures,tier', [(0, 'eligible'), (1, 'eligible'), (2, 'eligible'), (5, 'eligible'),
                                           (6, 'out'), (8, 'out')])
def test_simple_and_standard_adverse_tolerance_is_five_percent_with_no_provisional_tier(tmp_path, failures, tier):
    for demand in ('simple', 'standard'):
        got = against_best(tmp_path, demand, 100, failures)  # lb90 stays above 0.90 up to 6 failures
        assert (got['adverse'], got['paired_n'], got['tier']) == (failures, 100, tier)


@pytest.mark.parametrize('failures,tier', [(0, 'eligible'), (2, 'eligible'), (3, 'provisional'), (4, 'out')])
def test_deep_has_a_provisional_tier_capped_at_three_adverse_items(tmp_path, failures, tier):
    got = against_best(tmp_path, 'deep', 50, failures)  # lb90 stays above 0.85 up to 4 failures
    assert (got['adverse'], got['paired_n'], got['tier']) == (failures, 50, tier)


def test_lower_bound_floor_decides_before_adverse(tmp_path):
    got = against_best(tmp_path, 'deep', 25, 0)
    assert got['tier'] == 'eligible'
    rows = [row(f't{i}', head=('global.openai.gpt-5.6-sol', 'high'), demand='deep') for i in range(25)]
    rows += [row(f'u{i}', i >= 4, demand='deep', wave='v14-r80') for i in range(25)]  # 21/25, no common item
    low = builder.build(write(tmp_path, rows), generated_at='x')['heads'][LUNA]['cells']['deep']
    assert low['lb90'] < 0.85 and low['tier'] == 'out'


def test_no_common_item_means_no_paired_evidence_and_judgement_on_lb_alone(tmp_path):
    rows = [row(f'a{i}', head=('global.openai.gpt-5.6-sol', 'high'), demand='standard') for i in range(25)]
    rows += [row(f'b{i}', demand='standard', wave='v14-r79') for i in range(25)]
    got = builder.build(write(tmp_path, rows), generated_at='x')['heads'][LUNA]['cells']['standard']
    assert got['paired_n'] == 0 and got['adverse'] == 0 and got['tier'] == 'eligible'


def test_best_head_needs_n_at_least_twenty(tmp_path):
    rows = [row(f't{i}', head=('global.openai.gpt-5.6-sol', 'high')) for i in range(10)]  # 10/10 but n < 20
    rows += [row(f't{i}', i != 0) for i in range(30)]
    got = builder.build(write(tmp_path, rows), generated_at='x')['heads']
    assert got[LUNA]['cells']['standard']['best_head'] == LUNA  # the small cell cannot be the reference


# --- family refinement ------------------------------------------------------------------------------------------------
def family_rows(n, lineages, family='analysis', passed=True):
    return [row(f'f{i}', passed, family=family, lineage=f'l{i % lineages}') for i in range(n)]


def test_family_refinement_only_with_twenty_rows_and_eight_lineages(tmp_path):
    for n, lineages, present in [(20, 8, True), (19, 8, False), (20, 7, False), (40, 40, True)]:
        heads = builder.build(write(tmp_path, family_rows(n, lineages)), generated_at='x')['heads']
        assert ('families' in heads[LUNA] and 'analysis' in heads[LUNA]['families']) is present, (n, lineages)


def test_lineage_falls_back_to_the_task_id(tmp_path):
    rows = [row(f'task{i}', family='analysis', lineage=None) for i in range(20)]
    refined = builder.build(write(tmp_path, rows), generated_at='x')['heads'][LUNA]['families']['analysis']['standard']
    assert refined['lineages'] == 20


def test_shipped_family_rows_follow_the_same_thresholds():
    shipped = json.loads(TABLE.read_text())
    for head, entry in shipped['heads'].items():
        for family, by_demand in entry.get('families', {}).items():
            for demand, found in by_demand.items():
                assert found['n'] >= builder.FAMILY_MIN_N and found['lineages'] >= builder.FAMILY_MIN_LINEAGES, (head, family, demand)
