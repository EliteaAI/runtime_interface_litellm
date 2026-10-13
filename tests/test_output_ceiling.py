"""#6722: no invented output ceiling. Absent cap = provider default (None); an explicit cap is authoritative."""
import base64
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admission_support import args_for, classifier, descriptor, request
from routing.effort import check_output_limit
from routing.service import CONFIGURED_CLASSIFIER, GenerationRouter, RoutingUnavailable, decode_pin, encode_pin, resolve
from routing.v7.catalog import compile_catalog

KEYS = ('luna', 'terra')


def body(token):
    part = token.split('.')[0]
    return json.loads(base64.urlsafe_b64decode(part + '=' * ((-len(part)) % 4)))


def go(cap=None, **kwargs):
    args = args_for(*KEYS, **kwargs.pop('inventory', {}))
    req = request(**kwargs.pop('request', {}))
    if cap is not None:
        req['output_cap'] = cap
    return req, args, resolve(req, complete=classifier(demand='standard'), **args)


# --- Gateway preflight ---------------------------------------------------------------------------------------------------
def test_absent_cap_pins_the_provider_default():
    _, _, result = go()
    config = result['config']
    assert config['max_tokens'] is None and config['routing_total_output_cap'] is False
    assert config['max_output_tokens'] == 64000
    assert 'routing_min_output_cap' not in config
    assert result['trace']['budget']['completion_cap'] is None
    assert body(result['pin'])['config'] == config


def test_there_is_no_8000_default_and_no_32000_upper_bound():
    assert go(request={})[2]['config']['max_tokens'] is None
    _, _, big = go(cap=48000)  # above the old 32000 bound, within the model maximum
    assert big['config']['max_tokens'] == 48000 and big['config']['routing_total_output_cap'] is True


@pytest.mark.parametrize('cap', [255, 0, -5, 'many', 1.5, True])
def test_explicit_cap_must_be_an_integer_of_at_least_256(cap):
    req = request(); req['output_cap'] = cap
    with pytest.raises(RoutingUnavailable):
        resolve(req, complete=classifier(demand='standard'), **args_for(*KEYS))


def test_explicit_cap_is_bounded_above_by_each_models_maximum():
    with pytest.raises(RoutingUnavailable):
        go(cap=64001)
    # A model with a larger maximum is still admitted for that cap.
    _, _, result = go(cap=64001, inventory={'luna': {'max_output_tokens': 100000}, 'terra': {'max_output_tokens': 100000}})
    assert result['config']['max_tokens'] == 64001


def test_null_cap_in_the_request_is_the_same_as_absent():
    req = request(); req['output_cap'] = None
    result = resolve(req, complete=classifier(demand='standard'), **args_for(*KEYS))
    assert result['config']['max_tokens'] is None


def test_measured_contract_minimum_exists_only_when_a_cap_exists():
    args = args_for('terra'); req = request(); req['output_cap'] = 8000
    explicit = resolve(req, complete=classifier(demand='deep'), **args)
    assert explicit['config']['routing_min_output_cap'] == 8000 and explicit['config']['max_tokens'] == 8000
    absent = resolve(request(), complete=classifier(demand='deep'), **args_for('terra'))
    assert 'routing_min_output_cap' not in absent['config']


# --- relay envelope ------------------------------------------------------------------------------------------------------
PROVIDER_DEFAULT = {'max_tokens': None, 'max_output_tokens': 64000}
EXPLICIT = {'max_tokens': 8000, 'max_output_tokens': 64000, 'routing_min_output_cap': 8000}


@pytest.mark.parametrize('request_body', [{}, {'max_tokens': 1}, {'max_tokens': 64000}, {'max_completion_tokens': 5000},
                                          {'max_output_tokens': 64000}, {'max_tokens': None}])
def test_provider_default_pin_accepts_a_missing_limit_or_any_limit_up_to_the_model_maximum(request_body):
    check_output_limit(PROVIDER_DEFAULT, request_body)


@pytest.mark.parametrize('request_body', [{'max_tokens': 64001}, {'max_completion_tokens': 10 ** 9},
                                          {'max_output_tokens': '64000'}, {'max_tokens': 1.5}, {'max_tokens': True}])
def test_provider_default_pin_refuses_a_limit_beyond_the_model_maximum_or_malformed(request_body):
    with pytest.raises(ValueError):
        check_output_limit(PROVIDER_DEFAULT, request_body)


def test_minimum_applies_only_when_a_limit_is_present():
    check_output_limit({**PROVIDER_DEFAULT, 'routing_min_output_cap': 8000}, {})  # no limit, no minimum check
    with pytest.raises(ValueError, match='reduced'):
        check_output_limit({**PROVIDER_DEFAULT, 'routing_min_output_cap': 8000}, {'max_tokens': 4000})


