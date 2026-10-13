"""#6722 V5: policy values, shared hard gates, V5 decision, shadow/active switch.

No network, no model calls. The legacy suites prove the default-policy path is
unchanged; this file proves the new behaviour and the parity of the gates.
"""
import copy
import itertools
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing import selector as sel
from routing import v5
from routing.gates import admit, evaluate, is_anthropic
from routing.policy import DEFAULT_POLICY, effective_policy, runtime_policy, validate, PolicyError
from routing.pricing import PriceBook, Tokens
from routing.service import (resolve, decode_pin, RoutingUnavailable, compiled_router, routing_policy_revision,
                             pinned_is_anthropic)
from routing.inventory import effective_models, qualified_inventory
from test_auto_routing_service import fixture, request
from test_v5_selector import artifact, install

PROVE = 'Please prove the crash recovery architecture is correct'
SUMMARY = 'Summarise this paragraph about apples in two sentences.'


def policy(**changes):
    value = {'version': 1, 'shadow_mode': True, 'selector': {'artifact': 'selector-v5-stub', 'tau': 0.85},
             'fallback': {'name': 'gpt-5.4', 'project_id': 7}}
    value.update(changes)
    return value


@pytest.fixture
def trained(tmp_path, monkeypatch):
    """The stub's heads installed as a non-stub artifact, so active mode may execute it."""
    value = artifact(); value['id'] = 'selector-v5-t1'
    install(tmp_path, value)
    monkeypatch.setattr(sel, 'ARTIFACT_DIR', tmp_path)
    return 'selector-v5-t1'


def no_classifier(*args, **kwargs):
    raise AssertionError('Active V5 must not call the LLM classifier')


def classify_design(*args, **kwargs):
    from test_auto_routing_service import classifier_design
    return classifier_design()


# ---------------------------------------------------------------- policy

def test_absent_policy_is_todays_literals_and_no_v5_mode():
    assert effective_policy({'enabled': True}) == DEFAULT_POLICY
    assert runtime_policy({'enabled': True, 'policy': None})[1:] == (None, None)
    assert DEFAULT_POLICY['output'] == {'default_allowance': 8000, 'min_cap': 256, 'max_cap': 32000}
    assert DEFAULT_POLICY['thinking_budget_tokens'] == {'low': 2048, 'medium': 4096, 'high': 9092}


def test_partial_policy_is_merged_per_key_with_defaults():
    value = validate({'selector': {'tau': 0.9}, 'output': {'max_cap': 64000}})
    assert value['selector'] == {'artifact': None, 'tau': 0.9}
    assert value['output'] == {'default_allowance': 8000, 'min_cap': 256, 'max_cap': 64000}
    assert value['greeting_rule'] is True and value['shadow_mode'] is True


@pytest.mark.parametrize('raw', [
    {'unknown': 1}, {'version': 2}, {'version': True}, {'shadow_mode': 'no'}, {'greeting_rule': 1},
    {'selector': {'tau': 0.49}}, {'selector': {'tau': 0.991}}, {'selector': {'tau': True}},
    {'selector': {'artifact': '../x'}}, {'selector': {'extra': 1}},
    {'pool': []}, {'pool': 'gpt-5.4'}, {'pool': [1]}, {'pool': ['x']*257},
    {'fallback': {'name': 'x'}}, {'fallback': {'name': '', 'project_id': 1}},
    {'fallback': {'name': 'x', 'project_id': 0}}, {'fallback': {'name': 'x', 'project_id': 1, 'effort': 'low'}},
    {'output': {'min_cap': 255}}, {'output': {'max_cap': 128001}}, {'output': {'default_allowance': 255}},
    {'output': {'min_cap': 4000, 'default_allowance': 3000}}, {'output': {'max_cap': 7999}},
    {'output': {'default_allowance': 8000.0}},
    {'thinking_budget_tokens': {'low': -1}}, {'thinking_budget_tokens': {'low': 5000}},
    {'thinking_budget_tokens': {'max': 1}}, {'classifier': {'enabled': 'false'}}, [],
])
def test_invalid_policy_is_refused_with_policy_invalid(raw):
    with pytest.raises(PolicyError):
        validate(raw)
    with pytest.raises(RoutingUnavailable) as exc:
        effective_policy({'policy': raw})
    assert exc.value.reason == 'POLICY_INVALID'


