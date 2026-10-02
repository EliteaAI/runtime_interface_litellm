# Workload quality and cost policy v3

This is an **opt-in, unactivated** extension to the configured selection policy.
V1 and V2 retain their behavior. V3 does not add capability evidence or alter a
production profile. Synthetic regression fixtures demonstrate code behavior,
not model quality. No benchmark request IDs or judging rubrics enter selection.

## Decision contract

1. Resolve current authorized deployment, native transport/effort, context and
   output support. Explicit aliases are owner declarations; model-name strings
   never supply quality or transport support.
2. Classify the next requested deliverable and its typed work profile. Retain
   existing demand floors, uncertainty handling and adverse-evidence holds.
3. Match exactly one quality cohort by demand, all five work-profile fields and
   the trusted execution envelope. Missing or overlapping support abstains.
4. Preserve the V2 statistical contract: absolute success lower bound and paired
   adverse-probability upper bound on independent, representative groups, with
   the same one-sided Wilson/Bonferroni calculation. Unknowns stay adverse. A
   reference model is a comparator, not ground truth; wins do not cancel losses.
5. Rank eligible choices only when **every surviving choice** has a comparable,
   prospectively validated usage forecast for the exact work profile. Missing
   support uses the configured fallback, with its normal availability, binding
   and adverse-evidence checks. Never silently substitute an arbitrary model.

The administrative fallback remains explicitly unmeasured. It is not described
as meeting the quality target or minimizing cost. An unavailable fallback fails
closed. More stringent support can increase fallback frequency until adequate
evidence exists; enabling V3 with today's sparse pilot would not fix that.

## Configuration and evidence

Use `calibration_selection_policy.quality.version = 3`. The fields
`minimum_groups`, `minimum_success_lower_bound`, and
`maximum_paired_loss_upper_bound` retain their V2 meanings. For the current
prospective plan the latter two remain 0.95 and 0.02. Cohorts are named by an
owner-chosen ID and contain all V2 cohort fields plus:

- `demand`: `simple`, `standard` or `deep`;
- `work_profile`: the existing typed `work`, `reasoning`, `evidence`, `creativity`
  and `verification` enums; no prompts, task IDs or rubric names;
- the original reviewed outcomes, independent group identities, source digest,
  reference variant and execution envelope.

The quality policy also supplies `native_contract_sha256`, an exact map from
each variant appearing in outcomes to the canonical SHA256 of that configured
variant's complete `native` object (sorted UTF-8 JSON, compact separators).
Changing a deployment alias, owner/fingerprint, reasoning, transport or output
contract invalidates this binding. A reviewed alias migration must produce a
new policy and evidence binding; similarity of names is not sufficient.

V3 currently supports provider-default output mode only. The service does not
transfer its quality claim to a request with an explicit output cap. The
current configuration fingerprint binds the advertised provider capacity;
capacities remain admission metadata, never predicted token use. This adds no
generation cap and does not alter native provider output defaults.

Usage forecasts enter through the existing sealed `evidence_ref` snapshot, not
caller fields. Each validated usage cohort must include the exact
`work_profile` and `output_mode: "provider_default"` inside its certified hash.
It retains delivery, demand, measured input range, native request digest and
empirical call trajectories. Different profiles can coexist; duplicates reject.
The existing certificate gates remain: at least eight independent training and
eight validation groups, WAPE <= 0.5 and p90 symmetric cost factor <= 3, connected
training groups excluded, no tuning on that validation run. These are trusted
offline certificate assertions; the runtime verifies binding and declared
gates, not the independence or honesty of the source study.

## Cost meaning and limits

Rank by the equal-template expected cold/write native generation expenditure
over recorded calls. Billed reasoning is included in output usage, once. A
cheaper token rate can lose when it needs more billed tokens or native calls.
The old common-output scenario is never used as a V3 forecast. Missing or
invalid competitor support cannot give another candidate an artificial win.

This estimate covers the supplied native trajectories. It does **not** establish
full Auto savings: classifier overhead, separately billed external tools and
any authorized recovery/rework must also be metered in the end-to-end study.
Common classifier overhead does not change ordering inside one routing decision,
but does change comparison with a fixed-model reference. Unknown bills stay
unknown. No retries or recovery are authorized by this configuration.

Cache ranking and cache-aware switching are disabled on the V3 path, even if
legacy switching policies exist. Warm scenarios remain separate observations;
previous-model preference can only break equal-price ties. TTL300 is unchanged.

## Verification and activation gates

`tests/test_quality_workload_v3.py` exercises installed snapshots and the actual
service selection/signing path with a stub classifier. It checks workload and
native binding, unknown/overlapping quality support, measured total-cost ordering,
missing/changed forecasts, provider-default output, and fallback authorization.
Run each test file in a fresh Python process, as documented in the root README.

Before activation: review cohort representativeness and independent ancestry,
freeze source and classifier versions, verify current bindings/prices, establish
adequate quality and forecast evidence, then validate the whole frozen Auto
policy on fresh separate requests. A 100-request diagnostic does not necessarily
prove the 95%/2-point contract. No real deployment or release claim follows from
unit tests or observed model-share diversity.
