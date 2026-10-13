"""Authenticated routing preflight; no provider replacement or response conversion.

The SDK gets a native-client binding. The gateway checks its signed binding on
all later provider calls. Request text cannot expand measured qualification.
"""
import base64
import copy
import hashlib
import hmac
import json
import time
from contextvars import ContextVar
from functools import lru_cache
from .pricing import PriceBook, Tokens
from .context import read_context, digest, context_revision
from .v7.catalog import compile_catalog
from .v7.candidate import CalibratedRouter
from .v7.routing import GatewayError, CONFIGURED_CLASSIFIER
from .checkpoint import session_for, publish, apply_observation, state_value, MAX_CHECKPOINT_BYTES
from .inventory import effective_models, model_binding, validate_binding, qualified_inventory, fingerprint
from .gates import admit, is_anthropic
from .policy import runtime_policy, policy_revision as _policy_digest
from . import v5

PROFILE = {'id': 'v7-quality-cost', 'revision': 1}
MAX_REQUEST_BYTES = 2_000_000
PIN_HEADER = 'X-Elitea-Routing-Pin'
CLASSIFIER_REASONS = {'CLASSIFIER_NOT_CONFIGURED', 'CLASSIFIER_UNAVAILABLE', 'CLASSIFIER_NOT_CHAT'}


class RoutingUnavailable(ValueError):
    """`reason` is a stable code for product-visible failures (#6826), else None."""

    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason


def auto_routing_settings(rpc, project_id, user_id):
    """Actor-resolved settings: the classifier and `revision` depend on the actor's
    inventory (#6826), so resolve, renewal and relay checks pass the same actor."""
    try:
        return rpc.configurations_get_auto_routing_settings(project_id, user_id=user_id)
    except TypeError:
        return rpc.configurations_get_auto_routing_settings(project_id)  # Configurations before #6826.


def routing_policy_revision(router, settings):
    """Pins and checkpoints bind the code revision plus the effective policy and
    selector (#6722 V5). Without a policy (older Configurations) it is exactly
    the code revision, so pins stay identical to main."""
    policy, mode, error = runtime_policy(settings)
    if mode is None:
        return router.revision
    if error:
        return router.revision+'+policy-'+_policy_digest({'error': error})
    artifact = policy['selector']['artifact']
    try:
        from .selector import installed
        selector = f"{artifact}@{installed(artifact).sha256}"
    except ValueError:
        selector = artifact
    return router.revision+'+policy-'+_policy_digest(policy, selector)


def pinned_is_anthropic(binding, models, project_id):
    """Vendor of the pinned deployment from its identity; exact-name heuristic
    only for inventory items without identity (#6826 follow-up)."""
    current = effective_models(models, project_id).get(binding['name'])
    if current is None:
        raise ValueError('Pinned model configuration changed or is no longer available')
    return is_anthropic(current, {'model': current['name']})


class RoutingAdmissionDenied(Exception):
    """Owner denial must escape the classifier's recoverable ValueError path."""

    def __init__(self, response):
        super().__init__('Classifier admission denied')
        self.response = response


def encode_pin(value, key):
    body = base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return body+'.'+sig


def decode_pin(token, key, *, project_id, user_id, settings, now=None, invocation_id=None, scope_id=None, allow_expired=False):
    if not isinstance(token, str) or len(token) > 12000:
        raise RoutingUnavailable('Invalid routing pin')
    try:
        body, sig = token.rsplit('.', 1)
        expected = hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(body+'='*((-len(body)) % 4)))
        if (value['project_id'] != project_id or value['user_id'] != user_id or
                (not allow_expired and value['expires_at'] <= (time.time() if now is None else now)) or
                (invocation_id is not None and value.get('invocation_id') != invocation_id) or
                (scope_id is not None and value.get('scope_id') != scope_id) or
                value['gate_revision'] != settings['revision'] or not settings['enabled'] or
                value['profile_ref'] != PROFILE):
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError) as exc:
        raise RoutingUnavailable('Routing pin expired, revoked or invalid') from exc


_REQUEST = ContextVar('auto_gateway_request')