@pytest.mark.parametrize('raw', [
    {'selector': {'tau': 0.5}}, {'selector': {'tau': 0.99}}, {'output': {'min_cap': 256, 'max_cap': 128000,
     'default_allowance': 128000}}, {'thinking_budget_tokens': {'low': 0, 'medium': 1, 'high': 2}},
    {'pool': ['openai/gpt-5-4', 'global.openai.gpt-5.6-luna']}, {'fallback': None}])
def test_policy_bounds_are_inclusive(raw):
    validate(raw)


def test_invalid_policy_runs_legacy_with_literals_and_traces_policy_invalid():
    args = fixture()
    plain = resolve(request(), complete=no_classifier, **args)
    args['settings']['policy'] = {'selector': {'tau': 2}}
    result = resolve(request(), complete=no_classifier, **args)
    assert result['config'] == plain['config']
    assert result['trace']['v5']['reason'] == 'POLICY_INVALID'
    assert result['trace']['v5']['legacy_choice']['variant'] == plain['trace']['selection']['variant']


# ---------------------------------------------------------------- gates

def legacy_admit(runtime_variants, catalog, *, cap, size, output_schema, explicit, prices):
    """Frozen copy of the pre-#6722-V5 inline loop in service.resolve."""
    allowed, output_caps, contract_exclusions = [], {}, {}
    for vid, model in runtime_variants.items():
        variant = catalog['variants'][vid]
        contract = variant.get('calibration_contract')
        variant_cap = cap if cap is not None else (contract['output_allowance'] if contract else 8000)
        if contract:
            native = not model.get('openai_compatible', False) and is_anthropic(model, variant)
            transport = 'anthropic_messages' if native else 'chat_completions'
            reasons = []
            if transport != contract['transport']:
                reasons.append('CALIBRATION_TRANSPORT_UNMEASURED')
            if variant_cap != contract['output_allowance']:
                reasons.append('CALIBRATION_OUTPUT_ALLOWANCE_UNMEASURED')
            if output_schema is not None:
                reasons.append('CALIBRATION_STRUCTURED_OUTPUT_UNMEASURED')
            if explicit['mode'] == 'explicit' and 'reasoning_fields' not in contract:
                reasons.append('CALIBRATION_EXPLICIT_EFFORT_UNMEASURED')
            if variant['effort'] is not None and model.get('supports_reasoning') is False:
                reasons.append('CONFIGURATION_REASONING_DISABLED')
            if reasons:
                contract_exclusions[vid] = {'reasons': reasons, 'configured_transport': transport,
                                            'measured_transport': contract['transport']}
                continue
        if explicit['mode'] == 'explicit' and variant['effort'] != explicit.get('preset'):
            continue
        if (is_anthropic(model, variant) and variant['effort'] and not model.get('openai_compatible')
                and not (contract and 'reasoning_fields' in contract)):
            if variant_cap <= {'low': 2048, 'medium': 4096, 'high': 9092}[variant['effort']]:
                continue
        if type(model.get('context_window')) is not int or size+variant_cap > model['context_window']:
            continue
        if type(model.get('max_output_tokens')) is not int or variant_cap > model['max_output_tokens']:
            continue
        if prices.quote(model['name'], Tokens(size, variant_cap))['usd'] is None:
            continue
        if variant.get('cache_write_mode') != 'ordinary_input' and prices.quote(model['name'], Tokens(0, variant_cap, write=size))['usd'] is None:
            continue
        allowed.append(vid)
        output_caps[vid] = variant_cap
    return allowed, output_caps, contract_exclusions


def every_catalog_deployment():
    """All 28 compiled variants deployed, with deliberately mixed configuration facts."""
    catalog = compiled_router().catalog
    names = sorted({v['model'] for v in catalog['variants'].values()})
    models = []
    for i, name in enumerate(names):
        models.append({'name': name, 'project_id': 7, 'context_window': [128000, 40000, 200000][i % 3],
                       'max_output_tokens': [16000, 32000, 8000][i % 3], 'supports_reasoning': [True, False, None][i % 3],
                       'openai_compatible': i % 4 == 1})
    entries = [dict(model_name=n, input_cost_per_token='.000001', output_cost_per_token='.000002',
                    **({} if i % 5 == 2 else {'cache_creation_input_token_cost': '.00000125'}))
               for i, n in enumerate(names) if i % 7 != 3]
    return catalog, effective_models(models, 7), PriceBook(entries, source_revision='p')


