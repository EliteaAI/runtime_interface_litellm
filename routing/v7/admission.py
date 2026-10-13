"""Data-derived admission table and the chooser that reads it (interim policy, #6722).

``admission-table.json`` is generated offline by ``build_admission_table.py`` from
the reviewed-demand view of the routing-outcome dataset. For every canonical head
(``vendor/family|effort``) and demand it records counts, a lower bound, a paired
adverse count and a tier: ``eligible``, ``provisional`` (deep only) or ``out``.
A head or demand without a row is out: nothing is inferred from names or prices.

The legacy catalog tables (``tasks``, ``max_demand``, ``preferences``) and
``qualification-policy.json`` stay on disk for the lab tests but no longer decide
product routing: only the greeting rule still reads ``tasks``/``preferences``.
"""
import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

TABLE_PATH = Path(__file__).with_name('admission-table.json')
DEMAND = {'simple': 0, 'standard': 1, 'deep': 2}
ADMITTED = frozenset({'eligible', 'provisional'})
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


def _variant_id(canonical, effort):
    return f"{_SHORT.sub('', canonical.split('/', 1)[1])}-{effort or 'default'}"


def missing_variants(admission, existing):
    """Deployment variants for table heads that have an admitted row but no catalog variant yet.

    The identity (exact model id, canonical model, effort) comes from the table's own provenance.
    Anthropic heads carry a transport contract so a non-native deployment is flagged provisional.
    """
    have = {(v['canonical_model'], v['effort']) for v in existing.values()}
    result = {}
    for head, entry in sorted(admission['heads'].items()):
        admitted = [d for d, c in entry['cells'].items() if c['tier'] in ADMITTED]
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
        result[_variant_id(entry['canonical_model'], entry['effort'])] = variant
    return result


def _summary(found):
    return {k: found[k] for k in ('n', 'pass', 'lb90', 'adverse', 'paired_n', 'best_head') if k in found}


def annotate(selection, row, *, demand, family, admission, allowance_rejected=()):
    """Record the tier of the winning row on the selection (also after economics re-ranks)."""
    selection['tier'] = row['tier']
    selection['provisional'] = row['tier'] == 'provisional'
    selection['admission'] = {
        'status': 'admitted', 'demand': demand, 'family': family, 'tier': row['tier'], 'flags': list(row['flags']),
        'cell': copy.deepcopy(row['cell']), 'cell_p90_output': row['expected_output_p90'],
        'effective_cap': row['effective_cap'], 'cap_basis': row['cap_basis'],
        'calibration_allowance': row['cap_basis'], 'allowance_rejected': list(allowance_rejected),
        'rule_revision': admission['rule_revision'], 'table_sha256': admission['sha256']}
    return selection


def choose_admitted(descriptor, allowed, previous, min_demand, catalog, context=None):
    """Cheapest admitted deployment variant for the descriptor's demand (spec sections 2 and 3).

    ``context`` comes from the gateway of the current request: ``tier_caps`` maps a variant to
    ``{'tier': 'provisional', 'flags': [...]}`` (admitted, but its tier is capped), ``caps`` the
    effective output cap per variant (explicit cap, else the model's own maximum) and ``cap_basis``
    which of the two applies.
    """
    admission = catalog['admission']
    context = context or {}
    tier_caps, caps = context.get('tier_caps') or {}, context.get('caps') or {}
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
            reasons.append('outside configured PoC pool')
        if not v['enabled']:
            reasons.append(v.get('disabled_reason', 'disabled'))
        found = cell(admission, variant_head(v), demand, family)
        tier = found['tier'] if found else 'out'
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
    base = {'candidates': rows, 'predicted_output_tokens': None, 'currency_cost_estimate': None,
            'quality_status': catalog['qualification']}
    if not eligible:
        return _baseline(catalog, allowed, rows, base, admission, demand, family, rejected)
    eligible.sort(key=lambda vid: (catalog['variants'][vid]['cost_band'],
                                   EFFORT_ORDER[catalog['variants'][vid]['effort']], vid))
    selected = eligible[0]
    reason = 'UNCERTAIN_CHEAPEST_CLEARING_FLOOR' if uncertain else 'ADMISSION_TABLE_COST_BAND'
    if (not uncertain and previous in eligible
            and catalog['variants'][previous]['cost_band'] == catalog['variants'][selected]['cost_band']):
        selected, reason = previous, 'QUALIFIED_SAME_BAND_STAY'
    result = {'variant': selected, **copy.deepcopy(catalog['variants'][selected]), 'reason': reason, **base}
    return annotate(result, next(r for r in rows if r['variant'] == selected),
                    demand=demand, family=family, admission=admission, allowance_rejected=rejected)


def _baseline(catalog, allowed, rows, base, admission, demand, family, rejected):
    """Nothing clears the floor: the configured baseline family, or an honest refusal."""
    baseline = catalog['variants'][catalog['baseline']]
    usable = [vid for vid, v in catalog['variants'].items()
              if vid in allowed and v['enabled'] and v['model'] == baseline['model']]
    if not usable:
        raise ValueError('No eligible configured model; do not silently escape the pool')
    chosen = (catalog['baseline'] if catalog['baseline'] in usable
              else min(usable, key=lambda vid: (EFFORT_ORDER[catalog['variants'][vid]['effort']], vid)))
    result = {'variant': chosen, **copy.deepcopy(catalog['variants'][chosen]), 'reason': 'UNCERTAIN_TASK_BASELINE',
              **base, 'tier': None, 'provisional': False}
    result['admission'] = {'status': 'no_variant_clears_floor', 'demand': demand, 'family': family, 'tier': None,
                           'flags': [], 'allowance_rejected': rejected,
                           'rule_revision': admission['rule_revision'], 'table_sha256': admission['sha256']}
    return result
