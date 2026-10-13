"""Authenticated routing preflight; no provider replacement or response conversion.

The SDK gets a native-client binding. The gateway checks its signed binding on
all later provider calls. Request text cannot expand measured qualification.
"""
import base64
import copy
import hashlib
import hmac
import json
import re
import time
from contextvars import ContextVar
from functools import lru_cache
from .pricing import PriceBook, Tokens, BYTES_PER_TOKEN, INPUT_ESTIMATOR, estimated_input_tokens
from .context import read_context, digest, context_revision
from .v7.catalog import compile_catalog
from .v7.candidate import CalibratedRouter
from .v7.economics import rank
from .v7.routing import GatewayError, CONFIGURED_CLASSIFIER
from .checkpoint import session_for, publish, apply_observation, state_value, MAX_CHECKPOINT_BYTES
from .inventory import effective_models, model_binding, validate_binding, qualified_inventory, fingerprint

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


def is_anthropic(model, variant):
    identity = model.get('identity')
    if isinstance(identity, dict):
        return identity.get('vendor') == 'anthropic'
    return any(x in variant['model'].lower() for x in ('anthropic', 'claude'))


def transport_of(model, variant):
    native = not model.get('openai_compatible', False) and is_anthropic(model, variant)
    return 'anthropic_messages' if native else 'chat_completions'


def is_low_tier(model):
    identity = model.get('identity') if isinstance(model.get('identity'), dict) else {}
    return model.get('low_tier') is True or identity.get('low_tier_hint') is True


def deployment_signals(catalog, runtime_variants, alternates, visible, prices):
    """Request-independent pool health from the static binding (#6826 review G).

    `no_priced_deployment`: canonical models none of whose deployments has a
    price; `transport_unmeasured`: canonical models whose every variant has a
    contract its deployments' transport cannot meet (e.g. Anthropic served as
    OpenAI-compatible on DIAL); `low_tier_priced`: a priced low-tier deployment
    is bound to a measured variant.
    """
    priced = lambda name: prices.quote(name, Tokens(1, 1))['usd'] is not None
    groups = {}
    for vid, bound in runtime_variants.items():
        variant = catalog['variants'][vid]
        group = groups.setdefault(variant.get('canonical_model') or variant['model'], {'names': [], 'transport_ok': False})
        for model in [bound] + [visible[name] for name in alternates.get(vid, ())]:
            if model['name'] not in group['names']:
                group['names'].append(model['name'])
            contract = variant.get('calibration_contract')
            group['transport_ok'] |= not contract or transport_of(model, variant) == contract['transport']
    return {
        'no_priced_deployment': {canonical: [{'deployment': name, 'remedy': f'add a Costs price alias for {name}'}
                                             for name in group['names']]
                                 for canonical, group in groups.items() if not any(map(priced, group['names']))},
        'transport_unmeasured': sorted(c for c, group in groups.items() if not group['transport_ok']),
        'low_tier_priced': any(is_low_tier(visible[name]) and priced(name)
                               for group in groups.values() for name in group['names'])}


class RoutingAdmissionDenied(Exception):
    """Owner denial must escape the classifier's recoverable ValueError path."""

    def __init__(self, response):
        super().__init__('Classifier admission denied')
        self.response = response


_TOKEN = re.compile(r'[^A-Za-z0-9_.:/@+-]')


def _tok(value, limit=64):
    """One log token: names and codes only, no whitespace, bounded."""
    text = '-' if value is None or value == '' else str(value)
    return _TOKEN.sub('_', text)[:limit] or '-'


def _first_codes(mapping):
    """`vid:CODE,vid:CODE` using each variant's first reason; never free text."""
    return ','.join(f'{_tok(v)}:{_tok((codes or ["-"])[0])}' for v, codes in sorted((mapping or {}).items())) or '-'