@pytest.mark.parametrize('cap,size,schema,explicit', list(itertools.product(
    [None, 256, 8000, 16000, 32000], [10, 30000, 120000], [None, {'type': 'object'}],
    [{'mode': 'auto'}, {'mode': 'explicit', 'preset': None}, {'mode': 'explicit', 'preset': 'high'},
     {'mode': 'explicit', 'preset': 'low'}])))
def test_gates_with_default_policy_equal_the_pre_policy_loop(cap, size, schema, explicit):
    catalog, visible, prices = every_catalog_deployment()
    runtime, _ = qualified_inventory(visible, catalog, project_id=7)
    kwargs = dict(cap=cap, size=size, output_schema=schema, explicit=explicit, prices=prices)
    allowed, caps, contract, report = admit(runtime, catalog, policy=DEFAULT_POLICY, **kwargs)
    assert (allowed, caps, contract) == legacy_admit(runtime, catalog, **kwargs)
    assert set(report) == set(runtime) and all((not r['failed']) == (v in allowed) for v, r in report.items())


def test_thinking_budget_gate_uses_policy_budgets():
    prices = PriceBook([dict(model_name='claude-x', input_cost_per_token='1', output_cost_per_token='1',
                             cache_creation_input_token_cost='1')], source_revision='p')
    variant = {'model': 'claude-x', 'effort': 'high', 'cache_write_mode': 'separate'}
    model = {'name': 'claude-x', 'project_id': 7, 'context_window': 100000, 'max_output_tokens': 20000}
    common = dict(cap=9000, size=10, output_schema=None, explicit={'mode': 'auto'}, prices=prices)
    assert evaluate('v', variant, model, policy=DEFAULT_POLICY, **common)['failed'] == ['THINKING_BUDGET']
    lowered = validate({'thinking_budget_tokens': {'low': 1000, 'medium': 2000, 'high': 4000}})
    assert evaluate('v', variant, model, policy=lowered, **common)['failed'] == []


def test_gate_report_lists_every_failure():
    prices = PriceBook([], source_revision='p')
    variant = {'model': 'm', 'effort': None, 'canonical_model': 'openai/m'}
    model = {'name': 'm', 'project_id': 7, 'available': False, 'identity': {'canonical': 'openai/m', 'kind': 'image'}}
    result = evaluate('v', variant, model, cap=None, size=1, output_schema=None, explicit={'mode': 'auto'},
                      prices=prices, policy=validate({'pool': ['other']}))
    assert result['failed'] == ['AVAILABLE', 'CHAT', 'POOL', 'CONTEXT_WINDOW', 'OUTPUT_CAP', 'PRICE']


def test_pool_admits_canonical_ids_or_exact_names_in_both_paths():
    args = fixture()
    args['settings']['policy'] = policy(pool=['openai/gpt-5-6-luna'])
    result = resolve(request(), complete=no_classifier, **args)
    assert result['config']['model_name'] == 'global.openai.gpt-5.6-luna'
    assert result['trace']['inventory']['admitted_variants'] == 1
    args['settings']['policy'] = policy(pool=['gpt-5.4-mini'])
    assert resolve(request(), complete=no_classifier, **args)['config']['model_name'] == 'gpt-5.4-mini'
    args['settings']['policy'] = policy(pool=['openai/not-deployed'])
    with pytest.raises(RoutingUnavailable) as exc:
        resolve(request(), complete=no_classifier, **args)
    assert exc.value.reason == 'NO_QUALIFIED_MODELS'


def test_output_values_come_from_policy():
    args = fixture()
    args['settings']['policy'] = policy(output={'default_allowance': 6000, 'min_cap': 512, 'max_cap': 12000})
    assert resolve(request(), complete=no_classifier, **args)['config']['max_tokens'] == 6000
    for cap in (511, 12001):
        req = request(); req['output_cap'] = cap
        with pytest.raises(RoutingUnavailable, match='between 512 and 12000'):
            resolve(req, complete=no_classifier, **args)


# ---------------------------------------------------------------- V5 decision (pure)

def decide(prompt, *, tau=0.85, fallback=None, previous=None, loader=None, greeting_rule=True, args=None, mode='shadow'):
    args = args or fixture()
    catalog = compiled_router().catalog
    visible = effective_models(args['models'], 7)
    runtime, _ = qualified_inventory(visible, catalog, project_id=7)
    prices = PriceBook(args['price_snapshot']['entries'], source_revision='p')
    pol = validate(policy(selector={'artifact': 'selector-v5-stub', 'tau': tau}, fallback=fallback,
                          greeting_rule=greeting_rule))
    allowed, caps, _, report = admit(runtime, catalog, cap=None, size=500, output_schema=None,
                                     explicit={'mode': 'auto'}, prices=prices, policy=pol)
    return v5.decide(policy=pol, mode=mode, report=report, allowed=allowed, runtime_variants=runtime,
                     catalog=catalog, output_caps=caps, prices=prices,
                     messages=[{'role': 'user', 'content': prompt}], tools=[], output_schema=None, size=500,
                     previous=previous, loader=loader or sel.installed)


