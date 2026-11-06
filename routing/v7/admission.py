"""Data-derived admission table and the chooser that reads it (interim policy, #6722).

The table of record (``TABLE_PATH``, round vii, #6826) is built offline from the
reviewed-demand view of the routing-outcome dataset (``build_admission_table.py`` plus
the ac2/ref30/limit_oracle rules of autorouting_impl/admission-rebuild-iv);
``admission-table.json`` (material-r2) stays beside it as history. For every canonical head
(``vendor/family|effort``) and demand it records counts, a lower bound, a paired
adverse count and a tier: ``eligible``, ``provisional`` or ``out``. An ``out`` cell with
``status: floor_only`` cleared the absolute floor but not the paired rule: it is a candidate
only when the project chose the ``economical`` mode (``context['floor_only']``), never by
default. A head or demand without a row is out: nothing is inferred from names or prices.

The legacy catalog tables (``tasks``, ``max_demand``, ``preferences``) and
``qualification-policy.json`` stay on disk for the lab tests but no longer decide
product routing: only the greeting rule still reads ``tasks``/``preferences``.

Unmeasured installations (#7307): when no variant clears the floor and the baseline
family is unusable, Auto routes to the cheapest variant that passed every hard gate
(availability, context window, price, effort and pool filters) but holds no admitting
row: a table head that is out at this demand, or an unmeasured deployment the table
does not know. The reason is ``UNMEASURED_CHEAPEST``, the selection is provisional and
flagged ``guard_sample`` so the record-only guard samples every such answer. It is a
last resort only: never chosen while any variant clears the floor or the baseline is
usable. Auto refuses only when nothing passed the hard gates.
"""
import copy
import hashlib
import json
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

TABLE_PATH = Path(__file__).with_name('admission-table-material-r2-vii-3of4-ref30-limit-ai.json')
DEMAND = {'simple': 0, 'standard': 1, 'deep': 2}
ADMITTED = frozenset({'eligible', 'provisional'})
# Evidence verdicts of the table; the unmeasured last resort sets only these aside.
EVIDENCE_REJECTIONS = frozenset({'ADMISSION_NO_ROW', 'ADMISSION_ROW_OUT', 'CALIBRATION_ALLOWANCE_BELOW_P90'})
FLOOR_ONLY = 'floor_only'
EFFORT_ORDER = {None: 0, 'low': 1, 'medium': 2, 'high': 3}
TRANSPORT_BY_VENDOR = {'anthropic': 'anthropic_messages'}
_SHORT = re.compile(r'^(claude-|gpt-\d+-\d+-)')


@lru_cache(maxsize=1)
def load():
    """Parse the shipped table once; the hash covers the exact bytes and so the whole policy."""
    raw = TABLE_PATH.read_bytes()
    table = json.loads(raw)
    return {'rule_revision': table['rule_revision'], 'sha256': hashlib.sha256(raw).hexdigest(),
            'dataset_sha256': table['dataset_sha256'], 'heads': table['heads']}


def head_key(canonical_model, effort):
    return f"{canonical_model}|{effort or 'default'}"


def variant_head(variant):
    return head_key(variant['canonical_model'], variant['effort'])


def cell(admission, head, demand, family=None):
    """Family refinement where the table has one (n >= 20, lineages >= 8); else the pooled row."""
    entry = admission['heads'].get(head)
    if not entry:
        return None
    if family:
        refined = (entry.get('families') or {}).get(family, {}).get(demand)
        if refined:
            return refined
    return entry['cells'].get(demand)


def tier_of(admission, head, demand, family=None):
    found = cell(admission, head, demand, family)
    return found['tier'] if found else 'out'


def _variant_id(canonical, effort, taken=()):
    """The short id (``sol-default``), or the full family id when the short one is taken by another model
    (gpt-6.1-sol and gpt-5.6-sol both shorten to ``sol``)."""
    family = canonical.split('/', 1)[1]
    short = f"{_SHORT.sub('', family)}-{effort or 'default'}"
    if short not in taken:
        return short
    full = f"{family}-{effort or 'default'}"
    if full in taken:
        raise ValueError(f'Admission head {canonical} has no free variant id')
    return full


