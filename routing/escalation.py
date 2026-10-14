"""Cheap-first routing with escalation on a failure signal, never on a prediction (#6722, plan section 13).

Default (`economical`): the demand floor of the admission table stays, and among the admitted heads the
cheapest by the economics ranking wins at every demand. `premium_at_deep` starts deep cells, and
`premium_from_standard` standard and deep cells, at the cheapest head of the table's top (`eligible`) tier. One signal per resolve moves the scope one step up the ladder
of admitted heads, ordered by the economics ranking; a step changes the canonical MODEL (the cheapest
strictly dearer head of another model), never only the effort of the current one. The level is sticky in the signed state token, so
later turns of the same scope stay there until a further signal raises it.

Names and codes only: no request text is stored in a record, a trace or a log line.
"""
import copy
import re
import time
from decimal import Decimal
from .v7.admission import annotate, EFFORT_ORDER

# Demands at which a premium mode starts at the cheapest head of the top admitted tier.
PREMIUM_DEMANDS = {'premium_at_deep': frozenset({'deep'}), 'premium_from_standard': frozenset({'standard', 'deep'})}
MODES = frozenset({'economical'} | set(PREMIUM_DEMANDS))
DEFAULT_MODE = 'economical'
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
    """`escalation_mode` from Configurations; absent or unknown is the cheap default."""
    mode = settings.get('escalation_mode')
    return mode if mode in MODES else DEFAULT_MODE


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
    last = request.get('last_response')
    if last is not None:
        finish = last.get('finish_reason') if isinstance(last, dict) else None
        if (not isinstance(last, dict) or not (finish is None or isinstance(finish, str) and len(finish) <= 32)
                or any(k in last and type(last[k]) is not bool for k in RESPONSE_FLAGS)):
            raise ValueError('Invalid last response metadata')
        if finish == 'length':
            events.add('incomplete')
        events.update(k for k in RESPONSE_FLAGS if last.get(k))
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


def apply(selection, catalog, *, mode, event, prior):
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
    ladder, price = _ladder(selection, catalog)
    if event and not prior:
        record['reason'] = 'NO_PRIOR_ROUTE'  # nothing to move up from; the first route is the default
        return selection, record, None
    if not ladder:
        record['reason'] = 'ESCALATION_NO_RANKED_HEADS' if event else None
        return selection, record, (carried or None)
    family = {v: catalog['variants'][v].get('canonical_model') or catalog['variants'][v]['model'] for v in ladder}
    rows = {r['variant']: r for r in selection['candidates']}
    start = ladder[0]
    if selection['admission']['demand'] in PREMIUM_DEMANDS.get(mode, ()):
        start = next((v for v in ladder if rows[v]['tier'] == 'eligible'), start)
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
        record['reason'] = 'PREMIUM_START'
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
            'reason': escalation['reason'], 'ts': int(time.time() if now is None else now)}
