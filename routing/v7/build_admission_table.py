#!/usr/bin/env python3
"""Offline builder of ``admission-table.json`` (interim legacy policy, #6722/#6826).

Stdlib only. Input is the R81 routing-outcome dataset (a JSONL file that is NOT
committed); output is a counts-and-rates table with provenance. The Gateway
loads only the JSON.

Rule (REVIEW-R81 section I, "reviewed-demand view"):

* keep rows with ``outcome != 'unknown'`` and a model;
* cell demand = ``reviewed_demand`` when present, else ``authored_difficulty``
  only for the waves authored with request review (``AUTHORED_WAVES``); a
  template-assigned difficulty is never used;
* rows of router-chosen arms (``ROUTED_ARMS``) are excluded: the model varies per
  item by policy, so they are not a measurement of one fixed head;
* pass = ``outcome == 'pass'`` (refusal and incomplete count as non-pass);
* arms are merged by canonical head ``vendor/family|effort`` (see
  ``canonical_model``/``head_key``, mirrored from the lab's ``selector-v5/common.py``);
* ``lb90`` = one-sided 90% Jeffreys lower bound, Beta(pass+1/2, fail+1/2) 10th percentile;
* ``adverse`` = items on which the head did not pass while the best head passed,
  over the common ``(wave, task_id)`` items; the best head is the one with the
  highest point rate and ``n >= MIN_BEST_N``, ties broken by larger ``n`` and
  then by first appearance in the dataset (the dataset is hash-pinned, so the
  order is part of the provenance);
* tiers: simple/standard eligible iff ``lb90 >= 0.90`` and adverse rate ``<= 0.05``;
  deep eligible iff ``lb90 >= 0.85`` and adverse rate ``<= 0.05``, provisional iff
  ``lb90 >= 0.85`` and adverse rate ``<= 0.08`` with at most 3 adverse items.
  With no common item with the best head there is no paired evidence of
  regression; the cell is judged on ``lb90`` alone and ``paired_n`` is 0.
* family refinement only where ``n >= 20`` and distinct lineages ``>= 8``
  (lineage = ``lineage`` field, else ``task_id``); the best head is taken inside
  the family cell. Otherwise the pooled row applies.

``expected_output_tokens_p90`` is the nearest-rank 90th percentile of the
dataset row field ``usage.output`` over the kept rows of the cell.
"""
import argparse
import gzip
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

RULE_REVISION = 'interim-reviewed-demand-1'
DEMANDS = ('simple', 'standard', 'deep')
AUTHORED_WAVES = frozenset({'v14-r77', 'v14-r79', 'v14-r80'})
ROUTED_ARMS = frozenset({('v14-r53', 'candidate'), ('v14-r53', 'retained')})
FLOOR = {'simple': 0.90, 'standard': 0.90, 'deep': 0.85}
ADVERSE_ELIGIBLE = 0.05
ADVERSE_PROVISIONAL = 0.08
PROVISIONAL_MAX_ADVERSE_ITEMS = 3
MIN_BEST_N = 20
FAMILY_MIN_N = 20
FAMILY_MIN_LINEAGES = 8
LB_CONFIDENCE = 0.90
OUTPUT_PERCENTILE = 0.90
OUTPUT_FIELD = 'usage.output'

# --- canonical identity (mirror of selector-v5/common.py, chat models only) -------------------------------
_REGION = re.compile(r'^(global|eu|us|apac|ca|au|jp)\.')
_VENDOR_DOT = re.compile(r'^(openai|anthropic|amazon|meta|google|gemini|qwen|zai|xai|nvidia|deepseek|minimax|'
                         r'moonshotai|stability|foundry|azure)\.')
_PROVIDER_PATH = re.compile(r'^(openai|azure|anthropic|bedrock|vertex_ai|gemini|google)/')
_CONTRACT = re.compile(r'[-_.](reasoning|with[-_.]thinking|without[-_.]thinking|websearch|google[-_.]search)$')
_PINS = [re.compile(p) for p in (r'@default$', r'@\d{8}$', r'-\d{4}-\d{2}-\d{2}$', r'-\d{8}$', r'-v1(:\d+)?$',
                                 r'-preview$', r'-latest$')]
_FAMILY_VENDOR = [(re.compile(r'^(gpt|o1|o3|o4|chatgpt)'), 'openai'), (re.compile(r'^claude'), 'anthropic'),
                  (re.compile(r'^(gemini|veo)'), 'google'), (re.compile(r'^llama'), 'meta')]