def decision_log_line(project_id, request, result=None, refused=None):
    """One INFO line per Auto resolve (#6722): enough to read a decision from pylon_main logs.

    Built from the trace's names and codes only; request and message text never enter it.
    """
    scope = request.get('scope_id') if isinstance(request, dict) else None
    head = f'auto-routing decision project={_tok(project_id)} scope={_tok(scope if isinstance(scope, str) else None)[:8]}'
    if result is None:
        return f'{head} refused={_tok(refused)}'
    trace = result.get('trace') or {}
    classifier, selection = trace.get('classifier') or {}, trace.get('selection') or {}
    descriptor, inventory = trace.get('descriptor') or {}, trace.get('inventory') or {}
    admission = selection.get('admission') or {}
    config = result.get('config') or {}
    cap = config.get('max_tokens')
    deployment = (classifier.get('deployment') or {}).get('name')
    shape = '/'.join(_tok(descriptor.get(k)) for k in ('operation', 'demand', 'relation', 'task_family', 'effort_need'))
    return (f"{head} classifier={_tok(deployment)} schema_valid={classifier.get('schema_valid', '-')} "
            f"error={_tok(classifier.get('error'))} profile_error={_tok(classifier.get('profile_error'))} "
            f"descriptor={shape} needs_context={descriptor.get('needs_context', '-')} "
            f"input_status={_tok(descriptor.get('input_status'))} "
            f"selection={_tok(selection.get('variant') or trace.get('reason'))}/{_tok(selection.get('reason'))}/{_tok(selection.get('tier'))} "
            f"model={_tok(config.get('model_name'))} cap={'default' if cap is None else _tok(cap)} "
            f"admitted={_tok(inventory.get('admitted_variants'))}/{_tok(inventory.get('qualified_variants'))} "
            f"gate_failures={_first_codes(inventory.get('gate_failures'))} "
            f"rejections={_first_codes(admission.get('rejections'))} input_bytes={_tok(inventory.get('input_bytes'))} "
            f"input_tokens_est={_tok(inventory.get('input_tokens_estimate'))} "
            f"bytes_per_token={_tok((inventory.get('input_estimator') or {}).get('bytes_per_token'))}")


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
        """Effective output cap per admitted variant: the explicit cap, else the model's own maximum."""
        return _REQUEST.get().get('output_caps', {})

    @property
    def cap_basis(self):
        return _REQUEST.get().get('cap_basis', 'provider_default')

    @property
    def tier_caps(self):
        """Variants admitted only at provisional tier, with the flags that capped them."""
        return _REQUEST.get().get('tier_caps', {})

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
            ctx = self.local.get()
            if (mode == 'economic' and getattr(self.gateway, 'prices', None) is not None
                    and decision['selection']['reason'] != 'UNCERTAIN_TASK_BASELINE'):
                # The cheapest variant that clears the floor is chosen by expected expenditure.
                decision['selection'] = rank(decision['selection'], messages, self.catalog, session=ctx['session'],
                                             gateway=self.gateway, cap=ctx['cap'], tools=ctx['tools'], previous=previous)
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
    # Optional and authoritative when present (bounded below here, above by each model's own
    # maximum). Absent means the provider default: no ceiling is invented (#6722).
    cap = request.get('output_cap')
    if cap is not None and (type(cap) is not int or cap < 256):
        raise RoutingUnavailable('Auto output allowance must be at least 256 tokens')
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
    alternates = {}
    runtime_variants, exclusions = qualified_inventory(visible, catalog, project_id=project_id, alternates=alternates)
    inventory_revision = fingerprint([model_binding(m) for m in visible.values()])
    output_schema = request.get('output_schema')
    if output_schema is not None and not isinstance(output_schema, dict):
        raise RoutingUnavailable('Unsupported structured-output schema')
    size = len(json.dumps({'messages': messages, 'tools': tools, 'output_schema': output_schema}, ensure_ascii=False).encode()) + 64*len(messages)
    generation_size = request.get('generation_input_bytes', 0)
    if type(generation_size) is not int or not 0 <= generation_size <= 16_000_000:
        raise RoutingUnavailable('Invalid generation input size bound')
    size = max(size, generation_size)  # UTF-8 bytes; byte-based bounds and the trace use this
    input_tokens = estimated_input_tokens(size)  # tokens: context-window gate and price quotes
    prior = request.get('prior_pin')
    restored = None
    if prior:
        restored = decode_pin(prior, signing_key, project_id=project_id, user_id=user_id,
            settings=settings, now=now, scope_id=scope_id, invocation_id=invocation_id, allow_expired=True)
        if restored.get('policy_revision') != router.revision:
            raise RoutingUnavailable('The checkpoint routing policy changed')
        # An omitted cap renews the original run contract (None stays None: the provider
        # default), never a new-turn default. A user-specified cap can narrow it but cannot enlarge it.
        if cap is None:
            cap = restored['config']['max_tokens']
    allowed = []
    output_caps = {}
    tier_caps = {}
    contract_exclusions = {}
    provisional_contracts = {}
    explicit = selection.get('reasoning', {})
    if explicit.get('mode') not in {'auto', 'explicit'}:
        raise RoutingUnavailable('Invalid Auto reasoning selection')
    def gate(vid, variant, contract, variant_cap, model, flags):
        """Failed gates of one deployment, first failure only ([] admits).

        `flags` collects non-blocking provisional-tier flags of an admitted deployment.
        """
        if contract:
            transport = transport_of(model, variant)
            reasons = []
            if transport != contract['transport']:
                # Not a hard gate: an Anthropic deployment behind an OpenAI-compatible
                # transport is admitted at provisional tier, flagged in the trace.
                flags.append('CALIBRATION_TRANSPORT_UNMEASURED')
            if output_schema is not None:
                reasons.append('CALIBRATION_STRUCTURED_OUTPUT_UNMEASURED')
            if explicit['mode'] == 'explicit' and 'reasoning_fields' not in contract:
                reasons.append('CALIBRATION_EXPLICIT_EFFORT_UNMEASURED')
            if variant['effort'] is not None and model.get('supports_reasoning') is False:
                reasons.append('CONFIGURATION_REASONING_DISABLED')
            if reasons:
                # Missing evidence for this configuration is not model
                # incapability or a provider-family ban. Keep it observable.
                contract_exclusions[vid] = {'reasons': reasons, 'deployment': model['name'],
                    'configured_transport': transport, 'measured_transport': contract['transport']}
                return reasons
        if explicit['mode'] == 'explicit' and variant['effort'] != explicit.get('preset'):
            return ['EXPLICIT_EFFORT_MISMATCH']
        # The current native Anthropic SDK uses bounded enabled-thinking for
        # this frozen pool. Its budget must fit inside Auto's total allowance.
        if (is_anthropic(model, variant) and variant['effort'] and not model.get('openai_compatible')
                and not (contract and 'reasoning_fields' in contract)):
            thinking_budget = {'low': 2048, 'medium': 4096, 'high': 9092}[variant['effort']]
            if variant_cap <= thinking_budget:
                return ['THINKING_BUDGET']
        if type(model.get('context_window')) is not int or input_tokens+variant_cap > model['context_window']:
            return ['CONTEXT_WINDOW']
        if type(model.get('max_output_tokens')) is not int or variant_cap > model['max_output_tokens']:
            return ['OUTPUT_LIMIT']
        if prices.quote(model['name'], Tokens(input_tokens, variant_cap))['usd'] is None:
            return ['PRICE']
        if variant.get('cache_write_mode') != 'ordinary_input' and prices.quote(model['name'], Tokens(0, variant_cap, write=input_tokens))['usd'] is None:
            return ['CACHE_WRITE_PRICE']
        return []

    signals = deployment_signals(catalog, runtime_variants, alternates, visible, prices)
    # A renewal keeps its pinned deployment while it is still a ranked candidate
    # and passes the gates, so a later price row cannot flip an in-flight run.
    pinned = (restored['config']['model_name'], restored['config']['model_project_id']) if restored else None
    rebound, rebound_attempts = {}, {}
    gate_failures = {}
    for vid, bound in list(runtime_variants.items()):
        variant = catalog['variants'][vid]
        contract = variant.get('calibration_contract')
        candidates = [bound] + [visible[name] for name in alternates.get(vid, ())]
        candidates.sort(key=lambda m: (m['name'], m['project_id']) != pinned)
        attempts = []
        for model in candidates:
            # The explicit cap is authoritative; without one the provider default applies, which is
            # the deployment's own maximum. The calibration allowance is checked against the cell's
            # observed p90 output once the demand is known (v7/admission.py), not here.
            variant_cap = cap if cap is not None else model.get('max_output_tokens')
            flags = []
            failed = (['OUTPUT_LIMIT'] if type(variant_cap) is not int
                      else gate(vid, variant, contract, variant_cap, model, flags))
            attempts.append({'deployment': model['name'], 'failed': failed})
            if not failed:
                allowed.append(vid)
                output_caps[vid] = variant_cap
                if flags:
                    tier_caps[vid] = {'tier': 'provisional', 'flags': flags}
                    provisional_contracts[vid] = {
                        'flags': flags, 'deployment': model['name'],
                        'configured_transport': transport_of(model, variant), 'measured_transport': contract['transport']}
                if model is not bound:
                    runtime_variants[vid] = model
                    rebound[vid] = {'deployment': model['name'], 'rebound_from': bound['name'],
                                    'reason': 'PINNED_DEPLOYMENT' if (model['name'], model['project_id']) == pinned
                                    else 'BOUND_DEPLOYMENT_UNPRICED'}
                break
            # Only an unpriced deployment falls back to its next ranked duplicate (#6826);
            # any other gate failure stops the variant.
            if failed[0] not in {'PRICE', 'CACHE_WRITE_PRICE'}:
                break
        if vid not in output_caps:
            # Names only: which gate removed this qualified variant (the last deployment tried).
            gate_failures[vid] = list(attempts[-1]['failed'])
        if len(candidates) > 1:
            rebound_attempts[vid] = attempts
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
    if not allowed:
        raise RoutingUnavailable('No qualified model fits the request and pricing contract', 'NO_QUALIFIED_MODELS')
    if prior:
        try:
            validate_binding(restored.get('model_binding'), models, project_id)
        except ValueError as exc:
            raise RoutingUnavailable(str(exc)) from exc
        if restored.get('policy_revision') != router.revision:
            raise RoutingUnavailable('The checkpoint routing policy changed')
        kept = [v for v in allowed if runtime_variants[v]['name'] == restored['config']['model_name']
                and catalog['variants'][v]['effort'] == restored['config']['reasoning_effort']]
        gate_failures.update({v: ['CHECKPOINT_PIN'] for v in allowed if v not in kept})
        allowed = kept
        if not allowed:
            raise RoutingUnavailable('The checkpoint model is no longer admitted')
        if restored.get('invocation_id') == invocation_id:
            # Reauthorize a durable tool-cycle binding, never reclassify its result.
            pinned_cap = restored['config']['max_tokens']
            if cap is not None and pinned_cap is not None and cap > pinned_cap:
                raise RoutingUnavailable('The checkpoint output allowance cannot be enlarged')
            restored['config']['max_tokens'] = cap
            restored['config']['routing_total_output_cap'] = cap is not None
            if 'routing_min_output_cap' in restored['config']:
                if cap is None:
                    del restored['config']['routing_min_output_cap']
                else:
                    restored['config']['routing_min_output_cap'] = cap
            restored['expires_at'] = int(time.time() if now is None else now)+3600
            return {'action': 'generate', 'config': restored['config'], 'pin': encode_pin(restored, signing_key),
                    'invocation_id': invocation_id, 'expires_at': restored['expires_at'],
                    'scope_id': scope_id, 'state_token': request.get('state_token'),
                    'trace': {'reason': 'REAUTHORIZED_CHECKPOINT_PIN'}}
    runtime_context = read_context(request, key=signing_key, project_id=project_id, user_id=user_id, now=now)
    restored_state = restore_state(request.get('state_token'), signing_key, project_id=project_id,
        user_id=user_id, scope_id=scope_id, gate_revision=settings['revision'], policy_revision=router.revision)
    context_token = _REQUEST.set({'complete': complete, 'prices': prices, 'project_id': project_id, 'output_schema': output_schema, 'runtime_context': runtime_context, 'generation_input_bytes': size, 'output_caps': output_caps,
                                  'cap_basis': 'explicit' if cap is not None else 'provider_default', 'tier_caps': tier_caps,
                                  'classifier': {'model': classifier['name'], 'effort': None},
                                  'deployments': {vid: m['name'] for vid, m in runtime_variants.items()}})
    try:
        key = f'{project_id}:{user_id}:{scope_id}'
        with session_for(key, request.get('state_token') if restored_state else None,
                         restored_state['checkpoint'] if restored_state else None) as entry:
            session = entry['session']
            apply_observation(session, restored_state, request.get('observation'), messages,
                              router.gateway, catalog, tools, router.revision)
            previous = restored_state['decision']['selection']['variant'] if restored_state else None
            decision = router.resolve(messages, output_cap=cap, tools=tools, allowed=allowed,
                session=session, previous=previous, access_revision=digest({'gate':settings['revision'],'inventory':inventory_revision,'context':context_revision(runtime_context)}),
                hint={'kind': 'agent_task' if request['surface'] == 'agent' else 'chat_turn'})
            decision['budget']['completion_cap'] = cap
            state_token = encode_pin(state_value(session, decision, messages, scope_id=scope_id,
                project_id=project_id, user_id=user_id, gate_revision=settings['revision'],
                policy_revision=router.revision), signing_key)
            publish(entry, state_token)
    finally:
        _REQUEST.reset(context_token)

    trace = {k: copy.deepcopy(decision.get(k)) for k in ('descriptor', 'policy_revision', 'routing_ms', 'budget')}
    trace['selection'] = {k: copy.deepcopy(decision.get('selection', {}).get(k)) for k in (
        'variant', 'reason', 'family_qualification', 'economics', 'tier', 'provisional', 'admission')}
    if catalog.get('admission') is not None:
        trace['policy'] = {'admission_table_sha256': catalog['admission']['sha256'],
                           'rule_revision': catalog['admission']['rule_revision']}
    classifier_info = decision.get('classifier') or {}
    trace['classifier'] = {k: classifier_info.get(k) for k in ('classifier_variant', 'schema_valid', 'error', 'profile_error', 'trailing_chars') if k in classifier_info}
    trace['classifier']['finish_reason'] = (classifier_info.get('response') or {}).get('finish_reason')
    trace['classifier']['called'] = classifier_info.get('called', bool(classifier_info))
    trace['classifier']['classifier_variant'] = CONFIGURED_CLASSIFIER
    trace['classifier']['deployment'] = {'name': classifier['name'], 'project_id': classifier['project_id'],
                                         'source': ref.get('source')}
    entries = price_snapshot['entries']
    trace['prices'] = {'match': {e['model_name']: e.get('match', 'exact') for e in entries},
                       'aliases': {e['model_name']: e['matched_name'] for e in entries if e.get('match') == 'alias'}}
    trace['instruction_chars'] = (runtime_context.get('active_instructions') or {}).get('total_chars', 0)
    trace['inventory'] = {'revision': inventory_revision, 'discovered': len(visible),
                          'qualified_variants': len(runtime_variants), 'admitted_variants': len(allowed),
                          'excluded': exclusions, 'unmeasured_contracts': contract_exclusions,
                          'rebound': rebound, 'rebound_attempts': rebound_attempts,
                          'unquoted_alternates': list(price_snapshot.get('unquoted', [])),
                          'no_priced_deployment': signals['no_priced_deployment'],
                          'transport_unmeasured': signals['transport_unmeasured'],
                          'provisional_contracts': provisional_contracts, 'gate_failures': gate_failures,
                          'input_bytes': size, 'input_tokens_estimate': input_tokens,
                          'input_estimator': {'name': INPUT_ESTIMATOR, 'bytes_per_token': str(BYTES_PER_TOKEN)},
                          'low_tier_priced': any(is_low_tier(runtime_variants[v]) for v in allowed)}
    if 'uncertainty' in decision:
        trace['uncertainty'] = decision['uncertainty']
    selected = decision['selection']
    # The measured variant was chosen; its bound live deployment is dispatched.
    model = runtime_variants[selected['variant']]
    config = {'model_name': model['name'], 'model_project_id': model['project_id'],
              'reasoning_effort': selected['effort'], 'openai_compatible': model.get('openai_compatible', False),
              'max_output_tokens': model['max_output_tokens'], 'context_window': model['context_window'],
              # None is the provider default; the relay then accepts any value up to max_output_tokens.
              'max_tokens': cap, 'routing_total_output_cap': cap is not None}
    contract = catalog['variants'][selected['variant']].get('calibration_contract')
    if contract:
        unmeasured = selected['variant'] in tier_caps
        if not unmeasured:
            config['routing_transport'] = contract['transport']
            if 'reasoning_fields' in contract:
                config['routing_reasoning_fields'] = copy.deepcopy(contract['reasoning_fields'])
        if cap is not None:
            config['routing_min_output_cap'] = cap
        family = decision['descriptor'].get('task_family')
        trace['calibration_contract'] = {
            'revision': contract['revision'], 'source_sha256': contract['source_sha256'],
            'status': 'provisional_local_beta', 'production_promotion_allowed': False,
            'family': family, 'cell': contract['families'].get(family)}
    pin = {'version': 1, 'project_id': project_id, 'user_id': user_id, 'profile_ref': PROFILE,
           'gate_revision': settings['revision'], 'config': config, 'invocation_id': invocation_id, 'scope_id': scope_id,
           'model_binding': model_binding(model), 'policy_revision': router.revision,
           'expires_at': int(time.time() if now is None else now)+3600}
    return {'action': 'generate', 'config': config, 'pin': encode_pin(pin, signing_key), 'trace': trace,
            'invocation_id': invocation_id, 'scope_id': scope_id, 'state_token': state_token, 'expires_at': pin['expires_at']}
