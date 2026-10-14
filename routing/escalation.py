"""Start head per escalation mode, and escalation on a failure signal, never on a prediction (#6722, plan 13).

The demand floor of the admission table always stays. Modes choose the starting head among the admitted ones:
`tiered` (default): simple cheapest; standard splits per scope between the cheapest head (`standard_low_share`)
and the measured mid tier; deep the measured frontier tier. `economical`: cheapest at every demand.
`premium_at_deep` / `premium_from_standard`: the cheapest head of the table's top (`eligible`) tier at deep
(and standard). One signal per resolve (regenerate, upgrade control, correction, refusal, incomplete, tool failure,
inability) moves the scope one step up the ladder of admitted heads, ordered by the economics ranking; a step
changes the canonical MODEL (the cheapest strictly dearer head of another model), never only the effort. The level
is sticky in the signed state token. The answer guard (routing/guard.py) only records and never moves a turn.

Names and codes only: no request text is stored in a record, a trace or a log line.
"""
import copy
import hashlib
import re
import time
from decimal import Decimal
from .v7.admission import annotate, EFFORT_ORDER

# Demands at which a premium mode starts at the cheapest head of the top admitted tier.
PREMIUM_DEMANDS = {'premium_at_deep': frozenset({'deep'}), 'premium_from_standard': frozenset({'standard', 'deep'})}
MODES = frozenset({'economical', 'tiered'} | set(PREMIUM_DEMANDS))
DEFAULT_MODE = 'tiered'  # plan section 13.5; `economical` stays selectable
# `tiered`: the share of standard scopes that stays on the cheapest head, bounded (plan section 13, user decision).
SHARE_DEFAULT, SHARE_MIN, SHARE_MAX = 0.20, 0.10, 0.25
ARM_BUCKETS = 10_000
# Priority order, strongest first. Only one event fires per resolve.
PRIORITY = ('upgrade_control', 'regenerate', 'correction', 'refusal', 'incomplete', 'tool_failure', 'inability')
PRODUCT_EVENTS = frozenset({'upgrade_control', 'regenerate'})
RESPONSE_FLAGS = ('refusal', 'tool_failure', 'inability')
CORRECTION_WINDOW = 400
_CORRECTION = re.compile(
    r"\b(?:that|this|it)(?:'s| is| was) (?:wrong|incorrect|not (?:right|correct))\b"
    r"|\byour (?:answer|code|result) (?:is|was) (?:wrong|incorrect)\b"
    r"|\byou (?:missed|forgot|skipped|ignored|left out)\b"
    r"|\bas i (?:said|asked|mentioned|requested|told you)\b"
    r"|\bnot what i (?:asked|wanted|meant|requested)\b"
    r"|\b(?:doesn't|does not|didn't|did not) work\b"
    r"|\bstill (?:fails|failing|broken|wrong|not working)\b", re.I)


def mode_of(settings):
    """`escalation_mode` from Configurations: absent is the default (`tiered`), an unknown value is `economical`."""
    mode = settings.get('escalation_mode')
    return DEFAULT_MODE if mode is None else mode if mode in MODES else 'economical'


def share_of(settings):
    """`standard_low_share` for `tiered`, clamped to [0.10, 0.25]; absent or non-numeric is the default."""
    raw = settings.get('standard_low_share')
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw:
        return {'requested': None, 'used': SHARE_DEFAULT, 'clamped': False}
    used = min(max(float(raw), SHARE_MIN), SHARE_MAX)
    return {'requested': raw, 'used': used, 'clamped': used != raw}


def arm_of(scope_id, share):
    """The standard arm of a scope, stable for the whole conversation: `low` (cheapest head) or `mid`."""
    bucket = int(hashlib.sha256(scope_id.encode()).hexdigest(), 16) % ARM_BUCKETS
    return 'low' if bucket < share * ARM_BUCKETS else 'mid'


