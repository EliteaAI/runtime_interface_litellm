"""V5 decision: gated candidates -> selector scores -> cheapest with p >= tau.

#6722 V5 PLAN D3/section 3.3. Pure: no I/O besides the immutable installed
artifact. Candidates are the deployment-variants that passed the hard gates
(`routing/gates.py`); a variant maps to a selector head by canonical identity
and effort (`<vendor/family>|<effort or default>`). The trace carries ids,
scores and costs only, never request text.
"""
import hashlib
import json
import math
from decimal import Decimal
from .pricing import Tokens
from .selector import installed
from .v7.social import match_social

BYTES_PER_TOKEN = 4         # input-token estimate from the bounded request size
GREETING_OUTPUT_TOKENS = 128  # nominal answer size for the greeting cost comparison
EFFORT_ORDER = {None: 0, 'low': 1, 'medium': 2, 'high': 3}


def head_key(model, variant):
    identity = model.get('identity') if isinstance(model.get('identity'), dict) else {}
    canonical = identity.get('canonical') or variant.get('canonical_model')
    return f"{canonical}|{variant['effort'] or 'default'}" if canonical else None


def is_greeting(messages, runtime_context):
    """Exact standalone social turn; agent or system instructions may give a
    short ask a task, so they disable the rule (as the legacy rules do)."""
    if any(m.get('role') == 'system' for m in messages):
        return False
    if ((runtime_context or {}).get('active_instructions') or {}).get('total_chars'):
        return False
    latest = messages[-1]
    return latest.get('role') == 'user' and bool(match_social(latest.get('content')))


def _fallback(ref, allowed, runtime_variants, catalog):
    if not ref:
        return None
    matches = [vid for vid in allowed if runtime_variants[vid]['name'] == ref['name']
               and runtime_variants[vid]['project_id'] == ref['project_id']]
    return min(matches, key=lambda vid: (EFFORT_ORDER.get(catalog['variants'][vid]['effort'], 9), matches.index(vid)),
               default=None)


def _fallback_blocked(ref, report, runtime_variants, catalog, inventory):
    """Why a configured fallback cannot be used: FALLBACK_GATED (visible but a
    hard gate failed) or FALLBACK_UNAVAILABLE (not in the visible inventory)."""
    detail = {'deployment': ref['name'], 'project_id': ref['project_id'], 'gated': []}
    vids = [vid for vid, m in runtime_variants.items()
            if m['name'] == ref['name'] and m['project_id'] == ref['project_id']]
    if vids:
        best = min(vids, key=lambda vid: (len(report[vid]['failed']),
                                          EFFORT_ORDER.get(catalog['variants'][vid]['effort'], 9)))
        return 'FALLBACK_GATED', {**detail, 'variant': best, 'gated': list(report[best]['failed'])}
    model = (inventory or {}).get(ref['name'])
    if model is not None and model['project_id'] == ref['project_id']:
        gate = 'AVAILABLE' if model.get('available') is False else 'NO_CALIBRATED_MODEL_CONTRACT'
        return 'FALLBACK_GATED', {**detail, 'gated': [gate]}
    return 'FALLBACK_UNAVAILABLE', detail


def _most_likely(entries, order):
    """Highest calibrated p_pass; then cheaper; then inventory order."""
    return min(entries, key=lambda e: (-e['p_pass'], Decimal(e['expected_cost_usd']), order[e['variant']]))['variant']


def _cheapest(entries, previous, order):
    """Lowest expected cost; an exact tie keeps the incumbent (cache), then inventory order."""
    return min(entries, key=lambda e: (Decimal(e['expected_cost_usd']), e['variant'] != previous,
                                       order[e['variant']]))['variant']


