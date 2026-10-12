"""Authenticated preflight and relay use the same project-over-shared binding."""
import importlib
import types
from unittest.mock import Mock

import pytest

from test_auto_routing_service import fixture, request


@pytest.fixture
def relay(monkeypatch):
    # Load the existing relay fixture at execution time, after independent test
    # modules have installed their own import contracts during collection.
    from test_metering_handover import proxy
    args = fixture(); args['user_id'] = 42
    args.pop('now')
    models = args['models']
    seen, settings_calls = [], []
    rpc = types.SimpleNamespace(
        projects_get_personal_project_id=lambda user: 7,
        configurations_get_auto_routing_settings=lambda project, user_id=None: (
            settings_calls.append((project, user_id)) or args['settings']),
        configurations_get_routing_models=lambda project, user: (
            seen.append((project, user)) or {'items': models, 'revision': 'test'}),
        costs_get_routing_prices=lambda names: args['price_snapshot'],
    )
    context = types.SimpleNamespace(rpc_manager=types.SimpleNamespace(timeout=lambda t: rpc))
    monkeypatch.setattr(proxy, 'context', context)
    monkeypatch.setattr(proxy, 'this', types.SimpleNamespace(descriptor=types.SimpleNamespace(config={})))
    keys = []
    def vault(project):
        keys.append(project)
        return types.SimpleNamespace(get_secrets=lambda: {'project_llm_key': args['signing_key']})
    monkeypatch.setattr(proxy, 'VaultClient', vault)
    metering = Mock(return_value=None)
    monkeypatch.setattr(proxy, 'prepare_llm_call', metering)
    method = proxy.Method()
    method.preprocess_headers = lambda headers: headers
    method.descriptor = types.SimpleNamespace(config={})
    method.get_public_project_id = lambda: 1
    lookup = Mock(return_value={'available': True})
    method.service_node = types.SimpleNamespace(call=types.SimpleNamespace(litellm_api_call=lookup))
    return types.SimpleNamespace(proxy=proxy, args=args, models=models, method=method, context=context, vault=vault,
                                 keys=keys, seen=seen, lookup=lookup, metering=metering, settings_calls=settings_calls)


def signed_request(relay):
    service = importlib.import_module('plugin_under_test.routing.service')
    result = service.resolve(request(), complete=lambda *a, **k: pytest.fail('Greeting classified'), **relay.args)
    config = result['config']
    target = {'endpoint': '/v1/chat/completions', 'headers': relay.proxy.Headers({
        'X-Elitea-Routing-Pin': result['pin'], 'X-Elitea-Routing-Invocation': 'a'*64}),
        'json': {'model': config['model_name'], 'reasoning_effort': config['reasoning_effort'],
                 'max_tokens': config['max_tokens']}, 'data': None}
    return target, {'type': 'token', 'user': {'id': 42, 'name': 'user'}}, config


def test_auto_dispatch_uses_exact_owner_and_callers_key(relay):
    target, auth, config = signed_request(relay)
    assert relay.method.prepare_request(target, auth) is None
    relay.lookup.assert_called_once_with('model_group_info', '7_'+config['model_name'])
    assert relay.keys == [7] and relay.seen == [(7, 42)]
    assert target['headers']['Authorization'] == 'Bearer unit-test-only'
    assert target['json']['model'] == '7_'+config['model_name']
    assert relay.metering.call_args.args[3] == 7


def test_auto_dispatch_does_not_fall_back_when_project_deployment_disappears(relay):
    target, auth, config = signed_request(relay)
    relay.lookup.return_value = None
    assert relay.method.prepare_request(target, auth)[1] == 503
    relay.lookup.assert_called_once_with('model_group_info', '7_'+config['model_name'])
    relay.metering.assert_not_called()


def test_same_name_shared_replacement_rejected_before_relay(relay):
    target, auth, config = signed_request(relay)
    selected = next(m for m in relay.models if m['name'] == config['model_name'])
    selected.update(project_id=1, shared=True)
    assert relay.method.prepare_request(target, auth)[1] == 409
    relay.lookup.assert_not_called()


def test_internal_classifier_binding_uses_same_inventory_and_exact_dispatch(relay):
    from plugin_under_test.routing.inventory import model_binding
    model = relay.models[0]
    model.update(project_id=1, shared=True)
    target = {'endpoint': '/v1/chat/completions', 'headers': relay.proxy.Headers({}),
              'json': {'model': model['name']}, 'data': None}
    auth = {'type': 'token', 'user': {'id': 42, 'name': 'user'}, '_auto_model_binding': model_binding(model)}
    assert relay.method.prepare_request(target, auth) is None
    relay.lookup.assert_called_once_with('model_group_info', '1_'+model['name'])
    assert relay.keys == [7] and relay.metering.call_args.args[3] == 1


