# Runtime inventory and calibrated policy

Auto reads `configurations_get_routing_models(project_id, user_id)` after the
Gateway authenticates the actor and execution project. The effective inventory
is a union of current-project and shared models. Only a same-name shared entry
is replaced by the project definition: `{A, B} + {B, C}` becomes `{A, B, C}`.
Limits, health and transport flags belong to the selected configuration; they
are not merged with the shadowed entry. There is no separate personal-project
union when execution occurs in a team project.

`inventory.qualified_inventory` joins those live entries to the versioned policy
contracts. Calibration artifacts describe model/effort suitability, not project
inventory. A discovered model without evidence appears as
`NO_CALIBRATED_MODEL_CONTRACT`; price alone never supplies a quality label.
Users and administrators do not maintain model-to-rubric mappings. Broader
calibration produces central profiles, which require validation before promotion.
The local-beta candidate retains the V7-derived five-model/eight-variant cohort
and compiles twenty measured presets from V11: ten model identities and 28 presets
in total. Only 248 of 400 new family/preset cells passed all original tasks. Exact
observed demand, measured effort/transport and 32,000 total output allowance remain
required. Smaller explicit limits and native schema-enforced output exclude these
contracts. V9 remains available for historical replay. See the
[V12 implementation contract](V12-IMPLEMENTATION.md) for data flow and limits.

`v7/calibration-v11-observations.json` is a separate evidence-only export of
five models × four requested efforts × 75 matched calibration tasks. Its 400
family/preset cells preserve original results, difficulty support, fresh-default
pairs, catalog generation costs and unknown charges. Four targeted regrades
(three passes, one still invalid), nineteen source-shape ambiguity annotations,
and offline unchanged-code checks remain separate from the original outcomes.
Actual realized effort was not reported; native Anthropic observations do not
qualify compatible-mode execution. Three/four examples per cell and provisional
dual judges do not establish population quality guarantees.

No runtime loader reads that raw observation file. A separate compiler produces
`calibration-v12.json`, whose conservative all-pass contracts now drive provisional
effort admission. The live project/shared inventory remains authoritative.
Held-out quality validation remains a separate promotion gate. The
[V11 next-phase plan](V11-NEXT-PHASES.md) remains the historical plan;
[V12](V12-IMPLEMENTATION.md) records implementation and remaining work. Judges and
adjudicators remain evaluation-only.
Reproduce the export from the sealed
experiment package (no model calls):

```sh
python scripts/compile_v11_observations.py --source-root /path/to/model-efforts-v11 \
  --output routing/v7/calibration-v11-observations.json
python -m pytest --rootdir=tests --confcutdir=tests tests/test_v11_observations.py -q
```

Classifier and generation dispatch use the selected configuration owner and the
caller's billing-project key. Signed pins contain the configuration fingerprint
and policy revision. Renewal rechecks the current effective inventory. Auto
dispatch requires the exact owner-prefixed LiteLLM deployment; it never falls
back to a same-name shared deployment or raw model name when that deployment
disappears. Existing manual dispatch retains its previous mapping behavior.

