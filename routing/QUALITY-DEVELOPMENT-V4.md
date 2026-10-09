# Development quality policy, version 4

Version 4 is an opt-in, deployment-owned development screen. It does not change
existing version 1/2/3 policies or enable Auto by default. A development screen
allows a candidate to be evaluated; it does not establish release confidence.
The whole frozen Auto policy still needs a fresh prospective quality and cost
comparison before promotion.

## Selection contract

1. Match one exact demand, five-axis work profile and execution envelope. History,
   tools and user-turn bounds remain enforced. Overlapping matching cohorts
   abstain. Request IDs, rubric names and teacher answers are not selectors.
2. Bind evidence to the configured deployment, effort, transport, reasoning
   serialization, provider output allowance and cache accounting contract.
   Explicit owner alias mappings preserve provenance; guessed name similarity
   does not grant evidence.
3. Use only the declared connected training groups for observed statistics.
   `minimum_groups` must be at least 8. Operators declare
   `minimum_observed_success` and `maximum_observed_paired_adverse_fraction`.
   Any fail or unknown anywhere in the matching exact cohort vetoes the variant,
   including later validation or review. Wins cannot offset those negatives.
4. Rank eligible variants only when **every** eligible competitor has a comparable,
   prospectively validated total-usage forecast for the current input range and
   native contract. Rank expected cold/write expenditure across connected groups.
   Provider capacity and a common output-token scenario are not usage forecasts.
5. Otherwise use only the explicitly configured available fallback and label it
   unmeasured. The fallback cannot bypass a negative or unknown for its same exact
   native contract in a matching development cohort. This veto also applies when
   matching cohorts overlap. A fallback with no evidence remains an operator
   execution choice, not a measured quality or savings claim.

Unknown prices or usage remain unknown. Optional output limits stay omitted under
provider-default semantics; protocols requiring an output allowance retain their
configured native value. An explicit user output cap cannot borrow the
provider-default quality evidence. Cache-aware ranking and switching are disabled
for version 4; this policy does not change TTL or activate lexical bypass.

## Evidence shape

The outer configured selection policy and native bindings retain the existing
schema. Its `quality` object contains exactly:

- `version: 4`
- `minimum_groups`
- `minimum_observed_success`
- `maximum_observed_paired_adverse_fraction`
- `native_contract_sha256`: one digest per configured variant
- `cohorts`: source/hash, all connected `groups`, explicit `training_groups`,
  per-variant `outcomes`, `reference_variant`, `demand`, `work_profile` and
  `evaluation_scope`

`training_groups` is a nonempty unique subset of `groups`. Outcomes remain
`pass`, `fail` or `unknown`; all groups must have an outcome for each represented
variant. The paired adverse fraction counts a candidate non-pass against a
reference outcome other than fail, conservatively including reference unknowns.
The all-observed negative veto is stronger than the observed-rate thresholds.

The trace reports `evidence_stage: development`, `release_qualified: false` and
`quality_guaranteed: false`, plus exact source, training counts, negative counts,
candidate exclusions and comparable forecast quotes. It never converts a small
all-pass sample into a 95% population-success certificate.

## Verification

Run each test file in a fresh interpreter because existing fixtures install
process-global stubs:

```sh
python -m pytest -q --rootdir=tests tests/test_quality_development_v4.py
python -m pytest -q --rootdir=tests tests/test_quality_contract_v2.py
python -m pytest -q --rootdir=tests tests/test_quality_workload_v3.py
python -m pytest -q --rootdir=tests tests/test_configured_selection_policy.py
python -m pytest -q --rootdir=tests tests/test_v14_native_fallback.py
```

These synthetic tests verify policy behavior. Model quality, real provider
availability, economic benefit and full application acceptance require separate
evidence. Local evidence packages are deployment inputs and are not bundled or
activated by adding this implementation.
