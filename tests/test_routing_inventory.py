"""Live inventory precedence, qualification joins and exact dispatch ownership."""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing.inventory import (effective_models, model_binding, validate_binding,
                               qualified_inventory, map_bound_model)
from routing.service import resolve, RoutingUnavailable
from test_auto_routing_service import fixture, request, classifier_design


@pytest.mark.parametrize('reverse', [False, True])
def test_project_overrides_shared_whole_configuration_before_admission(reverse):
    shared = {'name': 'model', 'project_id': 1, 'shared': True, 'context_window': 100000,
              'openai_compatible': True}
    private = {'name': 'model', 'project_id': 7, 'context_window': 100,
               'openai_compatible': False, 'available': False, 'exclusion_reason': 'UNHEALTHY'}
    rows = [shared, private]
    if reverse:
        rows.reverse()
    actual = effective_models(rows, 7)
    assert actual == {'model': private}
    admitted, rejected = qualified_inventory(actual, {'variants': {'v': {'model': 'model'}}})
    assert admitted == {}
    assert rejected == [{'model': 'model', 'model_project_id': 7, 'reason': 'UNHEALTHY'}]


def test_unrelated_private_models_do_not_enter_inventory():
    assert effective_models([{'name': 'secret', 'project_id': 8}], 7) == {}


@pytest.mark.parametrize('reverse', [False, True])
def test_project_a_b_plus_shared_b_c_retains_a_b_c(reverse):
    rows = [{'name': 'A', 'project_id': 7}, {'name': 'B', 'project_id': 7},
            {'name': 'B', 'project_id': 1, 'shared': True},
            {'name': 'C', 'project_id': 1, 'shared': True}]
    if reverse:
        rows.reverse()
    result = effective_models(rows, 7)
    assert {name: model['project_id'] for name, model in result.items()} == {'A': 7, 'B': 7, 'C': 1}


def test_unmeasured_model_is_discovered_without_receiving_a_quality_label():
    args = fixture()
    args['models'].append({'name': 'new-model', 'project_id': 7, 'context_window': 128000,
                           'max_output_tokens': 16000})
    result = resolve(request(), complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
    assert result['trace']['inventory']['discovered'] == 6
    assert result['trace']['inventory']['qualified_variants'] == 8
    assert result['trace']['inventory']['excluded'] == [
        {'model': 'new-model', 'model_project_id': 7, 'reason': 'NO_CALIBRATED_MODEL_CONTRACT'}]


def test_removing_discovered_model_removes_all_its_variants_without_catalog_edit():
    args = fixture()
    args['models'] = [m for m in args['models'] if m['name'] != 'gpt-5.4-mini']
    result = resolve(request(), complete=lambda *a, **k: pytest.fail('Greeting classified'), **args)
    assert result['trace']['inventory']['discovered'] == 4
    assert result['trace']['inventory']['qualified_variants'] == 6


@pytest.mark.parametrize('change', ['owner', 'fingerprint', 'unavailable', 'limit'])
def test_continuation_cannot_silently_replace_its_configuration(change):
    args = fixture()
    result = resolve(request(), complete=classifier_design, **args)
    req = request(); req['prior_pin'] = result['pin']
    model = next(m for m in args['models'] if m['name'] == result['config']['model_name'])
    if change == 'owner':
        model.update(project_id=1, shared=True)
    elif change == 'fingerprint':
        model['configuration_fingerprint'] = 'b' * 64
    elif change == 'unavailable':
        model['available'] = False
    else:
        model['context_window'] -= 1
    with pytest.raises(RoutingUnavailable, match='configuration changed'):
        resolve(req, complete=lambda *a, **k: pytest.fail('Continuation classified'), **args)


@pytest.mark.parametrize('reverse', [False, True])
def test_actual_selection_uses_project_limits_in_both_orders(reverse):
    args = fixture()
    name = 'gpt-5.4'
    private = next(m for m in args['models'] if m['name'] == name)
    private['context_window'] = 64000
    args['models'].append({**private, 'project_id': 1, 'shared': True, 'context_window': 512000})
    if reverse:
        args['models'].reverse()
    result = resolve(request('Design recovery durability.'), complete=classifier_design, **args)
    assert result['config']['model_name'] == name
    assert result['config']['model_project_id'] == 7
    assert result['config']['context_window'] == 64000


def test_bound_dispatch_never_falls_back_to_shared_or_raw():
    lookup = Mock(return_value=None)
    with pytest.raises(ValueError, match='deployment is unavailable'):
        map_bound_model({'name': 'model', 'project_id': 7}, project_id=7,
                        public_project_id=1, lookup=lookup)
    lookup.assert_called_once_with('model_group_info', '7_model')


def test_bound_shared_dispatch_keeps_owner_distinct_from_billing_project():
    lookup = Mock(return_value={'model': '1_model'})
    assert map_bound_model({'name': 'model', 'project_id': 1}, project_id=7,
                           public_project_id=1, lookup=lookup) == ('1_model', True)
    lookup.assert_called_once_with('model_group_info', '1_model')


def test_shared_pin_rejected_when_project_override_appears():
    shared = {'name': 'model', 'project_id': 1, 'shared': True}
    binding = model_binding(shared)
    private = {'name': 'model', 'project_id': 7}
    with pytest.raises(ValueError, match='configuration changed'):
        validate_binding(binding, [shared, private], 7)
    assert validate_binding(binding, [shared], 7) == shared