Catalog and calibration variants declare `canonical_model` next to the exact
measured name (#6826). Inventory items carrying a Configurations `identity` join
a variant by canonical identity (chat kind, D6 request contract; web-search
deployments never join); items without `identity` join by exact name. One
deployment is bound per variant (`inventory.select_deployment`: explicit identity,
project-owned, preferred contract, shortest, lexicographic); losers appear as
`DUPLICATE_CANONICAL_DEPLOYMENT` with the selected winner. A canonical match only
joins an existing measured contract; it never adds quality evidence. The classifier
is the configured deployment from `settings['classifier']` (project, else platform),
resolved by name and owner in the visible inventory and called without effort.
Missing or unusable settings fail with a reason code (`CLASSIFIER_NOT_CONFIGURED`,
`CLASSIFIER_UNAVAILABLE`, `CLASSIFIER_PRICE_UNAVAILABLE`, `NO_QUALIFIED_MODELS`).

Product Auto always returns a generation binding, never a router-authored chat
reply. `service.GenerationRouter` converts ancestor clarification decisions
(missing input, ambiguous references or non-unique pending tasks) through the
same eligibility and effort selector while its invocation context is active.
It preserves `needs_context`, input status and uncertainty in the trace; it does
not invent sources, lower demand, add advisory prompts or make another classifier
call. The selected model receives the original SDK messages and tools and can
answer or ask for information naturally. These uncertain requests retain the
existing conservative selection policy. If no model is eligible, normal admission
failure remains an error. The resulting model/effort pin survives same-run tools
and resume. This product boundary adds `model-owned-clarification-1` to the internal
policy revision; ancestor calibration behavior and frozen evidence are unchanged.

The calibration candidate changes the internal signed policy revision while
retaining the public saved profile handle. Drain active Auto invocations before
installing it: old active pins receive a 409 rather than silently changing models.
A new external invocation may obtain a current binding. Install the matching SDK
update, which preserves absent-cap provenance and enforces the signed measured
transport. No routing table or database migration is required.

## V5 selector and policy (#6722)

**Policy** (`routing/policy.py`). Configurations serves the effective policy as
`settings['policy']` (platform default deep-merged with the project override, covered
by `revision`). The Gateway validates it strictly: unknown keys, wrong types and
out-of-bounds values are `POLICY_INVALID`.

| Key | Default (= the literal before V5) | Bounds |
|---|---|---|
| `shadow_mode` | `true` | bool |
| `selector` | `{artifact: null, tau: 0.85}` | installed artifact id; `tau` 0.5..0.99 |
| `pool` | `null` (every admitted deployment) | 1..256 canonical ids or exact deployment names |
| `fallback` | `null` | `{name, project_id}` |
| `output` | `{default_allowance: 8000, min_cap: 256, max_cap: 32000}` | 256 <= min <= default <= max <= 128000 |
| `thinking_budget_tokens` | `{low: 2048, medium: 4096, high: 9092}` | ints, strictly increasing |
| `greeting_rule` | `true` | bool |
| `classifier` | `{enabled: false}` | bool; no runtime effect in V5 v1 (ablation only) |

- No `policy` key (older Configurations): the literals above apply, no V5 code
  runs, the trace has no `v5` and pins carry the code revision exactly as before.
- Invalid policy: Auto never refuses for it. The literals apply, legacy runs and
  `trace.v5.reason == 'POLICY_INVALID'`.
- `pool` and the other values apply to the legacy path too (hard gates are shared).

**Hard gates** (`routing/gates.py`) are shared by both paths: available, chat kind,
pool, calibration contract, explicit effort, Anthropic thinking budget, context
window, output cap, price, cache-write price. Each deployment-variant reports every
failed gate; the trace lists them per candidate.

**Decision** (`routing/v5.py`). Candidates are the gated catalog deployment-variants.
Each maps to a selector head `<identity.canonical>|<effort or default>`; without a
head it is `NO_SELECTOR_HEAD`. Expected cost = `PriceBook.quote(deployment,
Tokens(ceil(request_bytes / 4), min(head expected_output_tokens, cap)))`. The cheapest
candidate with `p_pass >= tau` wins (`SELECTED`); an exact cost tie keeps the
previous turn's variant (incumbent). None qualifies: `policy.fallback` resolved among
the gated deployments, lowest effort first (`FALLBACK_NO_QUALIFIED`), else
`NO_QUALIFIED_MODELS`. A standalone social turn without instructions takes the
cheapest gated deployment without scoring (`GREETING_RULE`). A missing or invalid
artifact is `SELECTOR_UNAVAILABLE` (fallback when configured).

**Shadow/active** (`routing/service.py`). `shadow_mode: true`: the legacy decision is
executed and `trace.v5 = {selector_id, selector_sha256, tau, candidates, chosen,
reason, shadow, legacy_choice, policy_revision}`; a V5 defect is traced as `V5_ERROR`
and never breaks Auto. `shadow_mode: false`: the V5 choice is executed, no LLM
classifier is called or required; `SELECTOR_UNAVAILABLE`/`NO_QUALIFIED_MODELS` are a
422 `{error, reason}` only when no fallback can be used. An artifact flagged `stub` is
never executed. Every pin and checkpoint carries `policy_revision = <code revision>
+policy-<sha256(effective policy, artifact id@sha256)>`; resolve renewal and the
relay refuse a pin issued under another policy. The preflight emits the
`auto_routing_shadow_decision` tracing span (tracer `auto-routing`) with ids, scores
and costs only, never request text.

**Installing an artifact.** Put `selector-v5-<id>.json` (schema
`elitea-auto-selector/1`, `sha256` = sha256 of the canonical JSON without that field:
sorted keys, `(',', ':')` separators, no NaN) into `routing/selector/`, add
`{"<id>": {"file": "...", "sha256": "...", "stub": false}}` to
`routing/selector/MANIFEST.json`, and set `policy.selector.artifact` to the id. The
loader refuses size > 2 MB, unknown schema, out-of-bounds weights, and any hash
mismatch with the file or the manifest. `selector-v5-stub` is a two-head wiring stub
(never trained). Until WP-1 lands, `routing/selector.py` carries an interim scorer
(feature hashing documented in its docstring) that WP-1's `scorer.py` replaces.

Run standalone routing/relay tests without importing the Pylon plugin lifecycle:

```sh
for file in tests/test_*.py; do
  python -m pytest --rootdir=tests --confcutdir=tests "$file" -q || exit
done
```

Legacy relay and client-tag tests install different import stubs, so run files
in separate processes rather than combining their global stub environments.

Source tests cover ordering-independent union/override, unavailable shadows,
actor-aware discovery, shared-only retention, configuration revocation/replacement,
no-call greetings, same-run renewal, exact-owner relay and existing manual/Usage
behavior. Live acceptance is separate from these tests and from model-quality
calibration.
