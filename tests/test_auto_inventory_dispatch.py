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
    seen = []
    rpc = types.SimpleNamespace(
        projects_get_personal_project_id=lambda user: 7,
        configurations_get_auto_routing_settings=lambda project: args['settings'],
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
                                 keys=keys, seen=seen, lookup=lookup, metering=metering)


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
    method = preflight.Method(); method.descriptor = types.SimpleNamespace(config={})
    result = method.resolve_auto_routing({'method': 'POST', 'json': request(), 'headers': {}},
        {'project_id': 7, 'user': {'id': 42}})
    assert result['action'] == 'generate'
    assert result['trace']['inventory']['discovered'] == 5
    assert result['trace']['inventory']['qualified_variants'] == 8
    assert relay.seen == [(7, 42)]


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