@pytest.mark.parametrize('tau,prompt,expected,reason', [
    (0.80, PROVE, 'luna-default', 'SELECTED'),
    (0.85, PROVE, 'gpt54-medium', 'SELECTED'),
    (0.85, SUMMARY, 'luna-default', 'SELECTED'),
    (0.95, SUMMARY, 'gpt54-medium', 'SELECTED'),
    (0.97, SUMMARY, 'gpt54-low', 'FALLBACK_NO_QUALIFIED'),
])
def test_tau_sweep_picks_cheapest_qualified_else_fallback(tau, prompt, expected, reason):
    trace = decide(prompt, tau=tau, fallback={'name': 'gpt-5.4', 'project_id': 7})
    assert (trace['chosen']['variant'], trace['reason']) == (expected, reason)
    rows = {c['variant']: c for c in trace['candidates']}
    assert rows['haiku-default']['ineligible'] == 'NO_SELECTOR_HEAD'
    assert 'p_pass' not in rows['haiku-default']
    assert rows['luna-default']['canonical'] == 'openai/gpt-5-6-luna'
    if reason == 'FALLBACK_NO_QUALIFIED':
        assert all(c.get('ineligible') in {'BELOW_TAU', 'NO_SELECTOR_HEAD'} for c in trace['candidates'])


def test_expected_cost_uses_head_output_tokens_and_request_size():
    trace = decide(SUMMARY)
    row = next(c for c in trace['candidates'] if c['variant'] == 'luna-default')
    assert row['expected_output_tokens'] == 367
    assert row['expected_cost_usd'] == str(PriceBook(fixture()['price_snapshot']['entries'], source_revision='p')
                                           .quote('global.openai.gpt-5.6-luna', Tokens(125, 367))['usd'])


def test_no_qualified_and_no_fallback_reports_no_qualified_models():
    trace = decide(SUMMARY, tau=0.99)
    assert trace['chosen'] is None and trace['reason'] == 'NO_QUALIFIED_MODELS'
    trace = decide(SUMMARY, tau=0.99, fallback={'name': 'gpt-5.4', 'project_id': 8})
    assert trace['chosen'] is None and trace['reason'] == 'NO_QUALIFIED_MODELS'


def test_greeting_rule_takes_cheapest_gated_deployment_without_scoring():
    args = fixture()
    for entry in args['price_snapshot']['entries']:
        if entry['model_name'] == 'eu.anthropic.claude-haiku-4-5-20251001-v1:0':
            entry.update(input_cost_per_token='.0000001', output_cost_per_token='.0000002')
    trace = decide('Hi', args=args, loader=lambda _: (_ for _ in ()).throw(ValueError()))
    assert trace['reason'] == 'GREETING_RULE' and trace['chosen']['variant'] == 'haiku-default'
    assert all('p_pass' not in c for c in trace['candidates'])
    assert decide('Hi', args=args, greeting_rule=False)['reason'] == 'SELECTED'


def test_incumbent_wins_only_an_exact_cost_tie():
    value = artifact()
    value['heads'] = {'openai/gpt-5-4|low': copy.deepcopy(value['heads']['openai/gpt-5-4|medium']),
                      'openai/gpt-5-4|medium': value['heads']['openai/gpt-5-4|medium']}
    value['sha256'] = sel.canonical_sha256(value)
    loaded = sel.Selector(value)
    # Equal heads and price: inventory order decides without an incumbent ...
    assert decide(SUMMARY, loader=lambda _: loaded)['chosen']['variant'] == 'gpt54-medium'
    # ... and the incumbent wins the exact tie.
    tied = decide(SUMMARY, loader=lambda _: loaded, previous='gpt54-low')
    assert tied['chosen'] == {'variant': 'gpt54-low', 'deployment': 'gpt-5.4', 'project_id': 7,
                              'effort': 'low', 'incumbent': True}
    # A cheaper qualified candidate beats an incumbent that is not tied.
    assert decide(SUMMARY, previous='gpt54-medium')['chosen']['variant'] == 'luna-default'


