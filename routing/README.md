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
deployment is bound per variant (`inventory.rank_deployments`: explicit identity,
project-owned, preferred contract, the measured deployment name, shortest,
lexicographic); losers appear as `DUPLICATE_CANONICAL_DEPLOYMENT` with the selected
winner. Price never enters the ranking, so the binding is a function of the inventory.
If the bound deployment fails the price or cache-write price gate, admission runs every
gate on the next ranked duplicate and binds the first that passes
(`inventory.rebound`, `rebound_from`); every tried deployment and its failed gate is in
`inventory.rebound_attempts`, and `unmeasured_contracts` names the deployment it came from.
Any other gate failure on a duplicate stops the variant; further duplicates are not tried.
The preflight quotes winners and the classifier first and duplicates only up to the
64-name Costs bound; duplicates beyond it are listed in `inventory.unquoted_alternates`
and count as unpriced. A renewal (prior pin) keeps its pinned deployment while it is
still a ranked candidate and passes the gates, so a later price row cannot flip an
in-flight run (`rebound.reason: PINNED_DEPLOYMENT`).

Pool signals, request-independent, in `trace.inventory` and from the RPC
`runtime_interface_litellm_auto_preflight(project_id, user_id)` (Configurations readiness
will consume it in a follow-up): `no_priced_deployment` (canonical model -> deployments with
the remedy "add a Costs price alias for <name>"), `transport_unmeasured` (canonical models
whose every variant has a contract their deployments' transport cannot meet) and
`low_tier_priced` (the trace reports whether a low-tier deployment is admitted for this
request; the RPC whether one is priced). DIAL serves Anthropic models as
OpenAI-compatible, so every Anthropic calibration contract (`anthropic_messages`) does not
match their transport there. Since #6722 that is no longer a hard gate: such a variant is
admitted at provisional tier with `CALIBRATION_TRANSPORT_UNMEASURED` in the trace
(`inventory.provisional_contracts`, `selection.admission.flags`), and `transport_unmeasured`
keeps listing the canonical model as a readiness signal. A canonical match only
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

## Interim admission table and output ceiling (#6722)

Product routing admits by `v7/admission-table.json`, not by the catalog `tasks`,
`max_demand` and `preferences` tables or `qualification-policy.json`. The table is
generated offline from the R81 reviewed-demand view by `v7/build_admission_table.py`
(stdlib; the dataset is an input and is never committed):

```sh
python routing/v7/build_admission_table.py /path/to/dataset-r81/dataset.jsonl --generated-at YYYY-MM-DD
python -m pytest --rootdir=tests tests/test_admission_table_builder.py -q   # pinned digest, numbers
```

The table of record is label revision `material-r2`; `v7/README-admission-table.md` lists its digests, the heads
admitted and the cells that changed tier. Admission is not selection: selection is the price ordering over the
eligible heads.

Per canonical head (`vendor/family|effort`) and demand it holds counts, a one-sided 90%
Jeffreys lower bound, a paired adverse count against the best head, a tier (`eligible`,
`provisional` at deep only, `out`) and the cell's p90 output tokens. A head or demand
without a row is out. The chooser takes the cheapest admitted variant by the economics
ranking; a provisional winner is flagged in `trace.selection`. An uncertain descriptor
goes to the cheapest variant that clears the deep floor and only to the baseline when
nothing does. `trace.policy` names the table digest and rule revision; the digest is part
of `policy_revision`, so a new table refuses pins issued under the old one.

An absent `output_cap` is the provider default (`config.max_tokens` is `None`): the cap
is never defaulted or bounded above except by the model's own `max_output_tokens`. A
cap below the cell's p90 output rejects that variant (`CALIBRATION_ALLOWANCE_BELOW_P90`),
and ranking prices the cell p90, never the provider maximum. An Anthropic deployment
behind an OpenAI-compatible transport is admitted at provisional tier with
`CALIBRATION_TRANSPORT_UNMEASURED`. Greeting still reads the legacy `tasks` and
`preferences`; the frozen V9/V12 family cells remain for the lab suites
(`compile_catalog(admission=False)`).

Diagnostics (names and codes only, never request text): `trace.inventory.gate_failures`
maps every qualified variant that was not admitted to the gate that removed it
(`CONTEXT_WINDOW`, `PRICE`, `CACHE_WRITE_PRICE`, `OUTPUT_LIMIT`, `THINKING_BUDGET`,
`CONFIGURATION_REASONING_DISABLED`, ...) and `trace.inventory.input_bytes` is the size the
context gate used (`max` of the projected history and the SDK's `generation_input_bytes`).
`trace.selection.admission.rejections` maps every chooser candidate that was rejected to its
reasons (`ADMISSION_NO_ROW`, `ADMISSION_ROW_OUT`, `CALIBRATION_ALLOWANCE_BELOW_P90`,
`EFFORT_NEED_FILTER`, `QUALIFICATION_REJECTED`, `outside configured PoC pool`). Each
`resolve_auto_routing` call also logs one INFO line `auto-routing decision project=... scope=...
classifier=... descriptor=op/demand/relation/family/effort selection=variant/reason/tier
model=... cap=... admitted=n/m gate_failures=vid:CODE,... rejections=vid:CODE,...`
(`refused=<reason>` for a refusal), readable in pylon_main logs.

Input size is measured in UTF-8 bytes (`MAX_REQUEST_BYTES`, `trace.inventory.input_bytes`) but
context windows and prices are in tokens, so the context-window gate and every price quote use
`estimated_input_tokens(size) = ceil(size / 3)` (`routing/pricing.py`, trace
`inventory.input_tokens_estimate` and `input_estimator`). Three bytes per token is conservative:
English prose is about four, code and JSON about three. The SDK's per-turn runtime-context
blocks are real prompt content and stay counted.
