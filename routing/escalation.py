"""Start head per escalation mode, and escalation on a failure signal, never on a prediction (#6722, plan 13).

The demand floor of the admission table always stays. Modes choose the starting head among the admitted ones:
`tiered` (default): simple cheapest; standard splits per scope between the cheapest head (`standard_low_share`)
and the measured mid tier; deep the premium head below, else the cheapest (`TIERED_FRONTIER_UNCONFIRMED`).
`economical`: cheapest at every demand. Absent or unknown modes are `tiered` (unknown traced `MODE_UNKNOWN`).
An uncertain descriptor starts on the cheapest admitted head in every mode.

`premium_at_deep` / `premium_from_standard` (deep, and from standard up): the premium head is the admitted head
with the highest `lb90` among cells that are `cross_provider_confirmed` and `label_regime: panel` (a single-judge
cell never starts premium; `marginal` is trace only). `eligible` ranks before `provisional`; an exact `lb90` tie (full
precision, from the cell counts) goes to the lower paired adverse rate, then the cheaper list price (`ladder_order` when
no list prices are given), then the canonical head order; the deciding step
is traced as `premium_tiebreak`. The reviewer's head-to-head step (fewer losses on shared items) needs item outcomes,
which the table does not carry, so it cannot decide here. With no such cell at the demand, premium is inactive:
the scope starts as `tiered` would and the trace records `PREMIUM_INACTIVE` (Anthropic-only installations today).
Premium costs about 13x per success at deep for +2.5 pp [+0.1, +5.4] pass on the held-out deep items (REVIEW-R81
section X(b)), so a project chooses it with that number in view.

One signal per resolve (regenerate, upgrade control, correction, refusal, incomplete, tool failure, inability)
moves the scope one step up the ladder of admitted heads, ordered by the economics ranking; a step changes the
canonical MODEL (the cheapest strictly dearer head of another model), never only the effort. The level is sticky
in the signed state token. The answer guard (routing/guard.py) only records and never moves a turn.

Names and codes only: no request text is stored in a record, a trace or a log line.
"""
import copy
import hashlib
import re
import time
from decimal import Decimal
from .v7.admission import annotate, variant_head, ADMITTED, EFFORT_ORDER
from .v7.build_admission_table import jeffreys_lower

# Demands at which a premium mode starts on the premium head (`_premium`); `tiered` uses it at deep.
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
    """`escalation_mode` from Configurations: absent or unknown is the default (`tiered`); see `mode_code`."""
    mode = settings.get('escalation_mode')
    return mode if mode in MODES else DEFAULT_MODE


def mode_code(settings):
    """`MODE_UNKNOWN` when a value is set but is not a mode (Configurations never serves one), else None."""
    mode = settings.get('escalation_mode')
    return 'MODE_UNKNOWN' if mode is not None and mode not in MODES else None


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


def _lb90(cell):
    """`lb90` at full precision from the cell counts (the table stores it rounded to 6 places). A cell whose
    counts do not reproduce its stored bound (none in the shipped table; pinned by a test) keeps the stored one."""
    if 'pass' not in cell or 'n' not in cell:
        return cell['lb90']
    exact = jeffreys_lower(cell['pass'], cell['n'])
    return exact if round(exact, 6) == cell['lb90'] else cell['lb90']


def _adverse_rate(cell):
    return cell['adverse'] / cell['paired_n'] if cell.get('paired_n') else 0.0


_UNPRICED = Decimal('Infinity')


def _rank(pool, ladder, rows, list_price, head_of):
    """-> (head, deciding step) of `pool`: `eligible` before `provisional`, then highest `lb90`, exact tie -> lower
    paired adverse rate -> cheaper list price (an unpriced head last; without `list_price` the ladder order,
    traced `ladder_order`) -> canonical head order."""
    if not pool:
        return None, None
    if len(pool) == 1:
        return pool[0], 'lb90'  # a single candidate: no tie to break (AI.3 convention)
    if list_price is not None:
        price = ('list_price', lambda v: _UNPRICED if list_price.get(v) is None else list_price[v])
    else:
        price = ('ladder_order', {v: i for i, v in enumerate(ladder)}.__getitem__)
    steps = (('tier', lambda v: rows[v]['tier'] != 'eligible'),
             ('lb90', lambda v: -_lb90(rows[v]['cell'])),
             ('paired_adverse_rate', lambda v: _adverse_rate(rows[v]['cell'])),
             price,
             ('canonical_order', lambda v: (head_of or {}).get(v, v)))
    for step, key in steps:
        best = min(key(v) for v in pool)
        pool = [v for v in pool if key(v) == best]
        if len(pool) == 1:
            return pool[0], step
    return pool[0], 'canonical_order'


def _premium(ladder, rows, list_price=None, head_of=None):
    """-> (premium head, deciding step), or (None, None) when no admitted (eligible, provisional) cell is
    cross-provider-confirmed and panel-labelled. A `floor_only` row is never in the pool."""
    pool = [v for v in ladder if rows[v]['tier'] in ADMITTED
            and (rows[v].get('cell') or {}).get('cross_provider_confirmed') is True
            and rows[v]['cell'].get('label_regime') == 'panel']
    return _rank(pool, ladder, rows, list_price, head_of)


