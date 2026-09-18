# runtime_interface_litellm
Runtime interface: LiteLLM

## Auto local-beta calibration candidate

The current candidate is **V12**: measured model/effort contracts, task-group
context retrieval, richer classifier properties and comparable expenditure
ranking. See the [current implementation and rollout contract](routing/V12-IMPLEMENTATION.md).
The V9 description below is retained as historical provenance and replay guidance.

Auto remains off by default, with the saved public handle `v7-quality-cost` revision 1.
The live inventory comes from authenticated Configurations: current-project models
plus shared public-project models. A current-project definition shadows only the
same exact name before health/admission; shared-only names remain available.
Adding a model configuration does not invent evidence, and no administrator/user
model-to-rubric mapping is required.

`routing/v7/calibration-v9.json` is a generated central evidence snapshot, not a
deployment inventory. It retains 100 original calibration cells for Terra, Sol,
Opus 5, Opus 4.8 and Opus 4.7 provider-default presets:313 pass, 57 fail, 5 unknown. Only
54 complete all-pass family cells (3/3 or 4/4) can enter the local-beta candidate.
Existing V7 variants and their family cohorts remain. The classifier vocabulary
now contains the 20 measured families, including architecture/development/API,
security review and performance analysis. No new model passed the full
architecture family. Uncertain work still uses the explicitly provisional baseline.

New admission requires the exact measured family and an observed authored demand
band. Demand is not a model-independent effort label: default-only evidence grants
no low/medium/high preset coverage. The experiment did not independently label
operations; the selector's known-operation vocabulary is structural and is not
represented as additional model evidence. Unknown/ambiguous or unresolved tasks
cannot enter a new variant via fallback. Native schema-enforced output remains
unmeasured for this cohort and excludes it. Raw JSON quality is a separate matter.

The five new contracts measured a 32000-token total output allowance. An omitted
output cap resolves per eligible variant: existing cohorts keep 8000, new cohorts
use 32000. Explicit limits are never enlarged; a smaller explicit cap or model
configuration limit excludes the new contract. Cost ranking prices each candidate
at its own allowance; signing/checkpoint budgets and response cache observations
use the selected value. This is a conservative upper-cost scenario, not a claim
that all output tokens will be spent or that model thinking is controllable.

New bindings sign `routing_transport` and the minimum measured output cap. The SDK
must preserve omitted-cap provenance and enforce Chat Completions for Terra/Sol
and native Messages for Opus. The relay rejects transport/cap/thinking changes to
these new contracts. Native default thinking is left to the provider; explicit
thinking overrides are not the measured default. Old/manual transport behavior
is unchanged. Install together with the corresponding SDK update.

Reproduce the JSONs from the original sealed export (the compiler verifies its
SHA256 and does not accept held-out benchmark outcomes or successful retries):

```sh
python3 scripts/compile_v9_candidate.py --source <sealed-v9-export.json> --output routing/v7
for test_file in tests/test_*.py; do
  python3 -m pytest -q --rootdir=tests "$test_file" || exit 1
done
```

Run with the repository Python environment (including `pytest` and `jsonschema`).
Legacy test loaders use process-global stubs, so the files run in isolated processes.

The source export SHA is
`869c7592376cf547ebb03c3b293b8284622e47e2727dbb8d9ec1a104340a07dd`.
The generated JSON contains generation-freeze/task-artifact provenance and all
counts/confidence intervals. `SOURCE-MANIFEST.json` retains the old source hashes
separately from updated product hashes. Live model limits and exact-model Costs
prices remain authoritative; calibration prices do not replace billing prices.
Sol's catalog long-context price discrepancy remains an evidence limitation.

This changes the internal evidence/policy revision and requires a process reload.
Drain active Auto runs before rollout. Old signed active-run pins explicitly
reject renewal/dispatch after the change; they do not silently switch models.
Completed-turn state can start a new external selection without reusing old-policy
pending-intent evidence. No table or migration is added.

This candidate is **provisional local beta**, with `production_promotion_allowed`
false. Three/four samples have wide confidence bounds. Source fixtures and mocked
cross-repository tests are not live transport or held-out quality validation.
The next acceptance gate is a drained local install plus native/tool/pin replay,
then a separately frozen held-out comparison; evidence must not be relabelled as
production-qualified merely because a model becomes selectable.
