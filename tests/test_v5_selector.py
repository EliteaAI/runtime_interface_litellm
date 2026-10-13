"""V5 selector artifact loader and pure-Python scorer (#6722 V5 PLAN 3.1).

Interim vectors (`fixtures/v5-stub-vectors.json`) are produced by this package
for the stub artifact; WP-1's `test_vectors.json` replaces them together with
its verbatim `scorer.py`.
"""
import copy
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing import selector as sel

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT/'routing/selector/selector-v5-stub.json'
VECTORS = json.loads((ROOT/'tests/fixtures/v5-stub-vectors.json').read_text())


def artifact():
    return json.loads(STUB.read_text())


def install(directory, value, *, stub=False, manifest_sha=None):
    value = copy.deepcopy(value)
    value['sha256'] = sel.canonical_sha256(value)
    name = value['id']+'.json'
    (directory/name).write_text(json.dumps(value))
    (directory/'MANIFEST.json').write_text(json.dumps({'artifacts': {value['id']: {
        'file': name, 'sha256': manifest_sha or value['sha256'], 'stub': stub}}}))
    return value


def test_shipped_stub_matches_manifest_and_is_flagged_stub():
    manifest = sel.manifest()
    entry = manifest['artifacts']['selector-v5-stub']
    loaded = sel.installed('selector-v5-stub')
    assert loaded.sha256 == entry['sha256'] == artifact()['sha256'] == sel.canonical_sha256(artifact())
    assert loaded.stub is True and entry['stub'] is True
    assert set(loaded.heads) == {'openai/gpt-5-6-luna|default', 'openai/gpt-5-4|medium'}


@pytest.mark.parametrize('vector', VECTORS['vectors'], ids=lambda v: v['input']['request_text'][:20] or 'empty')
def test_scores_match_stored_vectors(vector):
    loaded = sel.load(STUB)
    assert loaded.sha256 == VECTORS['artifact_sha256']
    features = loaded.features(**vector['input'])
    assert len(features) == vector['nonzero_features']
    scores = loaded.score(features)
    for key, expected in vector['scores'].items():
        assert scores[key]['expected_output_tokens'] == expected['expected_output_tokens']
        assert scores[key]['p_pass'] == pytest.approx(expected['p_pass'], abs=1e-11)


def test_features_are_deterministic_and_bounded():
    loaded = sel.load(STUB)
    first = loaded.features('Prove it', 1, 2, True, 300)
    assert first == loaded.features('Prove it', 1, 2, True, 300)
    assert all(0 <= k < loaded.dim+len(loaded.numeric) for k in first)
    assert first[loaded.dim+2] == 2.0 and first[loaded.dim+3] == 1.0
    # Text beyond the feature bound does not change the hashed vector.
    long = 'a b c '*10000
    assert loaded.features(long, 0, 0, False, 0).keys() == loaded.features(long+'zzz', 0, 0, False, 0).keys()


def test_score_is_fast_enough_for_a_10kb_request():
    loaded = sel.load(STUB)
    text = ' '.join(f'token{i} recovery design {i % 97}' for i in range(500))[:10000]
    start = time.perf_counter()
    loaded.score(loaded.features(text, 4, 3, False, 12000))
    # Interim scorer bound (generous for CI); WP-1's scorer owns the 2 ms target.
    assert time.perf_counter()-start < 0.25


@pytest.mark.parametrize('mutate', [
    lambda a: a.update(schema='elitea-auto-selector/2'),
    lambda a: a.update(id='Bad Id'),
    lambda a: a['features'].update(hashing_dim=16385),
    lambda a: a['features'].update(word_ngrams=[2, 1]),
    lambda a: a['features'].update(numeric=['prompt_text']),
    lambda a: a['features'].update(lowercase='yes'),
    lambda a: a.update(heads={}),
    lambda a: a['heads'].update({'GPT-5|default': copy.deepcopy(a['heads']['openai/gpt-5-4|medium'])}),
    lambda a: a['heads']['openai/gpt-5-4|medium']['weights'].update({'99999': 1.0}),
    lambda a: a['heads']['openai/gpt-5-4|medium']['weights'].update({'1': float('inf')}),
    lambda a: a['heads']['openai/gpt-5-4|medium'].update(bias=True),
    lambda a: a['heads']['openai/gpt-5-4|medium']['output_tokens'].update(floor=0),
    lambda a: a['heads']['openai/gpt-5-4|medium'].pop('calibration'),
])
def test_loader_refuses_invalid_artifacts_even_with_a_valid_hash(tmp_path, mutate):
    value = artifact()
    mutate(value)
    value['sha256'] = sel.canonical_sha256(value) if 'inf' not in repr(value) else 'x'
    path = tmp_path/'a.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        sel.load(path)


def test_loader_refuses_tampered_content_and_oversize(tmp_path, monkeypatch):
    value = artifact()
    value['heads']['openai/gpt-5-4|medium']['bias'] = 9.0  # hash not recomputed
    (tmp_path/'t.json').write_text(json.dumps(value))
    with pytest.raises(ValueError, match='hash'):
        sel.load(tmp_path/'t.json')
    monkeypatch.setattr(sel, 'MAX_ARTIFACT_BYTES', 10)
    with pytest.raises(ValueError, match='size'):
        sel.load(STUB)


def test_installed_requires_manifest_entry_and_matching_hash(tmp_path, monkeypatch):
    value = artifact(); value['id'] = 'selector-v5-t1'
    install(tmp_path, value, manifest_sha='0'*64)
    monkeypatch.setattr(sel, 'ARTIFACT_DIR', tmp_path)
    with pytest.raises(ValueError, match='manifest'):
        sel.installed('selector-v5-t1')
    with pytest.raises(ValueError):
        sel.installed('selector-v5-missing')
    with pytest.raises(ValueError):
        sel.installed(None)
    other = tmp_path/'other'; other.mkdir()
    install(other, value)
    monkeypatch.setattr(sel, 'ARTIFACT_DIR', other)
    loaded = sel.installed('selector-v5-t1')
    assert loaded.id == 'selector-v5-t1' and loaded.stub is False