def decide(*, policy, mode, report, allowed, runtime_variants, catalog, output_caps, prices, messages,
           tools, output_schema, size, runtime_context=None, previous=None, inventory=None, loader=None):
    tau = policy['selector']['tau']
    trace = {'selector_id': policy['selector']['artifact'], 'tau': tau, 'shadow': mode != 'active',
             'abstain': policy['selector']['abstain'], 'noise_band': None, 'p_spread': None,
             'candidates': [], 'chosen': None, 'reason': None}
    selector, selector_error = None, None
    try:
        selector = (loader or installed)(policy['selector']['artifact'])
        trace.update(selector_sha256=selector.sha256, selector_stub=getattr(selector, 'stub', False),
                     noise_band=selector.noise_band)
        if mode == 'active' and getattr(selector, 'stub', False):
            selector, selector_error = None, 'SELECTOR_UNAVAILABLE'  # Never execute an untrained stub.
    except ValueError:
        selector_error = 'SELECTOR_UNAVAILABLE'
    order = {vid: i for i, vid in enumerate(runtime_variants)}
    input_tokens = math.ceil(size / BYTES_PER_TOKEN)
    entries = {}
    for vid, model in runtime_variants.items():
        variant = catalog['variants'][vid]
        key = head_key(model, variant)
        entries[vid] = {'variant': vid, 'deployment': model['name'], 'project_id': model['project_id'],
                        'canonical': key.rsplit('|', 1)[0] if key else None, 'effort': variant['effort'],
                        'gates': list(report[vid]['failed'])}
    incumbent = previous if previous in allowed else None
    chosen, reason = None, None
    if policy['greeting_rule'] and is_greeting(messages, runtime_context):
        priced = []
        for vid in allowed:
            out = min(GREETING_OUTPUT_TOKENS, output_caps[vid])
            usd = prices.quote(runtime_variants[vid]['name'], Tokens(input_tokens, out))['usd']
            entries[vid]['expected_cost_usd'] = usd
            if usd is not None:
                priced.append(entries[vid])
        chosen, reason = (_cheapest(priced, incumbent, order) if priced else None), 'GREETING_RULE'
    elif selector is None:
        reason = selector_error
    else:
        latest = messages[-1].get('content') if messages[-1].get('role') == 'user' else ''
        history = sum(1 for m in messages[:-1] if m.get('role') in {'user', 'assistant'})
        features = selector.features(latest or '', history, len(tools), output_schema is not None, size)
        # Identifies the request's feature vector for offline joins; not reversible to text.
        trace['features_sha256'] = hashlib.sha256(json.dumps(
            sorted((k, round(v, 9)) for k, v in features.items()), separators=(',', ':')).encode()).hexdigest()
        scores = selector.score(features)
        qualified = []
        for vid in allowed:
            entry = entries[vid]
            head = scores.get(head_key(runtime_variants[vid], catalog['variants'][vid]))
            if head is None:
                entry['ineligible'] = 'NO_SELECTOR_HEAD'
                continue
            out = min(head['expected_output_tokens'], output_caps[vid])
            usd = prices.quote(runtime_variants[vid]['name'], Tokens(input_tokens, out))['usd']
            entry.update(p_pass=round(head['p_pass'], 6), expected_output_tokens=out, expected_cost_usd=usd)
            if usd is None:
                entry['ineligible'] = 'PRICE_UNAVAILABLE'
            elif head['p_pass'] < tau:
                entry['ineligible'] = 'BELOW_TAU'
            else:
                qualified.append(entry)
        if len(qualified) >= 2:
            trace['p_spread'] = round(max(e['p_pass'] for e in qualified)-min(e['p_pass'] for e in qualified), 6)
        if (policy['selector']['abstain'] and selector.noise_band > 0 and trace['p_spread'] is not None
                and trace['p_spread'] <= selector.noise_band):
            # The qualified heads are indistinguishable within the fit's noise:
            # "cheapest that qualifies" is not evidence. Use the configured
            # fallback, else the most likely qualified head.
            chosen = _fallback(policy['fallback'], allowed, runtime_variants, catalog)
            reason = 'ABSTAIN_WITHIN_NOISE'
            if chosen is None:
                if policy['fallback']:
                    trace['fallback'] = _fallback_blocked(policy['fallback'], report, runtime_variants,
                                                          catalog, inventory)[1]
                chosen, reason = _most_likely(qualified, order), 'ABSTAIN_HIGHEST_P'
        elif qualified:
            chosen, reason = _cheapest(qualified, incumbent, order), 'SELECTED'
        else:
            reason = 'FALLBACK_NO_QUALIFIED'
    if chosen is None and reason in {'SELECTOR_UNAVAILABLE', 'FALLBACK_NO_QUALIFIED'}:
        chosen = _fallback(policy['fallback'], allowed, runtime_variants, catalog)
        if chosen is None and policy['fallback']:
            reason, trace['fallback'] = _fallback_blocked(policy['fallback'], report, runtime_variants,
                                                          catalog, inventory)
        elif chosen is None and reason == 'FALLBACK_NO_QUALIFIED':
            reason = 'NO_QUALIFIED_MODELS'
    trace['reason'], trace['selector_error'] = reason, selector_error
    if chosen is not None:
        model = runtime_variants[chosen]
        trace['chosen'] = {'variant': chosen, 'deployment': model['name'], 'project_id': model['project_id'],
                           'effort': catalog['variants'][chosen]['effort'], 'incumbent': chosen == incumbent}
    trace['candidates'] = [entries[vid] for vid in runtime_variants]
    return trace
