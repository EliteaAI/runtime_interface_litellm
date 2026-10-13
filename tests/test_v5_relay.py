"""#6722 V5 relay/preflight: policy revision on pins, shadow event emission."""
import importlib
import json
import sys
import types

import pytest

from test_auto_inventory_dispatch import relay, signed_request, preflight_method  # noqa: F401  (fixture)
from test_auto_routing_service import request

POLICY = {'version': 1, 'shadow_mode': True, 'selector': {'artifact': 'selector-v5-stub', 'tau': 0.85},
          'fallback': {'name': 'gpt-5.4', 'project_id': 7}}


def headers(relay, result):
    return relay.proxy.Headers({'X-Elitea-Routing-Pin': result['pin'], 'X-Elitea-Routing-Invocation': 'a'*64})


def test_relay_accepts_a_pin_under_the_same_policy_and_rejects_a_changed_one(relay):
    relay.args['settings']['policy'] = dict(POLICY)
    target, auth, _ = signed_request(relay)
    pin = target['headers']['X-Elitea-Routing-Pin']
    assert relay.method.prepare_request(target, auth) is None
    assert relay.seen == [(7, 42)]  # one inventory read serves the vendor and binding checks
    for settings_change in ({'policy': {**POLICY, 'selector': {'artifact': 'selector-v5-stub', 'tau': 0.9}}},
                            {'policy': None}):
        relay.args['settings'].update(settings_change)
        target, auth, _ = signed_request(relay)  # a fresh pin under the changed settings ...
        target['headers'] = headers(relay, {'pin': pin})  # ... but relay the original one
        assert relay.method.prepare_request(target, auth)[1] == 409


@pytest.mark.parametrize('budgets,expected', [
    (None, 409),  # default 2048 = "low"; the pin (provider default, no effort) refuses it
    ({'low': 1000, 'medium': 1500, 'high': 3000}, None)])  # 2048 names no effort under this policy
def test_relay_thinking_budget_map_follows_the_policy(relay, budgets, expected):
    service = importlib.import_module('plugin_under_test.routing.service')
    haiku = 'eu.anthropic.claude-haiku-4-5-20251001-v1:0'
    relay.args['settings']['policy'] = {**POLICY, 'pool': [haiku],
                                        **({'thinking_budget_tokens': budgets} if budgets else {})}
    result = service.resolve(request(), complete=lambda *a, **k: pytest.fail('classified'), **relay.args)
    config = result['config']
    assert config['model_name'] == haiku and config['reasoning_effort'] is None
    target = {'endpoint': '/v1/chat/completions', 'headers': headers(relay, result), 'data': None,
              'json': {'model': haiku, 'max_tokens': config['max_tokens'],
                       'thinking': {'type': 'enabled', 'budget_tokens': 2048}}}
    response = relay.method.prepare_request(target, {'type': 'token', 'user': {'id': 42, 'name': 'user'}})
    assert (response if response is None else response[1]) == expected


def test_preflight_emits_shadow_event_without_prompt_text(relay, monkeypatch):
    spans = []
    class Span:
        def __init__(self, name, attributes):
            spans.append((name, attributes))
        def end(self):
            pass
    otel = types.ModuleType('opentelemetry')
    otel.trace = types.SimpleNamespace(get_tracer=lambda name: types.SimpleNamespace(
        start_span=lambda name, attributes: Span(name, attributes)))
    monkeypatch.setitem(sys.modules, 'opentelemetry', otel)
    relay.args['settings']['policy'] = dict(POLICY)
    result = preflight_method(relay, monkeypatch)()
    assert result['action'] == 'generate' and result['trace']['v5']['reason'] == 'GREETING_RULE'
    [(name, attributes)] = spans
    assert name == 'auto_routing_shadow_decision' and attributes['project.id'] == 7
    assert attributes['auto.noise_band'] == '0.0' and 'auto.p_spread' in attributes
    record = json.loads(attributes['auto.record'])
    assert record['legacy_choice']['variant'] == result['trace']['selection']['variant']
    assert record['executed'] == result['config']['model_name']
    assert record['shadow'] is True and record['selector_id'] == 'selector-v5-stub'
    assert 'Hi' not in attributes['auto.record'].replace('Hit', '')
    assert set(record['candidates'][0]) <= {'variant', 'deployment', 'canonical', 'effort', 'p_pass',
                                            'expected_cost_usd', 'ineligible', 'gates'}


def test_preflight_without_policy_emits_nothing(relay, monkeypatch):
    spans = []
    otel = types.ModuleType('opentelemetry')
    otel.trace = types.SimpleNamespace(get_tracer=lambda name: spans.append(name))
    monkeypatch.setitem(sys.modules, 'opentelemetry', otel)
    assert 'v5' not in preflight_method(relay, monkeypatch)()['trace']
    assert spans == []


def test_tracing_failure_never_breaks_preflight(relay, monkeypatch):
    otel = types.ModuleType('opentelemetry')
    otel.trace = types.SimpleNamespace(get_tracer=lambda name: 1/0)
    monkeypatch.setitem(sys.modules, 'opentelemetry', otel)
    relay.args['settings']['policy'] = dict(POLICY)
    assert preflight_method(relay, monkeypatch)()['action'] == 'generate'
