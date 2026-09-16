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
The currently shipped profile remains the V7-derived five-model/eight-variant
profile; expanded V8/V9 results are not silently imported.

Classifier and generation dispatch use the selected configuration owner and the
caller's billing-project key. Signed pins contain the configuration fingerprint
and policy revision. Renewal rechecks the current effective inventory. Auto
dispatch requires the exact owner-prefixed LiteLLM deployment; it never falls
back to a same-name shared deployment or raw model name when that deployment
disappears. Existing manual dispatch retains its previous mapping behavior.

Installing this inventory contract invalidates older pins without a configuration
fingerprint. A new invocation obtains a current binding; an old active invocation
receives a 409 instead of silently changing configuration. No routing table or
SDK wire-field change is required: the added identity remains inside the opaque
Gateway pin.

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