class ClassifierTransport:
    @property
    def prices(self):
        return _REQUEST.get()['prices']

    @property
    def project(self):
        return _REQUEST.get()['project_id']

    @property
    def output_schema(self):
        return _REQUEST.get().get('output_schema')

    @property
    def generation_input_bytes(self):
        return _REQUEST.get().get('generation_input_bytes', 0)

    @property
    def output_caps(self):
        return _REQUEST.get().get('output_caps', {})

    @property
    def cache_policy_revision(self):
        return digest({"output_schema": self.output_schema, "runtime_context": context_revision(self.runtime_context)})

    @property
    def runtime_context(self):
        return _REQUEST.get().get("runtime_context", {})

    @property
    def classifier_contract(self):
        return _REQUEST.get()['classifier']

    @property
    def deployments(self):
        return _REQUEST.get().get('deployments', {})

    def complete(self, model, messages, *, effort=None, max_tokens=900):
        try:
            return _REQUEST.get()['complete'](model, messages, effort=effort, max_tokens=max_tokens)
        except RoutingAdmissionDenied:
            raise
        except Exception as exc:
            raise GatewayError('Classifier transport unavailable') from exc


class GenerationRouter(CalibratedRouter):
    """Product Auto chooses a model; only that model responds to the user."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.revision += '-model-owned-clarification-1'

    def _resolve(self, messages, mode, binding, hint, previous, allowed, min_demand):
        decision = super()._resolve(messages, mode, binding, hint, previous, allowed, min_demand)
        if decision.get('action') == 'clarify':
            # Remain inside the router's request ContextVar: qualification and
            # effort filters must see the same invocation contract. Retain the
            # uncertain descriptor; never invent sources or force a cheap route.
            decision['uncertainty'] = {'handling': 'generation_model',
                                       'reason': decision['selection']['reason']}
            decision['selection'] = self.select(decision['descriptor'], allowed, previous, min_demand)
            decision['action'] = 'generate'
            decision.pop('clarification', None)
        return decision


@lru_cache(maxsize=1)
def compiled_router():
    # Immutable source/profile is compiled once per process. Invocation data is
    # isolated in ContextVars; authority/prices remain current request snapshots.
    return GenerationRouter(ClassifierTransport(), CONFIGURED_CLASSIFIER, catalog=compile_catalog())


def restore_state(token, key, *, project_id, user_id, scope_id, gate_revision, policy_revision):
    if not token:
        return None
    try:
        if not isinstance(token, str) or len(token) > MAX_CHECKPOINT_BYTES*2:
            raise ValueError()
        body, sig = token.rsplit('.', 1)
        if not hmac.compare_digest(sig, hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(body+'='*((-len(body)) % 4)))
        if any(value.get(field) != expected for field, expected in {
                'kind': 'auto_session_v1', 'project_id': project_id, 'user_id': user_id,
                'scope_id': scope_id}.items()):
            raise ValueError()
        if value['gate_revision'] != gate_revision or value['policy_revision'] != policy_revision:
            # Current policy/admission can begin a new task; old intent evidence
            # cannot shortcut classification after a revision change.
            return None
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise RoutingUnavailable('Invalid or foreign routing checkpoint') from exc


def _configured_classifier(settings, visible, prices):
    # Classifier identity is the configured deployment (project, else platform),
    # never request text or a catalog literal. Missing settings fail closed.
    ref = settings.get('classifier')
    if not isinstance(ref, dict) or not isinstance(ref.get('name'), str) or type(ref.get('project_id')) is not int:
        # Configurations explains why nothing resolved; older plugins omit it.
        why = settings.get('classifier_reason') if ref is None else None
        if (isinstance(why, dict) and why.get('code') in CLASSIFIER_REASONS
                and isinstance(why.get('message'), str) and why['message']):
            raise RoutingUnavailable(why['message'][:512], why['code'])
        raise RoutingUnavailable('No Auto classifier model is configured', 'CLASSIFIER_NOT_CONFIGURED')
    classifier = visible.get(ref['name'])
    if (classifier is None or classifier['project_id'] != ref['project_id'] or classifier.get('available') is False
            or (classifier.get('identity') or {}).get('kind', 'chat') != 'chat'):
        raise RoutingUnavailable(f"The configured Auto classifier {ref['name']} is unavailable", 'CLASSIFIER_UNAVAILABLE')
    if prices.quote(classifier['name'], Tokens(34000, 900))['usd'] is None:
        raise RoutingUnavailable('Classifier pricing is unavailable', 'CLASSIFIER_PRICE_UNAVAILABLE')
    return classifier, ref


def resolve(request, *, project_id, user_id, settings, models, price_snapshot, signing_key, complete, now=None):
    if not settings.get('enabled'):
        raise RoutingUnavailable('Auto model selection is disabled for this project')
    # None: no policy (legacy only, as before #6722 V5); 'shadow': legacy runs,
    # V5 is traced; 'active': V5 runs. Invalid policy -> today's literals, traced.
    policy, policy_mode, policy_error = runtime_policy(settings)
    out_policy = policy['output']
    if not isinstance(request, dict) or len(json.dumps(request).encode()) > MAX_REQUEST_BYTES:
        raise RoutingUnavailable('Routing request exceeds the supported bound')
    scope_id = request.get('scope_id')
    if not isinstance(scope_id, str) or len(scope_id) != 64 or any(c not in '0123456789abcdef' for c in scope_id):
        raise RoutingUnavailable('An execution scope identity is required')
    invocation_id = request.get('invocation_id')
    if not isinstance(invocation_id, str) or len(invocation_id) != 64 or any(c not in '0123456789abcdef' for c in invocation_id):
        raise RoutingUnavailable('A stable invocation identity is required')
    selection = request.get('selection') or {}
    if selection.get('mode') != 'auto' or selection.get('profile_ref') != PROFILE:
        raise RoutingUnavailable('Unknown or stale Auto profile')
    if request.get('surface') not in {'chat', 'agent'}:
        raise RoutingUnavailable('Auto is unavailable for this execution surface')
    cap = request.get('output_cap')
    if 'output_cap' in request and (type(cap) is not int or not out_policy['min_cap'] <= cap <= out_policy['max_cap']):
        raise RoutingUnavailable(f"Auto output allowance must be between {out_policy['min_cap']} and "
                                 f"{out_policy['max_cap']} tokens")
    messages = request.get('messages')
    if not isinstance(messages, list) or not messages or len(messages) > 4096:
        raise RoutingUnavailable('Auto requires a bounded task history')
    # Classifier projection only. Full native blocks stay in SDK generation.
    if any(not isinstance(m, dict) or not isinstance(m.get('content'), str) for m in messages):
        raise RoutingUnavailable('Unsupported classifier history')
    tools = request.get('tools') or []
    if not isinstance(tools, list) or len(tools) > 256:
        raise RoutingUnavailable('Unsupported tool inventory')
    prices = PriceBook(price_snapshot['entries'], source_revision=price_snapshot['revision'])
    router = compiled_router()
    catalog = router.catalog
    visible = effective_models(models, project_id)
    runtime_variants, exclusions = qualified_inventory(visible, catalog, project_id=project_id)
    inventory_revision = fingerprint([model_binding(m) for m in visible.values()])
    output_schema = request.get('output_schema')
    if output_schema is not None and not isinstance(output_schema, dict):
        raise RoutingUnavailable('Unsupported structured-output schema')
    size = len(json.dumps({'messages': messages, 'tools': tools, 'output_schema': output_schema}, ensure_ascii=False).encode()) + 64*len(messages)
    generation_size = request.get('generation_input_bytes', 0)
    if type(generation_size) is not int or not 0 <= generation_size <= 16_000_000:
        raise RoutingUnavailable('Invalid generation input size bound')
    size = max(size, generation_size)
    prior = request.get('prior_pin')
    restored = None
    revision = routing_policy_revision(router, settings)
    if prior:
        restored = decode_pin(prior, signing_key, project_id=project_id, user_id=user_id,
            settings=settings, now=now, scope_id=scope_id, invocation_id=invocation_id, allow_expired=True)
        if restored.get('policy_revision') != revision:
            raise RoutingUnavailable('The checkpoint routing policy changed')
        # An omitted cap renews the original run contract, never the new-turn
        # default. A user-specified cap can narrow it but cannot enlarge it.
        if cap is None:
            cap = restored['config']['max_tokens']
    explicit = selection.get('reasoning', {})
    if explicit.get('mode') not in {'auto', 'explicit'}:
        raise RoutingUnavailable('Invalid Auto reasoning selection')
    allowed, output_caps, contract_exclusions, gate_report = admit(
        runtime_variants, catalog, cap=cap, size=size, output_schema=output_schema, explicit=explicit,
        prices=prices, policy=policy)
    active = policy_mode == 'active'
    classifier, ref = (None, None) if active else _configured_classifier(settings, visible, prices)
    if not allowed:
        raise RoutingUnavailable('No qualified model fits the request and pricing contract', 'NO_QUALIFIED_MODELS')
    if prior:
        try:
            validate_binding(restored.get('model_binding'), models, project_id)
        except ValueError as exc:
            raise RoutingUnavailable(str(exc)) from exc
        if restored.get('policy_revision') != revision:
            raise RoutingUnavailable('The checkpoint routing policy changed')
        allowed = [v for v in allowed if runtime_variants[v]['name'] == restored['config']['model_name']
                   and catalog['variants'][v]['effort'] == restored['config']['reasoning_effort']]
        if not allowed:
            raise RoutingUnavailable('The checkpoint model is no longer admitted')
        if restored.get('invocation_id') == invocation_id:
            # Reauthorize a durable tool-cycle binding, never reclassify its result.
            if cap > restored['config']['max_tokens']:
                raise RoutingUnavailable('The checkpoint output allowance cannot be enlarged')
            restored['config']['max_tokens'] = cap
            restored['expires_at'] = int(time.time() if now is None else now)+3600
            return {'action': 'generate', 'config': restored['config'], 'pin': encode_pin(restored, signing_key),
                    'invocation_id': invocation_id, 'expires_at': restored['expires_at'],
                    'scope_id': scope_id, 'state_token': request.get('state_token'),
                    'trace': {'reason': 'REAUTHORIZED_CHECKPOINT_PIN'}}
    runtime_context = read_context(request, key=signing_key, project_id=project_id, user_id=user_id, now=now)
    restored_state = restore_state(request.get('state_token'), signing_key, project_id=project_id,
        user_id=user_id, scope_id=scope_id, gate_revision=settings['revision'], policy_revision=revision)
    context_token = _REQUEST.set({'complete': complete, 'prices': prices, 'project_id': project_id, 'output_schema': output_schema, 'runtime_context': runtime_context, 'generation_input_bytes': size, 'output_caps': output_caps,
                                  'classifier': {'model': classifier['name'], 'effort': None} if classifier else None,
                                  'deployments': {vid: m['name'] for vid, m in runtime_variants.items()}})
    try:
        key = f'{project_id}:{user_id}:{scope_id}'
        with session_for(key, request.get('state_token') if restored_state else None,
                         restored_state['checkpoint'] if restored_state else None) as entry:
            session = entry['session']
            apply_observation(session, restored_state, request.get('observation'), messages,
                              router.gateway, catalog, tools, revision)
            previous = restored_state['decision']['selection']['variant'] if restored_state else None
            v5_inputs = dict(policy=policy, mode=policy_mode, report=gate_report, allowed=allowed,
                             runtime_variants=runtime_variants, catalog=catalog, output_caps=output_caps,
                             prices=prices, messages=messages, tools=tools, output_schema=output_schema, size=size,
                             runtime_context=runtime_context, previous=previous)
            if active:
                v5_trace = v5.decide(**v5_inputs)
                if v5_trace['chosen'] is None:
                    code = 'SELECTOR_UNAVAILABLE' if v5_trace['reason'] == 'SELECTOR_UNAVAILABLE' else 'NO_QUALIFIED_MODELS'
                    raise RoutingUnavailable('No qualified model fits the request and pricing contract'
                                             if code == 'NO_QUALIFIED_MODELS' else
                                             'The configured Auto selector is unavailable', code)
                chosen = v5_trace['chosen']
                decision = {'action': 'generate', 'descriptor': None, 'classifier': None, 'budget': {},
                            'policy_revision': revision,
                            'selection': {'variant': chosen['variant'], 'model': runtime_variants[chosen['variant']]['name'],
                                          'effort': chosen['effort'], 'reason': 'V5_'+v5_trace['reason']}}
            else:
                decision = router.resolve(messages, output_cap=cap if cap is not None else out_policy['default_allowance'],
                    tools=tools, allowed=allowed, session=session, previous=previous,
                    access_revision=digest({'gate':settings['revision'],'inventory':inventory_revision,'context':context_revision(runtime_context)}),
                    hint={'kind': 'agent_task' if request['surface'] == 'agent' else 'chat_turn'})
            cap = output_caps[decision['selection']['variant']]
            decision['budget']['completion_cap'] = cap
            state_token = encode_pin(state_value(session, decision, messages, scope_id=scope_id,
                project_id=project_id, user_id=user_id, gate_revision=settings['revision'],
                policy_revision=revision), signing_key)
            publish(entry, state_token)
    finally:
        _REQUEST.reset(context_token)

    trace = {k: copy.deepcopy(decision.get(k)) for k in ('descriptor', 'policy_revision', 'routing_ms', 'budget')}
    trace['selection'] = {k: copy.deepcopy(decision.get('selection', {}).get(k)) for k in ('variant', 'reason', 'family_qualification', 'economics')}
    if classifier is not None:
        classifier_info = decision.get('classifier') or {}
        trace['classifier'] = {k: classifier_info.get(k) for k in ('classifier_variant', 'schema_valid', 'error') if k in classifier_info}
        trace['classifier']['finish_reason'] = (classifier_info.get('response') or {}).get('finish_reason')
        trace['classifier']['called'] = classifier_info.get('called', bool(classifier_info))
        trace['classifier']['classifier_variant'] = CONFIGURED_CLASSIFIER
        trace['classifier']['deployment'] = {'name': classifier['name'], 'project_id': classifier['project_id'],
                                             'source': ref.get('source')}
    else:
        trace['classifier'] = {'called': False, 'classifier_variant': None}
    if policy_mode is not None:
        if policy_error:
            v5_trace = {'reason': policy_error, 'shadow': True, 'chosen': None, 'candidates': []}
        elif not active:
            # Shadow: observe the V5 choice; the legacy decision is executed.
            # A shadow defect is reported, never allowed to break legacy Auto.
            try:
                v5_trace = v5.decide(**v5_inputs)
            except Exception as exc:  # pylint: disable=W0703
                v5_trace = {'reason': 'V5_ERROR', 'error': type(exc).__name__, 'shadow': True,
                            'chosen': None, 'candidates': []}
        v5_trace['legacy_choice'] = None if active else {
            'variant': decision['selection']['variant'], 'deployment': runtime_variants[decision['selection']['variant']]['name'],
            'effort': decision['selection']['effort']}
        v5_trace['policy_revision'] = revision
        trace['v5'] = v5_trace
    entries = price_snapshot['entries']
    trace['prices'] = {'match': {e['model_name']: e.get('match', 'exact') for e in entries},
                       'aliases': {e['model_name']: e['matched_name'] for e in entries if e.get('match') == 'alias'}}
    trace['instruction_chars'] = (runtime_context.get('active_instructions') or {}).get('total_chars', 0)
    trace['inventory'] = {'revision': inventory_revision, 'discovered': len(visible),
                          'qualified_variants': len(runtime_variants), 'admitted_variants': len(allowed),
                          'excluded': exclusions, 'unmeasured_contracts': contract_exclusions}
    if 'uncertainty' in decision:
        trace['uncertainty'] = decision['uncertainty']
    selected = decision['selection']
    # The measured variant was chosen; its bound live deployment is dispatched.
    model = runtime_variants[selected['variant']]
    config = {'model_name': model['name'], 'model_project_id': model['project_id'],
              'reasoning_effort': selected['effort'], 'openai_compatible': model.get('openai_compatible', False),
              'max_output_tokens': model['max_output_tokens'], 'context_window': model['context_window'], 'max_tokens': cap,
              'routing_total_output_cap': True}
    contract = catalog['variants'][selected['variant']].get('calibration_contract')
    if contract:
        config['routing_transport'] = contract['transport']
        config['routing_min_output_cap'] = contract['output_allowance']
        if 'reasoning_fields' in contract:
            config['routing_reasoning_fields'] = copy.deepcopy(contract['reasoning_fields'])
        trace['calibration_contract'] = {
            'revision': contract['revision'], 'source_sha256': contract['source_sha256'],
            'status': 'provisional_local_beta', 'production_promotion_allowed': False}
        if decision['descriptor'] is not None:  # V5 decisions carry no task family.
            trace['calibration_contract'].update(family=decision['descriptor']['task_family'],
                cell=contract['families'][decision['descriptor']['task_family']])
    pin = {'version': 1, 'project_id': project_id, 'user_id': user_id, 'profile_ref': PROFILE,
           'gate_revision': settings['revision'], 'config': config, 'invocation_id': invocation_id, 'scope_id': scope_id,
           'model_binding': model_binding(model), 'policy_revision': revision,
           'expires_at': int(time.time() if now is None else now)+3600}
    return {'action': 'generate', 'config': config, 'pin': encode_pin(pin, signing_key), 'trace': trace,
            'invocation_id': invocation_id, 'scope_id': scope_id, 'state_token': state_token, 'expires_at': pin['expires_at']}