def test_preflight_reads_actor_inventory_and_keeps_shared_only_candidates(relay, monkeypatch):
    preflight = importlib.import_module('plugin_under_test.methods.routing')
    monkeypatch.setattr(preflight, 'context', relay.context)
    monkeypatch.setattr(preflight, 'VaultClient', relay.vault)
    for model in relay.models:
        model.update(project_id=1, shared=True)
    private = {**relay.models[0], 'project_id': 7, 'shared': False}
    relay.models.append(private)
    relay.args['settings']['classifier']['project_id'] = 1
    method = preflight.Method(); method.descriptor = types.SimpleNamespace(config={})
    result = method.resolve_auto_routing({'method': 'POST', 'json': request(), 'headers': {}},
        {'project_id': 7, 'user': {'id': 42}})
    assert result['action'] == 'generate'
    assert result['trace']['inventory']['discovered'] == 5
    assert result['trace']['inventory']['qualified_variants'] == 8
    assert relay.seen == [(7, 42)]


def preflight_method(relay, monkeypatch):
    preflight = importlib.import_module('plugin_under_test.methods.routing')
    monkeypatch.setattr(preflight, 'context', relay.context)
    monkeypatch.setattr(preflight, 'VaultClient', relay.vault)
    method = preflight.Method(); method.descriptor = types.SimpleNamespace(config={})
    return lambda: method.resolve_auto_routing({'method': 'POST', 'json': request(), 'headers': {}},
                                               {'project_id': 7, 'user': {'id': 42}})


def test_preflight_prices_classifier_and_passes_canonical_identities(relay, monkeypatch):
    calls = []
    def prices(names, canonical_by_name=None):
        calls.append((names, canonical_by_name))
        return relay.args['price_snapshot']
    relay.context.rpc_manager.timeout(10).costs_get_routing_prices = prices
    classifier = {'name': 'claude-haiku-5-5@default', 'project_id': 7, 'context_window': 200000,
                  'max_output_tokens': 8000, 'identity': {'canonical': 'anthropic/claude-haiku-5-5', 'kind': 'chat'}}
    relay.models.append(classifier)
    relay.args['price_snapshot']['entries'].append({**relay.args['price_snapshot']['entries'][0],
                                                    'model_name': classifier['name']})
    relay.args['settings']['classifier'] = {'name': classifier['name'], 'project_id': 7, 'source': 'project'}
    result = preflight_method(relay, monkeypatch)()
    assert result['trace']['classifier']['deployment']['name'] == classifier['name']
    [(names, canonical)] = calls
    assert classifier['name'] in names and len(names) == 6
    assert canonical == {m['name']: m['identity']['canonical'] for m in relay.models}


def test_preflight_keeps_working_with_exact_only_costs_signature(relay, monkeypatch):
    calls = []
    relay.context.rpc_manager.timeout(10).costs_get_routing_prices = lambda names: calls.append(names) or relay.args['price_snapshot']
    assert preflight_method(relay, monkeypatch)()['action'] == 'generate'
    assert len(calls) == 1 and 'global.openai.gpt-5.6-luna' in calls[0]


def test_preflight_422_carries_the_reason_code(relay, monkeypatch):
    relay.args['settings']['classifier'] = None
    body, status = preflight_method(relay, monkeypatch)()
    assert status == 422
    assert body == {'error': 'No Auto classifier model is configured', 'reason': 'CLASSIFIER_NOT_CONFIGURED'}


def test_preflight_missing_or_malformed_classifier_is_reason_coded(relay, monkeypatch):
    relay.models.clear()
    body, status = preflight_method(relay, monkeypatch)()
    assert status == 422
    assert body['reason'] == 'CLASSIFIER_UNAVAILABLE'
    relay.args['settings']['classifier']['name'] = 7
    assert preflight_method(relay, monkeypatch)()[0]['reason'] == 'CLASSIFIER_NOT_CONFIGURED'


def measured_request(relay, variant):
    from test_v9_candidate import inputs, classified
    data = inputs(variant)
    relay.models[:] = data['models']
    relay.args['price_snapshot'] = data['price_snapshot']
    service = importlib.import_module('plugin_under_test.routing.service')
    result = service.resolve(request('Transform these records A=1 B=2.'),
                             complete=lambda *a, **k: classified(), **relay.args)
    config = result['config']
    target = {'endpoint': '/v1/messages' if config['routing_transport']=='anthropic_messages' else '/v1/chat/completions',
        'headers': relay.proxy.Headers({'X-Elitea-Routing-Pin': result['pin'],
                                       'X-Elitea-Routing-Invocation': 'a'*64}),
        'json': {'model': config['model_name'], 'max_tokens': config['max_tokens']}, 'data': None}
    return target, {'type': 'token', 'user': {'id': 42, 'name': 'user'}}


@pytest.mark.parametrize('variant', ['terra-default','sol-default','opus5-default','opus48-default','opus47-default'])
def test_new_measured_contract_exact_native_dispatch(relay,variant):
    target,auth=measured_request(relay,variant)
    assert relay.method.prepare_request(target,auth) is None
    relay.lookup.assert_called_once()
    relay.metering.assert_called_once()


