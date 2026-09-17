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
from .v7.routing import GatewayError
from .checkpoint import session_for, publish, apply_observation, state_value, MAX_CHECKPOINT_BYTES
from .inventory import effective_models, model_binding, validate_binding, qualified_inventory, fingerprint

PROFILE = {'id': 'v7-quality-cost', 'revision': 1}
MAX_REQUEST_BYTES = 2_000_000
PIN_HEADER = 'X-Elitea-Routing-Pin'


class RoutingUnavailable(ValueError):
    pass


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

    def complete(self, model, messages, *, effort=None, max_tokens=900):
        try:
            return _REQUEST.get()['complete'](model, messages, effort=effort, max_tokens=max_tokens)
        except RoutingAdmissionDenied:
            raise
        except Exception as exc:
            raise GatewayError('Classifier transport unavailable') from exc


@lru_cache(maxsize=1)
def compiled_router():
    # Immutable source/profile is compiled once per process. Invocation data is
    # isolated in ContextVars; authority/prices remain current request snapshots.
    return CalibratedRouter(ClassifierTransport(), catalog=compile_catalog())


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


def resolve(request, *, project_id, user_id, settings, models, price_snapshot, signing_key, complete, now=None):
    if not settings.get('enabled'):
        raise RoutingUnavailable('Auto model selection is disabled for this project')
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
    if 'output_cap' in request and (type(cap) is not int or not 256 <= cap <= 32000):
        raise RoutingUnavailable('Auto output allowance must be between 256 and 32000 tokens')
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
    runtime_variants, exclusions = qualified_inventory(visible, catalog)
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
    if prior:
        restored = decode_pin(prior, signing_key, project_id=project_id, user_id=user_id,
            settings=settings, now=now, scope_id=scope_id, invocation_id=invocation_id, allow_expired=True)
        if restored.get('policy_revision') != router.revision:
            raise RoutingUnavailable('The checkpoint routing policy changed')
        # An omitted cap renews the original run contract, never the new-turn
        # default. A user-specified cap can narrow it but cannot enlarge it.
        if cap is None:
            cap = restored['config']['max_tokens']
    allowed = []
    output_caps = {}
    contract_exclusions = {}
    explicit = selection.get('reasoning', {})
    if explicit.get('mode') not in {'auto', 'explicit'}:
        raise RoutingUnavailable('Invalid Auto reasoning selection')
    for vid, model in runtime_variants.items():
        variant = catalog['variants'][vid]
        contract = variant.get('calibration_contract')
        variant_cap = cap if cap is not None else (contract['output_allowance'] if contract else 8000)
        if contract:
            native = not model.get('openai_compatible', False) and any(x in variant['model'].lower() for x in ('anthropic', 'claude'))
            transport = 'anthropic_messages' if native else 'chat_completions'
            reasons = []
            if transport != contract['transport']:
                reasons.append('CALIBRATION_TRANSPORT_UNMEASURED')
            if variant_cap != contract['output_allowance']:
                reasons.append('CALIBRATION_OUTPUT_ALLOWANCE_UNMEASURED')
            if output_schema is not None:
                reasons.append('CALIBRATION_STRUCTURED_OUTPUT_UNMEASURED')
            if explicit['mode'] == 'explicit':
                reasons.append('CALIBRATION_EXPLICIT_EFFORT_UNMEASURED')
            if reasons:
                # Missing evidence for this configuration is not model
                # incapability or a provider-family ban. Keep it observable.
                contract_exclusions[vid] = {'reasons': reasons,
                    'configured_transport': transport, 'measured_transport': contract['transport']}
                continue
        if explicit['mode'] == 'explicit' and variant['effort'] != explicit.get('preset'):
            continue
        # The current native Anthropic SDK uses bounded enabled-thinking for
        # this frozen pool. Its budget must fit inside Auto's total allowance.
        if 'anthropic' in variant['model'] and variant['effort'] and not model.get('openai_compatible'):
            thinking_budget = {'low': 2048, 'medium': 4096, 'high': 9092}[variant['effort']]
            if variant_cap <= thinking_budget:
                continue
        if type(model.get('context_window')) is not int or size+variant_cap > model['context_window']:
            continue
        if type(model.get('max_output_tokens')) is not int or variant_cap > model['max_output_tokens']:
            continue
        if prices.quote(variant['model'], Tokens(size, variant_cap))['usd'] is None:
            continue
        if variant.get('cache_write_mode') != 'ordinary_input' and prices.quote(variant['model'], Tokens(0, variant_cap, write=size))['usd'] is None:
            continue
        allowed.append(vid)
        output_caps[vid] = variant_cap
    # Classifier identity is supplied by the calibrated policy, never request text.
    classifier = catalog['variants'][router.classifier.variant]['model']
    if router.classifier.variant not in runtime_variants:
        raise RoutingUnavailable('The configured Auto classifier is unavailable')
    if prices.quote(classifier, Tokens(34000, 900))['usd'] is None:
        raise RoutingUnavailable('Classifier pricing is unavailable')
    if not allowed:
        raise RoutingUnavailable('No qualified model fits the request and pricing contract')
    if prior:
        try:
            validate_binding(restored.get('model_binding'), models, project_id)
        except ValueError as exc:
            raise RoutingUnavailable(str(exc)) from exc
        if restored.get('policy_revision') != router.revision:
            raise RoutingUnavailable('The checkpoint routing policy changed')
        allowed = [v for v in allowed if catalog['variants'][v]['model'] == restored['config']['model_name']
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
        user_id=user_id, scope_id=scope_id, gate_revision=settings['revision'], policy_revision=router.revision)
    context_token = _REQUEST.set({'complete': complete, 'prices': prices, 'project_id': project_id, 'output_schema': output_schema, 'runtime_context': runtime_context, 'generation_input_bytes': size, 'output_caps': output_caps})
    try:
        key = f'{project_id}:{user_id}:{scope_id}'
        with session_for(key, request.get('state_token') if restored_state else None,
                         restored_state['checkpoint'] if restored_state else None) as entry:
            session = entry['session']
            apply_observation(session, restored_state, request.get('observation'), messages,
                              router.gateway, catalog, tools, router.revision)
            previous = restored_state['decision']['selection']['variant'] if restored_state else None
            decision = router.resolve(messages, output_cap=cap if cap is not None else 8000, tools=tools, allowed=allowed,
                session=session, previous=previous, access_revision=digest({'gate':settings['revision'],'inventory':inventory_revision,'context':context_revision(runtime_context)}),
                hint={'kind': 'agent_task' if request['surface'] == 'agent' else 'chat_turn'})
            state_token = None
            if decision.get('action') != 'clarify':
                cap = output_caps[decision['selection']['variant']]
                decision['budget']['completion_cap'] = cap
                state_token = encode_pin(state_value(session, decision, messages, scope_id=scope_id,
                    project_id=project_id, user_id=user_id, gate_revision=settings['revision'],
                    policy_revision=router.revision), signing_key)
                publish(entry, state_token)
    finally:
        _REQUEST.reset(context_token)

    trace = {k: copy.deepcopy(decision.get(k)) for k in ('descriptor', 'policy_revision', 'routing_ms', 'budget')}
    trace['selection'] = {k: copy.deepcopy(decision.get('selection', {}).get(k)) for k in ('variant', 'reason', 'family_qualification', 'economics')}
    classifier_info = decision.get('classifier') or {}
    trace['classifier'] = {k: classifier_info.get(k) for k in ('classifier_variant', 'schema_valid', 'error') if k in classifier_info}
    trace['classifier']['finish_reason'] = (classifier_info.get('response') or {}).get('finish_reason')
    trace['classifier']['called'] = bool(classifier_info)
    trace['instruction_chars'] = (runtime_context.get('active_instructions') or {}).get('total_chars', 0)
    trace['inventory'] = {'revision': inventory_revision, 'discovered': len(visible),
                          'qualified_variants': len(runtime_variants), 'admitted_variants': len(allowed),
                          'excluded': exclusions, 'unmeasured_contracts': contract_exclusions}
    if decision.get('action') == 'clarify':
        return {'action': 'clarify', 'text': decision['clarification'], 'trace': trace}
    selected = decision['selection']
    model = visible[selected['model']]
    config = {'model_name': selected['model'], 'model_project_id': model['project_id'],
              'reasoning_effort': selected['effort'], 'openai_compatible': model.get('openai_compatible', False),
              'max_output_tokens': model['max_output_tokens'], 'context_window': model['context_window'], 'max_tokens': cap,
              'routing_total_output_cap': True}
    contract = catalog['variants'][selected['variant']].get('calibration_contract')
    if contract:
        config['routing_transport'] = contract['transport']
        config['routing_min_output_cap'] = contract['output_allowance']
        trace['calibration_contract'] = {
            'revision': contract['revision'], 'source_sha256': contract['source_sha256'],
            'status': 'provisional_local_beta', 'production_promotion_allowed': False,
            'family': decision['descriptor']['task_family'],
            'cell': contract['families'][decision['descriptor']['task_family']]}
    pin = {'version': 1, 'project_id': project_id, 'user_id': user_id, 'profile_ref': PROFILE,
           'gate_revision': settings['revision'], 'config': config, 'invocation_id': invocation_id, 'scope_id': scope_id,
           'model_binding': model_binding(model), 'policy_revision': router.revision,
           'expires_at': int(time.time() if now is None else now)+3600}
    return {'action': 'generate', 'config': config, 'pin': encode_pin(pin, signing_key), 'trace': trace,
            'invocation_id': invocation_id, 'scope_id': scope_id, 'state_token': state_token, 'expires_at': pin['expires_at']}
