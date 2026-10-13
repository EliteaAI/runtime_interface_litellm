"""Classifier contradictions are visible without rewriting labels or floors."""
import itertools
import json

from routing.service import resolve
from routing.v7.task_profile import apply_profile, ENUMS
from test_auto_routing_service import fixture, request
from test_v12_algorithm import DESC, PROFILE


def test_every_profile_retains_existing_floor_and_assertions():
    order = {'simple': 0, 'standard': 1, 'deep': 2}
    for values in itertools.product(*ENUMS.values()):
        profile = dict(zip(ENUMS, values))
        for demand in order:
            original = {**DESC, 'demand': demand}
            floor = ('deep' if profile['reasoning'] == 'interacting_constraints' else
                     'standard' if profile['reasoning'] == 'multi_step' or profile['evidence'] == 'conflicting'
                     else 'simple')
            result = apply_profile(original, profile)
            assert result['demand'] == max(demand, floor, key=order.get)
            assert result['work_profile'] == profile
            assert result['profile_demand_adjustment']['input_demand'] == demand
            assert not result['profile_consistency']['labels_corrected']
            assert original['demand'] == demand


def test_inconsistent_routine_workflow_remains_visible_through_real_resolver():
    profile = {**PROFILE, 'work': 'design', 'reasoning': 'interacting_constraints',
               'evidence': 'ordinary', 'verification': 'none'}
    answer = {**DESC, 'operation': 'design', 'demand': 'standard', 'work_profile': profile}
    result = resolve(request('Specify roles and steps for an approval workflow.'),
        complete=lambda *a, **k: {'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'},
        **fixture())
    trace = result['trace']
    assert trace['difficulty']['raw_classifier_demand'] == 'standard'
    assert trace['difficulty']['final_demand'] == 'deep'
    assert trace['descriptor']['work_profile'] == profile
    assert trace['descriptor']['profile_consistency']['status'] == 'reasoning_exceeds_raw_demand'
    assert trace['descriptor']['profile_consistency']['reasoning_minimum'] == 'deep'


def test_conflict_only_floor_is_not_a_reasoning_contradiction():
    result = apply_profile({**DESC, 'demand': 'simple'},
                           {**PROFILE, 'reasoning': 'bounded', 'evidence': 'conflicting'})
    assert result['demand'] == 'standard'
    assert result['profile_consistency']['status'] == 'consistent'


def test_high_raw_demand_is_never_downgraded_and_legacy_remains_unchanged():
    original = {**DESC, 'demand': 'deep'}
    assert apply_profile(original, None) is original
    result = apply_profile(original, {**PROFILE, 'reasoning': 'bounded'})
    assert result['demand'] == 'deep'
    assert result['profile_consistency']['status'] == 'consistent'
