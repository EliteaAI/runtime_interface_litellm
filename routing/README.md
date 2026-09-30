# Runtime inventory and calibrated policy

Trusted deployment bindings may additionally supply `coverage_fallback_native`
when the response model or reasoning contract is absent from an installed
calibration profile. It is mutually exclusive with `coverage_fallback_variant`.
The contract contains `model_binding` (name, owning project, configuration
fingerprint), `effort`, `transport`, `reasoning_fields`, `reasoning_format`,
`output_allowance`, and `cache_write_mode`. It accepts only the native mappings
already supported by the SDK. There is no default model or request-body override.

This administrative choice supplies an unmeasured response binding when an
optimized choice is unsupported, including history/tools outside the measured
envelope. It does not extend the calibrated envelope or add quality cells.
Classifier uncertainty remains visible and the response model receives the
original request. Access, current configuration identity, pricing, reasoning,
output/context bounds, and explicit qualification holds still apply. Adverse
evidence for the same measured model and native contract blocks fallback.
Fallback is excluded from economic ranking and forecasts. It does not retry a
provider error. Policy/configuration changes revoke old pins. UI editing remains
a separate increment; no deployment is enabled by this implementation.

The EL-6722 candidate adds [restart-safe advisory cache observations](V13-CACHE-OBSERVATIONS.md)
with a matching SDK timing/usage envelope. It does not promote new calibration cells.

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

Run standalone routing/relay tests without importing the Pylon plugin lifecycle:

```sh
for file in tests/test_*.py; do
  python -m pytest --rootdir=tests --confcutdir=tests "$file" -q || exit
done
```

Legacy relay and client-tag tests install different import stubs, so run files
in separate processes rather than combining their global stub environments.

## Explicit fallback for missing V14 coverage

### Configured selection policy

A trusted project/profile binding may additionally supply `selection_policy`.
This separates administrator-authorized deployment eligibility from exact
measured qualification. Without this field, the existing strict calibration
behavior is unchanged. Request bodies and classifier replies cannot introduce
the policy.

The policy has a revision, source/hash, allowed request-scope IDs, bounded
history/tool permissions, and named variants. Each variant supplies a native
deployment binding (owner and fingerprint), operations, explicit family/demand
eligibility, and an optional installed evidence reference. The latter includes
the profile revision, variant and snapshot hash. An explicit alias can map to
that evidence without inferring equivalence from a display name. Changed
sources, native contracts, revoked deployments and stale pins are rejected.
Exact adverse or unresolved evidence and qualification holds remain exclusions;
missing or statistically sparse measurements remain visible without becoming
universal execution bans. Configured eligibility does not create a measured
pass, confidence certificate or production promotion.

Configured permission alone never authorizes a cheapest-model decision. A
`quality` policy supplies `minimum_groups` (at least four),
`minimum_pass_rate`, `maximum_quality_gap`, and `cohorts` keyed by difficulty.
Each cohort records its source/hash, unique independent `groups`, and one
`pass`/`fail`/`unknown` outcome per group for every compared configured variant.
The same groups and denominator are required across model/effort choices;
lineage deduplication and semantic review belong to the offline evidence owner.
Unknowns remain in the denominator. An optional quality tolerance is explicitly
owned by the operator; zero permits only the best conservative score.

The selector first checks the observed quality floor, then retains candidates
within the configured tolerance of the best Wilson lower-bound score. Price
ranks only those survivors. These are conservative selection statistics, not
per-request guarantees or an independent noninferiority result. Sparse, missing
or inadequate quality evidence invokes the explicit response fallback with its
unmeasured status; it never silently authorizes the cheapest candidate. The
`quality_screen` trace shows the counts, source, thresholds and exclusions.
Rubrics generate offline votes; runtime does not need a matching rubric to
consume this difficulty-level quality evidence. Existing exact adverse holds
still apply. A policy without `quality` is therefore fallback-only.

When every quality-screened candidate has compatible validated usage support,
the selector can compare those forecasts. Otherwise it prices the same input proxy
and configured output scenario for every eligible candidate, including cold
cache-write costs. This is a price scenario, not expected usage, a cache-hit
promise or measured savings. One missing forecast no longer sends the entire
eligible pool to catalog-order fallback. Uncertain tasks or an empty permitted
pool still use the separately configured response fallback.

Configured selection uses **provider-default generation output**. Optional
output-limit fields are omitted; APIs requiring a maximum use the current
deployment's configured maximum. Explicit caller limits remain respected. The
price-comparison scenario never imposes a generation limit or substitutes a
model's maximum capacity for its expected output. This includes the configured
response fallback; there is no fixed 8,000-token generation limit in this mode.

Policy storage remains deployment-managed in this increment. The planned
UI-managed classifier, fallback, evidence import and alias editor are separate
work. The new mode is opt-in and must be evaluated on the deployment's workload.

An installed V14 profile remains strict unless its trusted deployment binding
sets `coverage_fallback_variant` to one exact variant ID from that profile.
The ID is an operator choice, independent of the catalog baseline and classifier;
no model is automatically recommended or selected as the fallback. For example,
the project entry under `auto_routing_calibration_profiles` can contain
`profile`, `revision`, and `coverage_fallback_variant`. Omit the last field (or
set it to null) for the existing strict behavior. Request bodies cannot opt in.

When exact qualification leaves an empty pool, only this configured variant may
serve a missing work profile, insufficient independent templates, or insufficient
quality confidence. It must still pass live availability, authorization, price,
native transport/output, operation, demand and reasoning-effort checks. Failed,
disputed, unknown, incompatible or held exact evidence cannot use this path.
An unavailable fallback never selects another model automatically. This is not
provider-error retry or failover, and it does not broaden the installed envelope.

The trace marks `UNMEASURED_CONFIGURED_FALLBACK`, grants no quality qualification,
and provides no comparative forecast or savings claim. Economic reranking cannot
replace the chosen fallback. Changing it changes policy/gate revisions and
invalidates old signed pins; an unchanged same-invocation pin renews normally.
The frozen calibration records and benchmark remain unchanged. A subsequent
candidate still requires fresh quality/cost validation and application acceptance.

Source tests cover ordering-independent union/override, unavailable shadows,
actor-aware discovery, shared-only retention, configuration revocation/replacement,
no-call greetings, same-run renewal, exact-owner relay and existing manual/Usage
behavior. Live acceptance is separate from these tests and from model-quality
calibration.