def canonical_model(model_id):
    if not model_id:
        return None
    s = model_id.strip().lower()
    s = re.sub(r'^\d+_', '', s)
    s = _PROVIDER_PATH.sub('', s)
    s = re.sub(r'^responses/', '', s)
    hint = None
    while True:
        m = _REGION.match(s) or _VENDOR_DOT.match(s)
        if not m:
            break
        if _VENDOR_DOT.match(s):
            hint = {'moonshotai': 'moonshot'}.get(m.group(1), m.group(1))
        s = s[m.end():]
    s = _CONTRACT.sub('', s)
    changed = True
    while changed:
        changed = False
        for p in _PINS:
            n = p.sub('', s)
            if n != s:
                s, changed = n, True
    s = re.sub(r'-+', '-', s.replace('.', '-').replace('_', '-'))
    s = s.replace('claude-v3-haiku', 'claude-3-haiku')
    vendor = hint if hint in ('openai', 'anthropic', 'google', 'meta') else None
    if vendor is None:
        for pat, v in _FAMILY_VENDOR:
            if pat.match(s):
                vendor = v
                break
    return f'{vendor}/{s}' if vendor else None


def head_key(model_id, effort):
    c = canonical_model(model_id)
    if c is None:
        return None
    e = effort if effort not in (None, '', 'default', 'none') else 'default'
    return f'{c}|{e}'


