"""Cross-repo contract: the Gateway's handling of every merged policy shape
Configurations can serve (`fixtures/policy-merge-cases.json`, shared format)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing import selector as sel
from routing.policy import runtime_policy, validate, PolicyError
from test_v5_selector import artifact, install

CASES = json.loads((Path(__file__).parent/'fixtures/policy-merge-cases.json').read_text())['cases']


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
def test_gateway_handles_each_merged_policy_shape(case, tmp_path, monkeypatch):
    if case.get('requires_installed'):
        directory = tmp_path/'installed'; directory.mkdir()
        value = artifact(); value['id'] = case['requires_installed']
        install(directory, value)
        monkeypatch.setattr(sel, 'ARTIFACT_DIR', directory)
    expected = case['gateway']
    policy, mode, error = runtime_policy({'enabled': True, 'policy': case['policy']})
    assert (mode, error) == (expected['mode'], expected['error'])
    if expected['error'] == 'POLICY_INVALID':
        with pytest.raises(PolicyError):
            validate(case['policy'])
        return
    assert policy['clamped'] == expected['clamped']
    assert policy['output'] == expected.get('output', case['policy']['output'])
    # Nothing else is coerced: every other served value is used as served.
    assert {k: v for k, v in policy.items() if k not in {'clamped', 'output'}} == \
        {k: v for k, v in case['policy'].items() if k != 'output'}
