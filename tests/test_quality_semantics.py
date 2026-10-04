"""Identical enum values must not imply compatibility across meaning changes."""
import pytest

from routing import service
from routing.quality import frontier, validate
from routing.v7.task_profile import PROFILE_SEMANTICS_VERSION
from test_auto_routing_service import request
from test_quality_contract_v2 import SCOPE
from test_quality_development_v4 import development, args_v4
from test_quality_workload_v3 import scoped_quality, classify
from test_v14_installed_bundle import installed
from test_v14_qualification import PROFILE


@pytest.mark.parametrize('version', [3, 4])
def test_reviewed_semantics_bind_to_current_classifier(version):
    q, variants = scoped_quality()
    if version == 4:
        q = development(q)
    q['profile_semantics'] = PROFILE_SEMANTICS_VERSION
    validate(q, variants)
    result = frontier(q, variants, 'standard', request_scope=SCOPE, work_profile=PROFILE)
    assert result['eligible'] == list(variants)
    assert result['profile_semantics'] == PROFILE_SEMANTICS_VERSION


@pytest.mark.parametrize('semantics', ['current-deliverable-3.1', '', None, 32])
def test_unchanged_profile_values_cannot_reuse_incompatible_meaning(semantics):
    q, variants = scoped_quality(); q = development(q)
    q['profile_semantics'] = semantics
    with pytest.raises(ValueError, match='profile semantics'):
        validate(q, variants)
    result = frontier(q, variants, 'standard', request_scope=SCOPE, work_profile=PROFILE)
    assert result['status'] == 'quality_profile_semantics_mismatch'
    assert result['eligible'] == []


def test_legacy_policy_does_not_claim_reviewed_semantics():
    q, variants = scoped_quality(); q = development(q)
    validate(q, variants)
    result = frontier(q, variants, 'standard', request_scope=SCOPE, work_profile=PROFILE)
    assert result['eligible'] == list(variants)
    assert 'profile_semantics' not in result


def test_gateway_rejects_semantic_drift_before_classifier_or_fallback(installed):
    args = args_v4(installed)
    args['settings']['calibration_selection_policy']['quality']['profile_semantics'] = 'current-deliverable-3.1'
    with pytest.raises(ValueError, match='profile semantics'):
        service.resolve(request('Implement this contract.'),
                        complete=lambda *a, **k: pytest.fail('Stale evidence dispatched inference'), **args)


def test_gateway_traces_reviewed_semantics_without_changing_price_rules(installed):
    args = args_v4(installed)
    args['settings']['calibration_selection_policy']['quality']['profile_semantics'] = PROFILE_SEMANTICS_VERSION
    result = service.resolve(request('Implement this contract.'), complete=classify, **args)
    assert result['trace']['selection']['quality_screen']['profile_semantics'] == PROFILE_SEMANTICS_VERSION
    assert result['trace']['selection']['reason'] == 'MEASURED_TRAJECTORY_EXPENDITURE'
