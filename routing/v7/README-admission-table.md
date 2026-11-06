# Admission table of record

**Since #6826 round (vii) the loader serves `admission-table-material-r2-vii-3of4-ref30-limit-ai.json`**, sha256
`09a9bcd29f7b2f8590126c9fc27b46b087801d7e89e93e488dd76129d6e4ba00` (21 heads, rule revision
`interim-reviewed-demand-1+ac2+ref30+limit_oracle`, 3-of-4 panel labels). Its rules, cell fields (`status: floor_only`,
`cross_provider_confirmed`, `label_regime`, `marginal`, `reference_head`) and the round's results are documented in
`autorouting_impl/admission-rebuild-iv/README.md` and `REPORT-vii.md`; the Gateway consumes tiers only. Selection:
`floor_only` cells are candidates only under an explicit `economical` mode. They are never admitted as a tier
through the unmeasured last resort (#7307 / AB.0): that path is tier-less, ranks every hard-gated deployment by quote
and marks the route provisional, so a floor_only deployment can be routed there exactly like an `out` one, with tier
none. Premium modes (and `tiered` at deep) start on the premium head: among admitted cells that are cross-provider
confirmed and panel-labelled, `eligible` before `provisional`, then the highest full-precision `lb90` (tie: paired
adverse rate, list price, canonical order; `premium_tiebreak` in the trace). Without one, premium modes start as
`tiered` (`PREMIUM_INACTIVE`) and `tiered` deep stays on the cheapest head (`TIERED_FRONTIER_UNCONFIRMED`). An
uncertain descriptor starts on the cheapest admitted head in every mode. The `tiered` standard mid tier is not a
premium posture: it may pick any eligible cell of another model, confirmed or not.
The material-r2 table below stays as `admission-table.json` for the history of the pins.

## Previous table of record (`admission-table.json`)

Label revision: **`material-r2`** (#6722 / #6826). Admission is not selection: selection is the policy's price
ordering over the eligible heads.

| Item | Value |
|---|---|
| Table | `routing/v7/admission-table.json`, sha256 `0d2320a526469f6cf12ad4313148b250bfe75b06e23cbb0890273491305e4692` |
| Previous table | wave-outcome labels, sha256 `5eef4a1a250aa781c0a11d7b52ffd2eb494301860d9eb1e0f5c4cb4413cf8d31` (kept as `tests/fixtures/admission-table-previous-wave-outcome.json`) |
| Label set | `label-set-material-r2.jsonl` (15,950 rows, no text), sha256 `98a78853fa8ac5a64b4981c7108ed43bd71be2956f965bfbf4ffa79ee623eafb` (not committed) |
| Labels of record | Stage B panel labels, revision `material-r2`, `stage-b-labels-record-material-r2.jsonl`, sha256 `010e4572a1691e04404270f1074a38f5149a3d1bf59cc523898e9a25b327bb21` |
| Dataset | R81 reviewed-demand view, sha256 `f5509ecf25fa1e4d49079c8f9f677c4c132d549078079c94ec4adefa548d826d`, 2,029 rows kept |
| Rule | `interim-reviewed-demand-1`, unchanged: floors 0.90 / 0.90 / 0.85, one-sided 90% Jeffreys lower bound, adverse 0.05 (provisional 0.08, at most 3 items, deep only), family refinement n >= 20 and lineages >= 8 |
| Builder | `build_admission_table.py`, unchanged. Only the per-row pass flag differs: the label of the label set (oracle, reweighted judge, or panel majority under the material standard), refusal and incomplete counted as non-pass |

The table carries one extra top-level key, `label_revision`. The loader (`admission.load`) reads named keys only, so
it ignores it; `tests/test_admission_table_builder.py` pins the key.

## What changed against the previous table

86 of the 2,029 view rows changed from fail to pass or back: 36 oracle verdicts that override the wave outcome (fail
to pass), 3 oracle pass to fail, 45 Stage B panel fail to pass under the strict standard, 2 more panel fail to pass
from the material re-read. The change comes from the already accepted r3 label rebuild; the material standard alone
moves no tier (strict-r3 against material-r2: 0 tier changes, 2 cell counts).

**7 cells change tier, 5 heads become admitted, none is removed.**

| Demand | Head | Previous | material-r2 |
|---|---|---|---|
| simple | claude-opus-4-7 default | out (25/27, lb90 0.839) | eligible (27/27, lb90 0.952) |
| standard | gpt-5.4 default | out (18/21, lb90 0.737) | eligible (21/21, lb90 0.938) |
| deep | gpt-5.6-luna default | out (59/65, lb90 0.853, 4/38 adverse) | eligible (63/65, lb90 0.931, 1/38 adverse vs sol-high) |
| deep | gpt-5.6-luna high | out (35/38, lb90 0.849) | provisional (36/38, lb90 0.883, 2/38 adverse) |
| deep | claude-opus-5-5 default | out (38/43, lb90 0.809) | eligible (42/43, lb90 0.929, 1/38 adverse) |
| deep | claude-opus-5-5 high | provisional (59/65, lb90 0.853) | eligible (62/65, lb90 0.910, 1/38 adverse) |
| deep | claude-sonnet-5-5 default | provisional (36/38, lb90 0.883) | eligible (37/38, lb90 0.921, 1/38 adverse) |

Heads that became admitted: claude-opus-4-7 default (simple), gpt-5.4 default (standard), gpt-5.6-luna default
(deep, eligible), gpt-5.6-luna high (deep, provisional), claude-opus-5-5 default (deep). Opus 5.5 high and Sonnet 5.5
at deep were already admitted and move from provisional to eligible. Other cells change counts but not tier.
gpt-5.6-terra default stays provisional at deep (63/65, 2/38 adverse). gpt-5.6-sol high stays the best deep head
(38/38, lb90 0.965).

Consequence for routing: under the economical posture (cheapest admitted head by price) deep work now goes to
gpt-5.6-luna default, like every other demand. The previous table sent it to terra default (provisional). The
`premium_at_deep` mode (PR #53) selects the highest-lb90 eligible head instead, today sol high.

## Provenance that travels with the labels

The anchor gate passed (ALL 143/149 = 0.960 [0.915, 0.981]; A1 70/75; A2 5/6; B 22/22; E1 30/30; E2 16/16), with
these caveats: the anchor was adjudicated mostly with the judges' cited defect shown (so it is a human confirmation
of the panel's reasons, not an independent blind label set); A2 is a six-item stratum whose lower bound says nothing;
the human labeller is the author of the system. The deep row is judge dependent (an Anthropic judge scored every v14
row), so the table stays interim by construction. The table is an admission floor and a price order, not a quality
ranking.

## Rebuild

```sh
# 1. the builder over the label set (the dataset and the label set are inputs and are never committed)
python autorouting_impl/admission-rebuild-material-r2/rebuild_tables.py
# 2. pinned digest, cells, tier diff and (where both inputs exist) the byte-for-byte rebuild
python -m pytest --rootdir=tests tests/test_admission_table_builder.py -q
```

The rebuild test runs only where `dataset-r81/dataset.jsonl` and the label set exist (override with `R81_DATASET`
and `MATERIAL_R2_LABEL_SET`) and checks the label set's sha256 first. Changing the table changes its digest, the
policy revision and every pin issued under the old table.