@pytest.mark.parametrize('change', [ {'endpoint':'/v1/responses'}, {'endpoint':'/v1/chat/completions'},
    {'body':{'max_tokens':8000}}, {'body':{'max_tokens':33000}},
    {'body':{'thinking':{'type':'adaptive'}}}, {'body':{'thinking':{'type':'disabled'}}},
    {'body':{'thinking':{'type':'enabled','budget_tokens':4096}}},
    {'body':{'reasoning':{'summary':'auto'}}}, {'body':{'reasoning_effort':'high'}}])
def test_measured_default_contract_cannot_change_transport_allowance_or_thinking(relay,change):
    target,auth=measured_request(relay,'opus5-default')
    if 'endpoint' in change:target['endpoint']=change['endpoint']
    target['json'].update(change.get('body',{}))
    assert relay.method.prepare_request(target,auth)[1]==409
    relay.lookup.assert_not_called()
    relay.metering.assert_not_called()


@pytest.mark.parametrize('preset,field', [('terra-high','reasoning'),('opus5-high','thinking'),
    ('opus5-high','output_config'),('terra-high','max_tokens'),('terra-high','model')])
@pytest.mark.parametrize('scope', ['additional_drop_params','model_drop_params'])
def test_parameter_drops_cannot_change_measured_auto_contract(relay,preset,field,scope):
    from test_v12_efforts import inputs, descriptor, DATA
    service=importlib.import_module('plugin_under_test.routing.service')
    data=inputs(preset)
    relay.models[:]=data['models'];relay.args['price_snapshot']=data['price_snapshot']
    contract=DATA['variants'][preset]
    cell=next(r for r in DATA['records'] if r['variant']==preset and r['eligible_local_beta'])
    req=request('Analyze supplied evidence');req['selection']['reasoning']={'mode':'explicit','preset':contract['effort']}
    import json
    desc=descriptor(cell['family'],cell['demand_coverage'][0],contract['effort'])
    result=service.resolve(req,complete=lambda *a,**k:{'message':{'content':json.dumps(desc)},'finish_reason':'stop'},**relay.args)
    config=result['config']
    target={'endpoint':'/v1/messages' if config['routing_transport']=='anthropic_messages' else '/v1/chat/completions',
            'headers':relay.proxy.Headers({'X-Elitea-Routing-Pin':result['pin'],'X-Elitea-Routing-Invocation':'a'*64}),
            'json':{'model':config['model_name'],'max_tokens':config['max_tokens'],**config['routing_reasoning_fields']},'data':None}
    drops=[field] if scope=='additional_drop_params' else {config['model_name']:[field]}
    relay.proxy.this.descriptor.config['additional_litellm_params']={scope:drops}
    assert relay.method.prepare_request(target,{'type':'token','user':{'id':42,'name':'user'}})[1]==409
    relay.lookup.assert_not_called()
    relay.metering.assert_not_called()


def test_resolve_and_relay_read_settings_for_the_same_actor(relay, monkeypatch):
    # Configurations resolves the classifier per actor; `revision` covers it.
    rpc = relay.context.rpc_manager.timeout(10)
    def settings(project, user_id=None):
        relay.settings_calls.append((project, user_id))
        return {**relay.args['settings'], 'revision': f'g-{user_id}'}
    rpc.configurations_get_auto_routing_settings = settings
    result = preflight_method(relay, monkeypatch)()
    config = result['config']
    target = {'endpoint': '/v1/chat/completions', 'headers': relay.proxy.Headers({
        'X-Elitea-Routing-Pin': result['pin'], 'X-Elitea-Routing-Invocation': 'a'*64}),
        'json': {'model': config['model_name'], 'reasoning_effort': config['reasoning_effort'],
                 'max_tokens': config['max_tokens']}, 'data': None}
    assert relay.method.prepare_request(target, {'type': 'token', 'user': {'id': 42, 'name': 'user'}}) is None
    assert relay.settings_calls == [(7, 42), (7, 42)]
    # A revision computed for another actor (or none) never renews this pin.
    rpc.configurations_get_auto_routing_settings = lambda project, user_id=None: {
        **relay.args['settings'], 'revision': 'g-None'}
    target['headers'] = relay.proxy.Headers({'X-Elitea-Routing-Pin': result['pin'], 'X-Elitea-Routing-Invocation': 'a'*64})
    assert relay.method.prepare_request(target, {'type': 'token', 'user': {'id': 42, 'name': 'user'}})[1] == 409


def test_settings_fall_back_to_older_configurations_signature(relay, monkeypatch):
    calls = []
    relay.context.rpc_manager.timeout(10).configurations_get_auto_routing_settings = (
        lambda project: calls.append(project) or relay.args['settings'])
    assert preflight_method(relay, monkeypatch)()['action'] == 'generate'
    assert calls == [7]


def test_preflight_422_passes_configurations_classifier_reason(relay, monkeypatch):
    relay.args['settings'].update(classifier=None, classifier_reason={
        'code': 'CLASSIFIER_UNAVAILABLE', 'message': 'Classifier model luna is no longer available to this project',
        'model': {'name': 'luna', 'project_id': 1}})
    assert preflight_method(relay, monkeypatch)() == ({
        'error': 'Classifier model luna is no longer available to this project', 'reason': 'CLASSIFIER_UNAVAILABLE'}, 422)
