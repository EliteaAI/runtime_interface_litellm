"""Auto preflight uses the same authenticated relay and Usage hooks as generation."""
import copy
import json
from pylon.core.tools import web, log
from tools import context, VaultClient
from ..routing.service import (resolve, PROFILE, RoutingUnavailable, RoutingAdmissionDenied, compiled_router,
                               auto_routing_settings, deployment_signals, decision_log_line)
from ..routing.pricing import PriceBook
from ..routing.inventory import effective_models, model_binding, qualified_inventory
from ..routing.escalation import usage_record
from ..utils.metering import meter_llm_call, record_escalation

UNRESOLVED = 'Auto cannot resolve this request under the current profile'
MAX_PRICE_NAMES = 64  # Costs get_routing_prices bound.


def routing_prices(rpc, names, canonical_by_name):
    """Exact prices, plus labelled alias matches when Costs supports them (#6826)."""
    if canonical_by_name:
        try:
            return rpc.costs_get_routing_prices(names, canonical_by_name=canonical_by_name)
        except TypeError:
            pass  # Costs before #6826 accepts exact names only.
    return rpc.costs_get_routing_prices(names)


def routing_price_snapshot(rpc, inventory, catalog, project_id, classifier=None):
    """Join the inventory and quote its bound deployments, the classifier and,
    within the Costs bound, ranked duplicates. Returns (variants, alternates, snapshot);
    `snapshot['unquoted']` lists alternates beyond the bound (treated as unpriced)."""
    alternates = {}
    variants, _ = qualified_inventory(inventory, catalog, project_id=project_id, alternates=alternates)
    # Unqualified inventory can exceed the bounded Costs RPC and is never quoted.
    names = {model['name'] for model in variants.values()}
    if isinstance(classifier, dict) and classifier.get('name') in inventory:
        names.add(classifier['name'])
    names, unquoted = sorted(names), []
    # Ranked duplicates price a fallback for an unpriced bound deployment;
    # winners first, within the Costs bound.
    for name in (n for vid in variants for n in alternates.get(vid, ())):
        if name not in names and name not in unquoted:
            (names if len(names) < MAX_PRICE_NAMES else unquoted).append(name)
    canonical = {name: inventory[name]['identity']['canonical'] for name in names
                 if isinstance((inventory[name].get('identity') or {}).get('canonical'), str)}
    snapshot = routing_prices(rpc, names, canonical)
    return variants, alternates, {**snapshot, 'unquoted': unquoted}


def auto_preflight(rpc, project_id, user_id):
    """Request-independent pool health from the same actor inventory and price
    snapshot as the Auto preflight (`runtime_interface_litellm_auto_preflight`).

    Configurations readiness will consume this in a follow-up (DIAL-only and
    no-priced-low-tier warnings). Names only, never credentials.
    """
    inventory = effective_models(rpc.configurations_get_routing_models(project_id, user_id)['items'], project_id)
    catalog = compiled_router().catalog
    variants, alternates, snapshot = routing_price_snapshot(rpc, inventory, catalog, project_id)
    prices = PriceBook(snapshot['entries'], source_revision=snapshot['revision'])
    return deployment_signals(catalog, variants, alternates, inventory, prices)


class Method:
    @web.method()
    def resolve_auto_routing(self, proxy_target, proxy_auth):
        project_id = proxy_auth['project_id']
        settings = auto_routing_settings(context.rpc_manager.timeout(10), project_id, proxy_auth['user']['id'])
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
        _, _, prices = routing_price_snapshot(context.rpc_manager.timeout(10), inventory, router.catalog,
                                              project_id, settings.get('classifier'))
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

        def record(result=None, refused=None):
            try:  # Diagnostics must never change a routing outcome.
                log.info(decision_log_line(project_id, proxy_target.get('json'), result, refused))
                if result is not None:
                    record_escalation(usage_record(project_id, proxy_target['json'], result))
            except Exception:  # pylint: disable=W0703
                pass

        try:
            result = resolve(proxy_target['json'], project_id=project_id, user_id=proxy_auth['user']['id'],
                             settings=settings, models=models, price_snapshot=prices, signing_key=key, complete=complete)
            record(result)
            return result
        except RoutingAdmissionDenied as denied:
            record(refused='ADMISSION_DENIED')
            return denied.response
        except RoutingUnavailable as exc:
            record(refused=exc.reason or str(exc))
            if exc.reason:
                return {'error': str(exc), 'reason': exc.reason}, 422
            return {'error': UNRESOLVED, 'reason': None}, 422
        except (ValueError, KeyError, TypeError) as exc:
            # Our own fixed messages for ValueError; other classes by name only.
            record(refused=f'{type(exc).__name__}:{exc}' if type(exc) is ValueError else type(exc).__name__)
            return {'error': UNRESOLVED, 'reason': None}, 422
