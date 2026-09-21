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