_COMPUTE = object()


def _needs_premium(mode, demand):
    """Premium modes at their demands, and `tiered` at deep: both start on the `_premium` head (AJ.2)."""
    return demand in PREMIUM_DEMANDS.get(mode, ()) or (mode == 'tiered' and demand == 'deep')


def _start(mode, demand, ladder, rows, family, tiering, list_price=None, head_of=None, premium=_COMPUTE,
           uncertain=False):
    """-> (start head, reason code, arm). The head a scope starts on before any signal.

    Tiers are measured, never named. An uncertain descriptor (any `UNCERTAIN_*` chooser reason) starts on the
    cheapest admitted head in every mode (AA condition 1). Premium modes at their demands and `tiered` at deep
    start on the `_premium` head (confirmed, panel-labelled); without one, a premium mode starts as `tiered`
    (`PREMIUM_INACTIVE`) and `tiered` deep stays on the cheapest head (`TIERED_FRONTIER_UNCONFIRMED`). The
    standard mid tier is an eligible head whose canonical model is not that of the cheapest admitted head,
    regardless of confirmation: it is not a premium posture. `premium` is the head when the caller computed it.
    """
    cheapest = ladder[0]
    if uncertain:
        return cheapest, None, None
    if premium is _COMPUTE:
        premium = _premium(ladder, rows, list_price, head_of)[0] if _needs_premium(mode, demand) else None
    inactive = False
    if demand in PREMIUM_DEMANDS.get(mode, ()):
        if premium:
            return premium, 'PREMIUM_START', None
        mode, inactive = 'tiered', True  # no confirmed panel cell: start as `tiered` for this demand
    start, reason, arm = _tiered_start(mode, demand, ladder, rows, family, tiering, premium)
    return start, ('PREMIUM_INACTIVE' if inactive else reason), arm


def _tiered_start(mode, demand, ladder, rows, family, tiering, premium=None):
    cheapest = ladder[0]
    if mode != 'tiered' or demand == 'simple':
        return cheapest, None, None
    if demand == 'deep':
        return (premium, 'TIERED_START', None) if premium else (cheapest, 'TIERED_FRONTIER_UNCONFIRMED', None)
    eligible = [v for v in ladder if rows[v]['tier'] == 'eligible']
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


def apply(selection, catalog, *, mode, event, prior, tiering=None, list_price=None):
    """-> (selection, record, sticky); `record['next']` is the head a failed guard would escalate to.

    `list_price` maps a variant to its list price, the premium tie-break before the canonical order."""
    selection, record, sticky = _apply(selection, catalog, mode=mode, event=event, prior=prior, tiering=tiering,
                                       list_price=list_price)
    record['next'] = _next(selection, catalog, record['to'])
    return selection, record, sticky


def _apply(selection, catalog, *, mode, event, prior, tiering, list_price=None):
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
    demand = (selection.get('admission') or {}).get('demand')
    premium_demand = demand in PREMIUM_DEMANDS.get(mode, ())
    inactive = {'head': None, 'premium_tiebreak': None, 'code': 'PREMIUM_INACTIVE'}
    if not ladder or 'admission' not in selection:  # greeting rule or no ranking: nothing to climb
        if premium_demand:
            record['premium'] = inactive  # the chooser ran and admitted nothing at a premium demand
        record['reason'] = 'ESCALATION_NO_RANKED_HEADS' if event else None
        return selection, record, (carried or None)
    family = {v: catalog['variants'][v].get('canonical_model') or catalog['variants'][v]['model'] for v in ladder}
    head_of = {v: variant_head(catalog['variants'][v]) for v in ladder}
    rows = {r['variant']: r for r in selection['candidates']}
    # Any UNCERTAIN_* chooser reason: frontier and premium starts apply to a confident label only (AA.1).
    uncertain = str(selection.get('reason') or '').startswith('UNCERTAIN_')
    premium = None
    if _needs_premium(mode, demand):
        if uncertain:
            record['premium'] = {'head': None, 'premium_tiebreak': None, 'code': 'UNCERTAIN_DESCRIPTOR'}
        else:
            premium, step = _premium(ladder, rows, list_price, head_of)
            unset = 'PREMIUM_INACTIVE' if premium_demand else 'TIERED_FRONTIER_UNCONFIRMED'
            record['premium'] = {'head': premium, 'premium_tiebreak': step, 'code': None if premium else unset}
    start, start_reason, arm = _start(mode, demand, ladder, rows, family, tiering, list_price, head_of, premium,
                                      uncertain)
    if mode == 'tiered' or arm:
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
    elif start != ladder[0] or start_reason in {'PREMIUM_INACTIVE', 'TIERED_FRONTIER_UNCONFIRMED'}:
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