def test_selector_unavailable_uses_fallback_or_reports():
    missing = lambda _: (_ for _ in ()).throw(ValueError('missing'))
    trace = decide(SUMMARY, loader=missing, fallback={'name': 'gpt-5.4', 'project_id': 7})
    assert trace['reason'] == 'SELECTOR_UNAVAILABLE' and trace['chosen']['variant'] == 'gpt54-low'
    trace = decide(SUMMARY, loader=missing)
    assert trace['reason'] == 'SELECTOR_UNAVAILABLE' and trace['chosen'] is None
    # The untrained stub is observable in shadow mode but never executed.
    assert decide(SUMMARY, mode='active')['reason'] == 'SELECTOR_UNAVAILABLE'
    assert decide(SUMMARY, mode='shadow')['selector_stub'] is True


def test_trace_never_contains_request_text():
    trace = decide('Secret customer phrase 7c1f prove it')
    assert 'Secret' not in json.dumps(trace) and '7c1f' not in json.dumps(trace)


# ---------------------------------------------------------------- service: shadow / active

def test_no_policy_key_has_no_v5_trace_and_code_revision_pins():
    args = fixture()
    result = resolve(request(), complete=no_classifier, **args)
    assert 'v5' not in result['trace']
    pin = decode_pin(result['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=101)
    assert pin['policy_revision'] == compiled_router().revision


@pytest.mark.parametrize('prompt', ['Hi', PROVE])
def test_shadow_executes_legacy_and_traces_v5(prompt):
    args = fixture()
    complete = no_classifier if prompt == 'Hi' else classify_design
    legacy = resolve(request(prompt), complete=complete, **args)
    args['settings']['policy'] = policy()
    shadow = resolve(request(prompt), complete=complete, **args)
    assert shadow['config'] == legacy['config']
    assert shadow['trace']['selection'] == legacy['trace']['selection']
    trace = shadow['trace']['v5']
    assert trace['shadow'] is True and trace['selector_id'] == 'selector-v5-stub' and trace['tau'] == 0.85
    assert trace['legacy_choice']['variant'] == legacy['trace']['selection']['variant']
    assert trace['reason'] == ('GREETING_RULE' if prompt == 'Hi' else 'SELECTED')
    assert trace['policy_revision'] == routing_policy_revision(compiled_router(), args['settings'])
    pin = decode_pin(shadow['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=101)
    assert pin['policy_revision'] == trace['policy_revision'] != compiled_router().revision


def test_shadow_v5_failure_never_breaks_legacy(monkeypatch):
    args = fixture(); args['settings']['policy'] = policy()
    monkeypatch.setattr(v5, 'decide', lambda **k: 1/0)
    result = resolve(request(), complete=no_classifier, **args)
    assert result['trace']['v5']['reason'] == 'V5_ERROR' and result['config']['model_name']


def test_active_executes_v5_without_classifier_and_pins_policy_revision(trained):
    args = fixture()
    args['settings'].pop('classifier')  # V5 needs no LLM classifier.
    args['settings']['policy'] = policy(shadow_mode=False, selector={'artifact': trained, 'tau': 0.85})
    result = resolve(request(PROVE), complete=no_classifier, **args)
    assert result['config']['model_name'] == 'gpt-5.4' and result['config']['reasoning_effort'] == 'medium'
    assert result['config']['max_tokens'] == 8000
    assert result['trace']['selection']['variant'] == 'gpt54-medium'
    assert result['trace']['selection']['reason'] == 'V5_SELECTED'
    assert result['trace']['v5']['shadow'] is False and result['trace']['v5']['legacy_choice'] is None
    assert result['trace']['classifier'] == {'called': False, 'classifier_variant': None}
    pin = decode_pin(result['pin'], args['signing_key'], project_id=7, user_id=2, settings=args['settings'], now=101)
    assert pin['config'] == result['config']
    assert pin['policy_revision'] == routing_policy_revision(compiled_router(), args['settings'])
    summary = resolve(request(SUMMARY), complete=no_classifier, **args)
    assert summary['config']['model_name'] == 'global.openai.gpt-5.6-luna'


def test_active_state_token_carries_the_v5_incumbent(trained):
    args = fixture(); args['settings'].pop('classifier')
    args['settings']['policy'] = policy(shadow_mode=False, selector={'artifact': trained, 'tau': 0.85})
    first = resolve(request(PROVE), complete=no_classifier, **args)
    second = request(PROVE); second['invocation_id'] = 'b'*64; second['state_token'] = first['state_token']
    second['messages'] = [{'role': 'user', 'content': PROVE}, {'role': 'assistant', 'content': 'Done.'},
                          {'role': 'user', 'content': PROVE}]
    again = resolve(second, complete=no_classifier, **args)
    assert again['config'] == first['config']


def test_active_with_stub_or_missing_selector_uses_fallback_else_refuses(trained):
    args = fixture(); args['settings'].pop('classifier')
    args['settings']['policy'] = policy(shadow_mode=False, selector={'artifact': 'selector-v5-absent', 'tau': 0.85})
    result = resolve(request(SUMMARY), complete=no_classifier, **args)
    assert result['trace']['v5']['reason'] == 'SELECTOR_UNAVAILABLE'
    assert result['config']['model_name'] == 'gpt-5.4'
    args['settings']['policy']['fallback'] = None
    with pytest.raises(RoutingUnavailable) as exc:
        resolve(request(SUMMARY), complete=no_classifier, **args)
    assert exc.value.reason == 'SELECTOR_UNAVAILABLE'
    args['settings']['policy'] = policy(shadow_mode=False, fallback=None,
                                        selector={'artifact': trained, 'tau': 0.99})
    with pytest.raises(RoutingUnavailable) as exc:
        resolve(request(SUMMARY), complete=no_classifier, **args)
    assert exc.value.reason == 'NO_QUALIFIED_MODELS'


def test_active_greeting_rule_still_applies(trained):
    args = fixture(); args['settings'].pop('classifier')
    args['settings']['policy'] = policy(shadow_mode=False, selector={'artifact': trained, 'tau': 0.85})
    result = resolve(request('Hi'), complete=no_classifier, **args)
    assert result['trace']['v5']['reason'] == 'GREETING_RULE'


@pytest.mark.parametrize('change', [
    lambda p: p['selector'].update(tau=0.9), lambda p: p.update(shadow_mode=True),
    lambda p: p.update(fallback=None), lambda p: p['output'].update(default_allowance=7000),
    lambda p: p.pop('selector') and None])
def test_renewal_rejects_a_changed_policy(trained, change):
    args = fixture(); args['settings'].pop('classifier')
    args['settings']['policy'] = policy(shadow_mode=False, selector={'artifact': trained, 'tau': 0.85},
                                        output={'default_allowance': 8000})
    first = resolve(request(PROVE), complete=no_classifier, **args)
    req = request(PROVE); req['prior_pin'] = first['pin']
    renewed = resolve(req, complete=no_classifier, **args)
    assert renewed['trace'] == {'reason': 'REAUTHORIZED_CHECKPOINT_PIN'}
    changed = copy.deepcopy(args['settings']['policy']); change(changed)
    args['settings']['policy'] = changed
    args['settings']['classifier'] = fixture()['settings']['classifier']
    with pytest.raises(RoutingUnavailable, match='policy changed'):
        resolve(req, complete=no_classifier, **args)


def test_removing_the_policy_invalidates_policy_pins():
    args = fixture(); args['settings']['policy'] = policy()
    first = resolve(request(), complete=no_classifier, **args)
    del args['settings']['policy']
    req = request(); req['prior_pin'] = first['pin']
    with pytest.raises(RoutingUnavailable, match='policy changed'):
        resolve(req, complete=no_classifier, **args)


def test_pinned_vendor_comes_from_identity():
    def item(name, identity=None):
        return {'name': name, 'project_id': 7, **({'identity': identity} if identity else {})}
    anthropic = {'canonical': 'anthropic/claude-haiku-4-5', 'vendor': 'anthropic', 'kind': 'chat'}
    openai = {'canonical': 'openai/gpt-5-4', 'vendor': 'openai', 'kind': 'chat'}
    assert pinned_is_anthropic({'name': 'my-alias'}, [item('my-alias', anthropic)], 7) is True
    assert pinned_is_anthropic({'name': 'claude-proxy'}, [item('claude-proxy', openai)], 7) is False
    assert pinned_is_anthropic({'name': 'eu.anthropic.x'}, [item('eu.anthropic.x')], 7) is True
    assert pinned_is_anthropic({'name': 'gpt-5.4'}, [item('gpt-5.4')], 7) is False
    with pytest.raises(ValueError):
        pinned_is_anthropic({'name': 'gone'}, [item('gpt-5.4')], 7)