def missing_variants(admission, existing):
    """Deployment variants for table heads that have an admitted row but no catalog variant yet.

    The identity (exact model id, canonical model, effort) comes from the table's own provenance.
    Anthropic heads carry a transport contract so a non-native deployment is flagged provisional.
    """
    have = {(v['canonical_model'], v['effort']) for v in existing.values()}
    result = {}
    for head, entry in sorted(admission['heads'].items()):
        admitted = [d for d, c in entry['cells'].items() if c['tier'] in ADMITTED or c.get('status') == FLOOR_ONLY]
        if not admitted or (entry['canonical_model'], entry['effort']) in have or not entry['model_ids']:
            continue
        vendor = entry['canonical_model'].split('/', 1)[0]
        variant = {'model': entry['model_ids'][0], 'canonical_model': entry['canonical_model'],
                   'effort': entry['effort'], 'enabled': True,
                   'tasks': ['creative', 'transform', 'analysis', 'design'],
                   'max_demand': max(admitted, key=DEMAND.get), 'cost_band': 2, 'cache_write_mode': 'separate'}
        if vendor in TRANSPORT_BY_VENDOR:
            variant['calibration_contract'] = {
                'revision': admission['rule_revision'], 'source_sha256': admission['dataset_sha256'],
                'families': {}, 'transport': TRANSPORT_BY_VENDOR[vendor], 'output_allowance': None,
                'production_promotion_allowed': False}
        result[_variant_id(entry['canonical_model'], entry['effort'], set(existing) | set(result))] = variant
    return result


def _summary(found):
    return {k: found[k] for k in ('n', 'pass', 'lb90', 'adverse', 'paired_n', 'best_head', 'status', 'reference_head',
                                  'cross_provider_confirmed', 'label_regime', 'marginal') if k in found}


def annotate(selection, row, *, demand, family, admission, allowance_rejected=(), rejections=None):
    """Record the tier of the winning row on the selection (also after economics re-ranks)."""
    selection['tier'] = row['tier']
    selection['provisional'] = row['tier'] == 'provisional'
    selection['admission'] = {
        'status': 'admitted', 'demand': demand, 'family': family, 'tier': row['tier'], 'flags': list(row['flags']),
        'cell': copy.deepcopy(row['cell']), 'cell_p90_output': row['expected_output_p90'],
        'effective_cap': row['effective_cap'], 'cap_basis': row['cap_basis'],
        'calibration_allowance': row['cap_basis'], 'allowance_rejected': list(allowance_rejected),
        'rejections': copy.deepcopy(rejections or {}),
        'rule_revision': admission['rule_revision'], 'table_sha256': admission['sha256']}
    return selection


def choose_admitted(descriptor, allowed, previous, min_demand, catalog, context=None):
    """Cheapest admitted deployment variant for the descriptor's demand (spec sections 2 and 3).

    ``context`` comes from the gateway of the current request: ``tier_caps`` maps a variant to
    ``{'tier': 'provisional', 'flags': [...]}`` (admitted, but its tier is capped), ``caps`` the
    effective output cap per variant (explicit cap, else the model's own maximum) and ``cap_basis``
    which of the two applies. ``last_resort_quotes`` orders the unmeasured last resort
    (``{'tokens': {...}, 'usd': {variant: str | None}}``).
    """
    admission = catalog['admission']
    context = context or {}
    tier_caps, caps = context.get('tier_caps') or {}, context.get('caps') or {}
    pre_rejected = context.get('pre_rejected') or {}
    basis = context.get('cap_basis') or 'provider_default'
    allowed = set(allowed) if allowed is not None else set(catalog['variants'])
    family = descriptor.get('task_family')
    uncertain = (descriptor['needs_context'] or descriptor['relation'] == 'ambiguous'
                 or descriptor['operation'] == 'other' or family == 'unknown')
    demand = max(descriptor['demand'], min_demand, key=DEMAND.get)
    if uncertain:
        demand = 'deep'
    family = None if family in (None, 'unknown') else family
    rows = []
    for vid, v in catalog['variants'].items():
        reasons, flags = [], []
        if vid not in allowed:
            # Removed before the chooser by the effort filter or the qualification snapshot, else
            # not admitted by the preflight gates (see trace.inventory.gate_failures).
            reasons.append(pre_rejected.get(vid, 'outside configured PoC pool'))
        if not v['enabled']:
            reasons.append(v.get('disabled_reason', 'disabled'))
        found = cell(admission, variant_head(v), demand, family)
        tier = found['tier'] if found else 'out'
        if tier == 'out' and found and found.get('status') == FLOOR_ONLY and context.get('floor_only'):
            tier = FLOOR_ONLY  # the project chose `economical`: the measured gap is accepted
        if tier == 'out':
            reasons.append('ADMISSION_NO_ROW' if found is None else 'ADMISSION_ROW_OUT')
        capped = tier_caps.get(vid)
        if capped:
            flags = list(capped.get('flags', []))
            if tier == 'eligible':
                tier = capped.get('tier', 'provisional')
        p90 = found.get('expected_output_tokens_p90') if found else None
        effective = caps.get(vid)
        if tier != 'out' and effective is not None and p90 is not None and effective < p90:
            reasons.append('CALIBRATION_ALLOWANCE_BELOW_P90')
            flags.append('CALIBRATION_ALLOWANCE_BELOW_P90')
        rows.append({'variant': vid, 'eligible': not reasons, 'rejections': reasons, 'cost_band': v['cost_band'],
                     'tier': tier, 'flags': flags, 'cell': _summary(found) if found else None,
                     'expected_output_p90': p90, 'effective_cap': effective, 'cap_basis': basis})
    eligible = [r['variant'] for r in rows if r['eligible']]
    rejected = [r['variant'] for r in rows if 'CALIBRATION_ALLOWANCE_BELOW_P90' in r['rejections']]
    rejections = {r['variant']: list(r['rejections']) for r in rows if r['rejections']}
    base = {'candidates': rows, 'predicted_output_tokens': None, 'currency_cost_estimate': None,
            'quality_status': catalog['qualification']}
    if not eligible:
        return _baseline(catalog, allowed, rows, base, admission, demand, family, rejected, rejections,
                         context.get('last_resort_quotes') or {})
    eligible.sort(key=lambda vid: (catalog['variants'][vid]['cost_band'],
                                   EFFORT_ORDER[catalog['variants'][vid]['effort']], vid))
    selected = eligible[0]
    reason = 'UNCERTAIN_CHEAPEST_CLEARING_FLOOR' if uncertain else 'ADMISSION_TABLE_COST_BAND'
    if (not uncertain and previous in eligible
            and catalog['variants'][previous]['cost_band'] == catalog['variants'][selected]['cost_band']):
        selected, reason = previous, 'QUALIFIED_SAME_BAND_STAY'
    result = {'variant': selected, **copy.deepcopy(catalog['variants'][selected]), 'reason': reason, **base}
    return annotate(result, next(r for r in rows if r['variant'] == selected),
                    demand=demand, family=family, admission=admission, allowance_rejected=rejected,
                    rejections=rejections)


