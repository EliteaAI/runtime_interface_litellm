"""Hard gates shared by the legacy and V5 decisions (#6722 V5 D3).

Pure: no I/O, no request context. A deployment-variant is admitted only when
no gate fails. With the default policy the admitted set, its output caps and
the unmeasured-contract report equal the pre-policy inline loop exactly.
"""


def is_anthropic(model, variant):
    identity = model.get('identity')
    if isinstance(identity, dict):
        return identity.get('vendor') == 'anthropic'
    return any(x in variant['model'].lower() for x in ('anthropic', 'claude'))


def in_pool(pool, model, variant):
    """`pool` null admits all; otherwise canonical ids or exact deployment names."""
    if pool is None:
        return True
    identity = model.get('identity') if isinstance(model.get('identity'), dict) else {}
    canonical = identity.get('canonical') or variant.get('canonical_model')
    return model['name'] in pool or (canonical is not None and canonical in pool)


def evaluate(vid, variant, model, *, cap, size, output_schema, explicit, prices, policy):
    """Every gate is evaluated so the trace lists all failures, not the first."""
    from .pricing import Tokens
    contract = variant.get('calibration_contract')
    variant_cap = cap if cap is not None else (
        contract['output_allowance'] if contract else policy['output']['default_allowance'])
    failed, contract_report = [], None
    identity = model.get('identity') if isinstance(model.get('identity'), dict) else {}
    if model.get('available') is False:
        failed.append('AVAILABLE')
    if identity.get('kind', 'chat') != 'chat':
        failed.append('CHAT')
    if not in_pool(policy['pool'], model, variant):
        failed.append('POOL')
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
            # Missing evidence for this configuration is not model
            # incapability or a provider-family ban. Keep it observable.
            contract_report = {'reasons': reasons, 'configured_transport': transport,
                               'measured_transport': contract['transport']}
            failed.append('CONTRACT')
    if explicit['mode'] == 'explicit' and variant['effort'] != explicit.get('preset'):
        failed.append('EXPLICIT_EFFORT')
    # The current native Anthropic SDK uses bounded enabled-thinking for
    # this frozen pool. Its budget must fit inside Auto's total allowance.
    if (is_anthropic(model, variant) and variant['effort'] and not model.get('openai_compatible')
            and not (contract and 'reasoning_fields' in contract)):
        if variant_cap <= policy['thinking_budget_tokens'][variant['effort']]:
            failed.append('THINKING_BUDGET')
    if type(model.get('context_window')) is not int or size+variant_cap > model['context_window']:
        failed.append('CONTEXT_WINDOW')
    if type(model.get('max_output_tokens')) is not int or variant_cap > model['max_output_tokens']:
        failed.append('OUTPUT_CAP')
    if prices.quote(model['name'], Tokens(size, variant_cap))['usd'] is None:
        failed.append('PRICE')
    elif (variant.get('cache_write_mode') != 'ordinary_input'
          and prices.quote(model['name'], Tokens(0, variant_cap, write=size))['usd'] is None):
        failed.append('CACHE_WRITE_PRICE')
    return {'variant': vid, 'deployment': model['name'], 'project_id': model['project_id'],
            'cap': variant_cap, 'failed': failed, 'contract': contract_report}


def admit(runtime_variants, catalog, *, cap, size, output_schema, explicit, prices, policy, enforce_pool=True):
    """Returns (allowed, output_caps, contract_exclusions, report) in inventory order.

    `enforce_pool=False` (shadow mode) still reports POOL failures in `report`
    for the V5 candidate set but does not narrow the executed legacy set."""
    allowed, output_caps, contract_exclusions, report = [], {}, {}, {}
    for vid, model in runtime_variants.items():
        result = evaluate(vid, catalog['variants'][vid], model, cap=cap, size=size, output_schema=output_schema,
                          explicit=explicit, prices=prices, policy=policy)
        report[vid] = result
        if result['contract'] is not None:
            contract_exclusions[vid] = result['contract']
        if not [gate for gate in result['failed'] if enforce_pool or gate != 'POOL']:
            allowed.append(vid)
            output_caps[vid] = result['cap']
    return allowed, output_caps, contract_exclusions, report
