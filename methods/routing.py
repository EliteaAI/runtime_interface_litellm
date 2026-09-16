"""Auto preflight uses the same authenticated relay and Usage hooks as generation."""
import copy
import json
from pylon.core.tools import web
from tools import context, VaultClient
from ..routing.service import resolve, PROFILE, RoutingUnavailable, RoutingAdmissionDenied, compiled_router
from ..routing.inventory import effective_models, model_binding, qualified_inventory
from ..utils.metering import meter_llm_call


class Method:
    @web.method()
    def resolve_auto_routing(self, proxy_target, proxy_auth):
        project_id = proxy_auth['project_id']
        settings = context.rpc_manager.timeout(10).configurations_get_auto_routing_settings(project_id)
        if proxy_target['method'] == 'GET':
            return {'enabled': bool(settings['enabled']), 'profile_ref': PROFILE,
                    'surfaces': ['chat', 'agent'], 'revision': settings['revision']}
        if proxy_target['method'] != 'POST':
            return {'error': 'Method not allowed'}, 405
        if not settings['enabled']:
            return {'error': 'Auto model selection is disabled'}, 403
        snapshot = context.rpc_manager.timeout(10).configurations_get_routing_models(project_id, proxy_auth['user']['id'])
        models = snapshot['items']
        inventory = effective_models(models, project_id)
        router = compiled_router()
        variants, _ = qualified_inventory(inventory, router.catalog)
        # Quote discovered qualified bindings only; unqualified inventory can
        # exceed the bounded Costs RPC and cannot become a candidate anyway.
        names = sorted({model['name'] for model in variants.values()})
        prices = context.rpc_manager.timeout(10).costs_get_routing_prices(names)
        key = VaultClient(project_id).get_secrets().get('project_llm_key')
        if not key:
            return {'error': 'Auto model selection is unavailable'}, 503

        def complete(model, messages, effort, max_tokens):
            body = {'model': model, 'messages': messages, 'max_tokens': max_tokens, 'stream': False}
            if effort:
                body['reasoning_effort'] = effort
            target = {**copy.copy(proxy_target), 'url': 'v1/chat/completions', 'endpoint': '/v1/chat/completions',
                      'method': 'POST', 'params': {}, 'headers': proxy_target['headers'].copy(),
                      'json': body, 'data': None, 'files': None}
            if proxy_auth.get('platform_run_id'):
                target['headers']['X-Elitea-Run-Id'] = proxy_auth['platform_run_id']
            identity = {**proxy_auth}
            # Internal authenticated state, never accepted from a request header.
            identity['_auto_model_binding'] = model_binding(inventory[model])
            denied = self.prepare_request(target, identity)
            if denied is not None:
                raise RoutingAdmissionDenied(denied)
            response_id = self.stream_node.add_stream()
            emitter = None
            try:
                request_id = self.service_node.call.litellm_request_start(response_id)
                emitter = self.stream_node.get_emitter(request_id)
                consumer = self.stream_node.get_consumer(response_id, timeout=60)
                iterator = iter(consumer)
                emitter.chunk(target)
                response = next(iterator)
                self.prepare_response(target, identity, response)
                raw = b''.join(part if isinstance(part, bytes) else part.encode() for part in meter_llm_call(target, identity, response, iterator))
                if response['status_code'] != 200:
                    raise RoutingUnavailable('Classifier request failed')
                value = json.loads(raw)
                choice = value['choices'][0]
                return {'message': choice['message'], 'finish_reason': choice['finish_reason'], 'usage': value.get('usage')}
            finally:
                if emitter is not None:
                    emitter.end()
                self.stream_node.remove_stream(response_id)

        try:
            return resolve(proxy_target['json'], project_id=project_id, user_id=proxy_auth['user']['id'],
                           settings=settings, models=models, price_snapshot=prices, signing_key=key, complete=complete)
        except RoutingAdmissionDenied as denied:
            return denied.response
        except (ValueError, KeyError, TypeError):
            return {'error': 'Auto cannot resolve this request under the current profile'}, 422