class NoEligibleModel(ValueError):
    """Nothing clears the floor, the baseline family is unusable and no hard-gated variant is left.

    Carries the baseline model name so the refusal can tell an admin what to add (#7307).
    """

    def __init__(self, baseline):
        super().__init__('No eligible configured model; do not silently escape the pool')
        self.baseline = baseline


def _baseline(catalog, allowed, rows, base, admission, demand, family, rejected, rejections, quotes):
    """Nothing clears the floor: the configured baseline family, else the unmeasured last resort."""
    baseline = catalog['variants'][catalog['baseline']]
    usable = [vid for vid, v in catalog['variants'].items()
              if vid in allowed and v['enabled'] and not v.get('unmeasured') and v['model'] == baseline['model']]
    if not usable:
        return _unmeasured_cheapest(catalog, rows, base, admission, demand, family, rejected, rejections, quotes,
                                    baseline['model'])
    chosen = (catalog['baseline'] if catalog['baseline'] in usable
              else min(usable, key=lambda vid: (EFFORT_ORDER[catalog['variants'][vid]['effort']], vid)))
    result = {'variant': chosen, **copy.deepcopy(catalog['variants'][chosen]), 'reason': 'UNCERTAIN_TASK_BASELINE',
              **base, 'tier': None, 'provisional': False}
    result['admission'] = {'status': 'no_variant_clears_floor', 'demand': demand, 'family': family, 'tier': None,
                           'flags': [], 'allowance_rejected': rejected, 'rejections': rejections,
                           'rule_revision': admission['rule_revision'], 'table_sha256': admission['sha256']}
    return result


def _unmeasured_cheapest(catalog, rows, base, admission, demand, family, rejected, rejections, quotes, baseline):
    """The cheapest variant held back only by the table's evidence verdicts, or an honest refusal.

    The pool is extended by hard-gated heads only, never silently escaped: a variant outside the
    preflight pool, removed by the effort or qualification filters, or disabled is never a candidate.
    Ordered by the fixed-request quote (unpriced last), a measured head before an unmeasured one at
    an equal price, then effort and id.
    """
    usd = quotes.get('usd') or {}
    candidates = [r['variant'] for r in rows if r['rejections'] and set(r['rejections']) <= EVIDENCE_REJECTIONS]
    if not candidates:
        raise NoEligibleModel(baseline)

    def key(vid):
        quote, variant = usd.get(vid), catalog['variants'][vid]
        return (quote is None, Decimal(quote) if quote is not None else Decimal(0), bool(variant.get('unmeasured')),
                EFFORT_ORDER[variant['effort']], vid)
    chosen = min(candidates, key=key)
    result = {'variant': chosen, **copy.deepcopy(catalog['variants'][chosen]), 'reason': 'UNMEASURED_CHEAPEST',
              **base, 'tier': None, 'provisional': True, 'guard_sample': True}
    flags = next(list(r['flags']) for r in rows if r['variant'] == chosen)
    result['admission'] = {'status': 'unmeasured_installation', 'demand': demand, 'family': family, 'tier': None,
                           'flags': flags, 'allowance_rejected': rejected, 'rejections': rejections,
                           'last_resort': {'quote_tokens': copy.deepcopy(quotes.get('tokens')),
                                           'quotes_usd': {vid: usd.get(vid) for vid in sorted(candidates)}},
                           'rule_revision': admission['rule_revision'], 'table_sha256': admission['sha256']}
    return result