@pytest.mark.parametrize('request_body,ok', [({'max_tokens': 8000}, True), ({'max_tokens': 8001}, False),
                                             ({'max_tokens': 7999}, False), ({}, False)])
def test_explicit_cap_pin_is_exact(request_body, ok):
    if ok:
        check_output_limit(EXPLICIT, request_body)
    else:
        with pytest.raises(ValueError):
            check_output_limit(EXPLICIT, request_body)


# --- renewal ---------------------------------------------------------------------------------------------------------------
def renew(first, args, **changes):
    req = request(**changes)
    req['prior_pin'] = first['pin']; req['state_token'] = first['state_token']
    req['messages'].append({'role': 'tool', 'content': 'Completed step', 'tool_call_id': 't1'})
    return req, lambda *a, **k: pytest.fail('Same run reclassified')


def test_none_stays_none_on_renewal():
    req, args, first = go()
    req, complete = renew(first, args)
    renewed = resolve(req, complete=complete, **args)
    assert renewed['config'] == first['config'] and renewed['config']['max_tokens'] is None
    assert body(renewed['pin'])['config']['max_tokens'] is None


def test_renewal_can_narrow_a_provider_default_but_never_enlarge_an_explicit_cap():
    req, args, first = go()
    req, complete = renew(first, args)
    req['output_cap'] = 4000
    narrowed = resolve(req, complete=complete, **args)
    assert narrowed['config']['max_tokens'] == 4000 and narrowed['config']['routing_total_output_cap'] is True
    again = request(); again['prior_pin'] = narrowed['pin']; again['state_token'] = narrowed['state_token']
    again['messages'].append({'role': 'tool', 'content': 'Completed step', 'tool_call_id': 't1'})
    again['output_cap'] = 4001
    with pytest.raises(RoutingUnavailable, match='cannot be enlarged'):
        resolve(again, complete=complete, **args)


def test_an_omitted_cap_renews_the_original_explicit_cap():
    req, args, first = go(cap=6000)
    req, complete = renew(first, args)
    renewed = resolve(req, complete=complete, **args)
    assert renewed['config']['max_tokens'] == 6000 and renewed['config'] == first['config']


def test_renewed_minimum_follows_the_narrowed_cap():
    args = args_for('terra'); req = request(); req['output_cap'] = 9000
    first = resolve(req, complete=classifier(demand='deep'), **args)
    nxt = request(); nxt['prior_pin'] = first['pin']; nxt['state_token'] = first['state_token']
    nxt['messages'].append({'role': 'tool', 'content': 'Completed step', 'tool_call_id': 't1'})
    nxt['output_cap'] = 7000
    narrowed = resolve(nxt, complete=lambda *a, **k: pytest.fail('Reclassified'), **args)
    assert narrowed['config']['max_tokens'] == 7000 and narrowed['config']['routing_min_output_cap'] == 7000


# --- Router (child scopes) ---------------------------------------------------------------------------------------------------
class Scope:
    id = 's'
    lock = threading.Lock()
    classifications = 0

    def __init__(self, cap):
        self.decision = {'selection': {'variant': 'luna-default', 'reason': 'ADMISSION_TABLE_COST_BAND'},
                         'descriptor': descriptor(), 'budget': {'completion_cap': cap}}


@pytest.fixture
def router():
    return GenerationRouter(object(), CONFIGURED_CLASSIFIER, catalog=compile_catalog())


def test_router_accepts_an_absent_cap_and_rejects_malformed_ones(router):
    messages = [{'role': 'user', 'content': 'x'}]
    assert router.resolve(messages, binding={'mode': 'fixed', 'variant': 'luna-default'}, output_cap=None)
    for bad in (100, 'x', 1.0):
        with pytest.raises(ValueError, match='at least 256'):
            router.resolve(messages, binding={'mode': 'fixed', 'variant': 'luna-default'}, output_cap=bad)
    assert router.resolve(messages, binding={'mode': 'fixed', 'variant': 'luna-default'}, output_cap=500_000)


def test_child_scope_cannot_widen_a_bounded_root_pin(router):
    messages = [{'role': 'user', 'content': 'x'}]
    router.resolve(messages, scope=Scope(8000), output_cap=8000)
    router.resolve(messages, scope=Scope(8000), output_cap=4000)
    with pytest.raises(ValueError, match='root pin'):
        router.resolve(messages, scope=Scope(8000), output_cap=9000)
    with pytest.raises(ValueError, match='root pin'):
        router.resolve(messages, scope=Scope(8000), output_cap=None)  # provider default is wider than 8000


def test_child_scope_under_a_provider_default_root_pin_may_set_a_cap(router):
    messages = [{'role': 'user', 'content': 'x'}]
    router.resolve(messages, scope=Scope(None), output_cap=None)
    router.resolve(messages, scope=Scope(None), output_cap=4000)