def _start(mode, demand, ladder, rows, family, tiering):
    """-> (start head, reason code, arm). The head a scope starts on before any signal.

    Tiers are measured, never named: `eligible` is the admission-table tier of the row for this demand and
    family; the frontier is the eligible head(s) with the highest `lb90` (ties: cheapest); mid is an eligible
    head whose canonical model is not that of the cheapest admitted head.
    """
    cheapest = ladder[0]
    if demand in PREMIUM_DEMANDS.get(mode, ()):
        return next((v for v in ladder if rows[v]['tier'] == 'eligible'), cheapest), 'PREMIUM_START', None
    if mode != 'tiered' or demand == 'simple':
        return cheapest, None, None
    eligible = [v for v in ladder if rows[v]['tier'] == 'eligible']
    if demand == 'deep':
        top = max((rows[v]['cell']['lb90'] for v in eligible), default=None)
        return next((v for v in eligible if rows[v]['cell']['lb90'] == top), cheapest), 'TIERED_START', None
    arm = arm_of(tiering['scope_id'], tiering['share']['used'])
    mid = [v for v in eligible if family[v] != family[cheapest]]
    return (mid[0] if arm == 'mid' and mid else cheapest), 'TIERED_START', arm


def response_events(last):
    """Events of the last Auto response metadata `{finish_reason, refusal, tool_failure, inability}`."""
    if last is None:
        return set()
    finish = last.get('finish_reason') if isinstance(last, dict) else None
    if (not isinstance(last, dict) or not (finish is None or isinstance(finish, str) and len(finish) <= 32)
            or any(k in last and type(last[k]) is not bool for k in RESPONSE_FLAGS)):
        raise ValueError('Invalid last response metadata')
    events = {'incomplete'} if finish == 'length' else set()
    events.update(k for k in RESPONSE_FLAGS if last.get(k))
    return events


def request_events(request):
    """Events carried by the SDK fields `escalation` and `last_response`; malformed input is refused."""
    events = set()
    sent = request.get('escalation')
    if sent is not None:
        from_id = sent.get('from_message_id') if isinstance(sent, dict) else None
        if (not isinstance(sent, dict) or sent.get('event') not in PRODUCT_EVENTS
                or not (from_id is None or isinstance(from_id, str) and len(from_id) <= 128)):
            raise ValueError('Invalid escalation signal')
        events.add(sent['event'])
    events.update(response_events(request.get('last_response')))
    return events


def correction_event(messages, descriptor):
    """A follow-up/return turn whose opening words state the previous answer failed."""
    last = messages[-1]
    if last.get('role') != 'user' or (descriptor or {}).get('relation') not in {'followup', 'return'}:
        return set()
    text = last['content'][:CORRECTION_WINDOW].replace('’', "'")
    return {'correction'} if _CORRECTION.search(text) else set()


def _ladder(selection, catalog):
    """Admitted variants of this decision, cheapest first by the economics ranking, with their prices."""
    quotes = (selection.get('economics') or {}).get('quotes') or {}
    price = {vid: Decimal(q['ranking_usd']) for vid, q in quotes.items()}
    order = sorted(price, key=lambda v: (price[v], EFFORT_ORDER[catalog['variants'][v]['effort']], v))
    return order, price


def _switch(selection, catalog, vid):
    """Re-point a ranked selection at another admitted variant, as `economics.rank` would have."""
    row = next(r for r in selection['candidates'] if r['variant'] == vid)
    info = selection['admission']
    result = {**selection, **copy.deepcopy(catalog['variants'][vid]), 'variant': vid}
    result['economics'] = {**selection['economics'],
                           'output_range': [0, selection['economics']['quotes'][vid]['output_allowance']]}
    return annotate(result, row, demand=info['demand'], family=info['family'],
                    admission={'rule_revision': info['rule_revision'], 'sha256': info['table_sha256']},
                    allowance_rejected=info.get('allowance_rejected', ()), rejections=info.get('rejections'))


def _chain(ladder, price, family, start):
    """Heads reached from `start` by repeated escalation: each step is the cheapest admitted head of
    another canonical model that is strictly more expensive. Effort variants of a model are skipped."""
    chain = [start]
    while True:
        here = chain[-1]
        up = next((v for v in ladder if family[v] != family[here] and price[v] > price[here]), None)
        if up is None:
            return chain
        chain.append(up)