# --- statistics ---------------------------------------------------------------------------------------------
def _betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    front = math.exp(ln)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def jeffreys_lower(passed, n, confidence=LB_CONFIDENCE):
    """One-sided lower bound: the (1 - confidence) quantile of Beta(passed + 1/2, n - passed + 1/2)."""
    if n <= 0 or passed <= 0:
        return 0.0
    a, b = passed + 0.5, n - passed + 0.5
    target = 1.0 - confidence
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if betainc(a, b, mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def nearest_rank(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


# --- view ---------------------------------------------------------------------------------------------------
def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def reviewed_demand(row):
    """Reviewed demand, else authored difficulty for the reviewed-authoring waves, else None."""
    demand = row.get('reviewed_demand')
    if not demand and row.get('wave') in AUTHORED_WAVES:
        demand = row.get('authored_difficulty')
    return demand if demand in DEMANDS else None


def _open(path):
    path = Path(path)
    return gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz' else open(path, encoding='utf-8')


def iter_view(path):
    """Yield one dict per kept row: demand, head, family, lineage, item, passed, output, model, line."""
    with _open(path) as handle:
        for line, text in enumerate(handle):
            row = json.loads(text)
            if row.get('outcome') == 'unknown' or not row.get('model'):
                continue
            if (row.get('wave'), row.get('arm')) in ROUTED_ARMS:
                continue
            demand = reviewed_demand(row)
            head = head_key(row['model'], row.get('effort'))
            if demand is None or head is None:
                continue
            output = (row.get('usage') or {}).get('output')
            yield {'demand': demand, 'head': head, 'family': row.get('family'), 'model': row['model'],
                   'lineage': row.get('lineage') or str(row.get('task_id')),
                   'item': (row.get('wave'), str(row.get('task_id'))), 'passed': row.get('outcome') == 'pass',
                   'output': output if type(output) is int and output >= 0 else None, 'line': line}


# --- cells --------------------------------------------------------------------------------------------------
def _cell_stats(rows):
    n = len(rows)
    passed = sum(1 for r in rows if r['passed'])
    return n, passed


def _item_outcomes(rows):
    """One outcome per (wave, task) item; replicate rows of one head must all pass (conservative)."""
    out = {}
    for r in rows:
        out[r['item']] = out.get(r['item'], True) and r['passed']
    return out


def _tier(demand, lb, adverse, paired):
    if lb < FLOOR[demand]:
        return 'out'
    rate = adverse / paired if paired else 0.0
    if rate <= ADVERSE_ELIGIBLE:
        return 'eligible'
    if demand == 'deep' and rate <= ADVERSE_PROVISIONAL and adverse <= PROVISIONAL_MAX_ADVERSE_ITEMS:
        return 'provisional'
    return 'out'


def _score_cell(demand, by_head, *, min_rows=1, min_lineages=0):
    """by_head: head -> list of kept rows of one (demand[, family]) cell. Returns head -> cell row."""
    stats = {}
    for head, rows in by_head.items():
        n, passed = _cell_stats(rows)
        lineages = len({r['lineage'] for r in rows})
        if n < min_rows or lineages < min_lineages:
            continue
        stats[head] = {'rows': rows, 'n': n, 'pass': passed, 'lineages': lineages,
                       'first': min(r['line'] for r in rows)}
    candidates = [h for h, s in stats.items() if s['n'] >= MIN_BEST_N]
    best = min(candidates, key=lambda h: (-stats[h]['pass'] / stats[h]['n'], -stats[h]['n'], stats[h]['first'])) \
        if candidates else None
    best_items = _item_outcomes(stats[best]['rows']) if best else {}
    cells = {}
    for head, s in stats.items():
        lb = jeffreys_lower(s['pass'], s['n'])
        mine = _item_outcomes(s['rows'])
        common = [k for k in mine if k in best_items]
        adverse = 0 if head == best else sum(1 for k in common if best_items[k] and not mine[k])
        paired = 0 if head == best else len(common)
        outputs = [r['output'] for r in s['rows'] if r['output'] is not None]
        p90 = nearest_rank(outputs, OUTPUT_PERCENTILE)
        cells[head] = {'n': s['n'], 'pass': s['pass'], 'lineages': s['lineages'], 'lb90': round(lb, 6),
                       'adverse': adverse, 'paired_n': paired, 'best_head': best,
                       'tier': _tier(demand, lb, adverse, paired),
                       'expected_output_tokens_p90': p90, 'output_n': len(outputs)}
    return cells


def build(dataset_path, *, generated_at):
    dataset_path = Path(dataset_path)
    rows = list(iter_view(dataset_path))
    models = defaultdict(set)
    pooled = defaultdict(lambda: defaultdict(list))
    fam = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in rows:
        models[r['head']].add(r['model'])
        pooled[r['demand']][r['head']].append(r)
        if r['family']:
            fam[r['demand']][r['family']][r['head']].append(r)
    heads = {}
    for demand in DEMANDS:
        for head, cell in _score_cell(demand, pooled[demand]).items():
            heads.setdefault(head, {'cells': {}, 'families': {}})['cells'][demand] = cell
        for family, by_head in sorted(fam[demand].items()):
            scored = _score_cell(demand, by_head, min_rows=FAMILY_MIN_N, min_lineages=FAMILY_MIN_LINEAGES)
            for head, cell in scored.items():
                heads[head]['families'].setdefault(family, {})[demand] = cell
    out_heads = {}
    for head in sorted(heads):
        canonical, _, effort = head.partition('|')
        out_heads[head] = {'canonical_model': canonical, 'effort': None if effort == 'default' else effort,
                           'model_ids': sorted(models[head]),
                           'cells': {d: heads[head]['cells'][d] for d in DEMANDS if d in heads[head]['cells']},
                           'families': {f: v for f, v in sorted(heads[head]['families'].items())}}
        if not out_heads[head]['families']:
            del out_heads[head]['families']
    return {'schema_version': 1, 'rule_revision': RULE_REVISION, 'generated_at': generated_at,
            'dataset_sha256': file_sha256(dataset_path), 'dataset_rows_total': sum(1 for _ in _open(dataset_path)),
            'rows_kept': len(rows), 'output_field': OUTPUT_FIELD,
            'rule': {'view': 'reviewed-demand', 'authored_waves': sorted(AUTHORED_WAVES),
                     'excluded_routed_arms': sorted(list(a) for a in ROUTED_ARMS),
                     'lower_bound': 'one-sided %d%% Jeffreys' % round(LB_CONFIDENCE * 100),
                     'floor': FLOOR, 'adverse_eligible': ADVERSE_ELIGIBLE, 'adverse_provisional': ADVERSE_PROVISIONAL,
                     'provisional_max_adverse_items': PROVISIONAL_MAX_ADVERSE_ITEMS, 'provisional_demands': ['deep'],
                     'min_best_n': MIN_BEST_N, 'family_min_n': FAMILY_MIN_N, 'family_min_lineages': FAMILY_MIN_LINEAGES,
                     'output_percentile': OUTPUT_PERCENTILE},
            'heads': out_heads}


def dumps(table):
    return json.dumps(table, indent=1, sort_keys=True, ensure_ascii=True) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('dataset', help='dataset-r81/dataset.jsonl (offline input, never committed)')
    parser.add_argument('--out', default=str(Path(__file__).with_name('admission-table.json')))
    parser.add_argument('--generated-at', required=True, help='ISO date recorded in the table (explicit, for reproducibility)')
    args = parser.parse_args(argv)
    text = dumps(build(args.dataset, generated_at=args.generated_at))
    Path(args.out).write_text(text)
    print(f'wrote {args.out} sha256={hashlib.sha256(text.encode()).hexdigest()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