def _next(selection, catalog, here):
    """The head a guard failure would move `here` to: the cross-model step, or None at the ceiling."""
    ladder, price = _ladder(selection, catalog)
    if 'admission' not in selection or here not in price:
        return None
    family = {v: catalog['variants'][v].get('canonical_model') or catalog['variants'][v]['model'] for v in ladder}
    return next((v for v in ladder if family[v] != family[here] and price[v] > price[here]), None)


def apply(selection, catalog, *, mode, event, prior, tiering=None):
    """-> (selection, record, sticky); `record['next']` is the head a failed guard would escalate to."""
    selection, record, sticky = _apply(selection, catalog, mode=mode, event=event, prior=prior, tiering=tiering)
    record['next'] = _next(selection, catalog, record['to'])
    return selection, record, sticky


def _apply(selection, catalog, *, mode, event, prior, tiering):
    """-> (selection, record, sticky).

    `prior` is the signed state of the scope's previous turn (None for the first); `event` the one
    event that fired, or None. `record` is the trace entry; `sticky` goes into the next state token.
    """
    carried = copy.deepcopy((prior or {}).get('escalation') or {})
    level = carried.get('level', 0)
    previous = prior['decision']['selection']['variant'] if prior else None
    chosen = selection['variant']
    record = {'mode': mode, 'event': event or 'none', 'from': previous, 'to': chosen, 'level': level, 'reason': None,
              'ladder': []}
    if mode == 'tiered':
        record.update(arm=None, standard_low_share=tiering['share'])
    ladder, price = _ladder(selection, catalog)
    if event and not prior:
        record['reason'] = 'NO_PRIOR_ROUTE'  # nothing to move up from; the first route is the default
        return selection, record, None
    if not ladder or 'admission' not in selection:  # greeting rule or no ranking: nothing to climb
        record['reason'] = 'ESCALATION_NO_RANKED_HEADS' if event else None
        return selection, record, (carried or None)
    family = {v: catalog['variants'][v].get('canonical_model') or catalog['variants'][v]['model'] for v in ladder}
    rows = {r['variant']: r for r in selection['candidates']}
    start, start_reason, arm = _start(mode, selection['admission']['demand'], ladder, rows, family, tiering)
    if mode == 'tiered':
        record['arm'] = arm
    chain = _chain(ladder, price, family, start)
    record['ladder'] = chain
    target = chain[min(level, len(chain) - 1)]
    sticky = carried or None
    if event:
        here = previous if previous in price else target
        up = next((v for v in ladder if family[v] != family[here] and price[v] > price[here]), None)
        target = here if up is None else up
        level = level if up is None else level + 1
        record.update(level=level, reason='ESCALATION_CEILING' if up is None else 'ESCALATED')
        sticky = {'event': event, 'from': previous, 'level': level} if level else None
    elif level:
        record['reason'] = 'STICKY'
    elif start != ladder[0]:
        record['reason'] = start_reason
    if record['reason'] is not None and target != chosen:
        selection = _switch(selection, catalog, target)
        record['to'] = selection['variant']
    if sticky:
        sticky['to'] = record['to']
    return selection, record, sticky


def usage_record(project_id, request, result, now=None):
    """The section 13.2 record of one Auto resolve: names and codes only, never request text."""
    trace = result.get('trace') or {}
    escalation = trace.get('escalation')
    if escalation is None:
        return None
    admission = (trace.get('selection') or {}).get('admission') or {}
    return {'kind': 'auto_routing_escalation', 'scope_id': request.get('scope_id'), 'project_id': project_id,
            'demand': admission.get('demand'), 'family': admission.get('family'),
            'head': (trace.get('selection') or {}).get('variant'), 'model': (result.get('config') or {}).get('model_name'),
            'escalation_event': escalation['event'], 'escalation_from': escalation['from'],
            'escalation_to': escalation['to'], 'level': escalation['level'], 'mode': escalation['mode'],
            'reason': escalation['reason'], 'arm': escalation.get('arm'),
            'ts': int(time.time() if now is None else now)}
