# Integration branch `integration/6722-v14-on-main`

Local comparison build only. It is not a PR and must not be merged as is.

`origin/main` (0.2.28, already contains #6826 as squash #46)
+ `feat/6826-canonical-join-configured-classifier` (f01be5f; tree identical to main, only `metadata.json` conflicted, main's kept)
+ draft PR #37 `feat/v14-reviewed-freeze-r35` (bb3fee7).
Version: 0.2.29.

## What each side contributes

- **#6826**: catalog/calibration `canonical_model`. Inventory joins by canonical identity, with duplicate resolution. The classifier comes from `settings['classifier']`. `RoutingUnavailable` and the 422 `{error, reason}` carry reason codes. `auto_routing_settings(rpc, project_id, user_id)`. Vendor checks are identity-based. Prices use `canonical_by_name`.
- **PR #37**: installed calibration bundles (`routing/calibration/`, `bundle.py`). Uniform catalog compiler. Configured selection policy (`selection_policy.py`, quality V1–V4 in `quality.py`). Native coverage fallback (`fallback_config.py`). Measured trajectory economics, request scope, difficulty trace and lexical shadow mode.

## Conflicts and resolutions

| File | Resolution |
|---|---|
| `routing/service.py` | `_calibrated_router` passes `CONFIGURED_CLASSIFIER`, so the calibrated path calls the configured deployment and the snapshot's `classifier_variant` is only a catalog label. `compiled_router` also passes it. No `luna-default` default anywhere. Transport: `is_anthropic(model, variant)` (#6826) plus PR #37's configured native transport. Trace keeps both the #6826 classifier deployment/price-match block and PR #37's `difficulty` block. |
| `methods/routing.py` | Settings come from `auto_routing_settings(..., user_id)`, then `bundle.runtime_settings` (503 on an invalid binding). `configured_router(settings)` replaces `compiled_router()`. `qualified_inventory(..., project_id=)`. The price request includes the classifier and `canonical_by_name`, with a TypeError fallback. The 422 keeps `{error, reason}`. |
| `methods/proxy.py` | Relay check uses `auto_routing_settings(..., user_id)`, then `runtime_settings`. |
| `routing/v7/catalog.py` | `canonical_model` is kept, and so is PR #37's `enabled: bool(demands)`. |
| `routing/v7/coordinator.py` | Takes PR #37's lexical parameters. `classifier_variant` stays required (no default). |
| `routing/v7/SOURCE-MANIFEST.json` | Union of notes and support files. Every product hash and compiler hash was regenerated. |
| `tests/test_auto_routing_service.py`, `test_model_owned_clarification.py`, `test_v9_candidate.py` | Both sides kept: identity fixtures plus an explicit classifier, and PR #37's router/output policy. |

Integration changes, so that PR #37's joins use the #6826 canonical identity:

- `routing/v7/catalog.py::SNAPSHOT_CANONICAL_MODEL`: `compile_uniform_catalog` gives every installed-snapshot variant a `canonical_model`, taken from its exact name (#6826 §3.2 rules; checked against Configurations `resolve_identity`). The reviewed bundle bytes and `manifest.json` digests are unchanged, so existing `evidence_ref.snapshot_sha256` values stay valid. A snapshot variant that already carries `canonical_model` keeps it.
- `routing/inventory.py::_joins`: owner-configured native bindings (`configured_routing_contract`, `configured_fallback_contract`) stay exact-name joins. They are signed owner assertions; admission re-validates the owner and fingerprint. This was required because, with #6826 identities on the inventory, these variants never joined.
- `routing/v7/measured_economics.py`: trajectory prices quote the bound deployment (`gateway.deployments`), as `economics.py` already did for #6826. The cache identity stays on the measured variant.
- `routing/v7/candidate.py`: the "existing exact evidence hold" and the coverage-fallback adverse veto also match the bound deployment's canonical model (`gateway.deployment_canonicals`), so a renamed deployment cannot hide an adverse result.
- Left exact on purpose: `selection_policy` `evidence_ref` (variant id plus snapshot digest), `fallback_config` / `selection_policy` `model_binding` (name + project + fingerprint), and signed pins. `methods/proxy.py:429` `'anthropic' in model_name` is the known #6826 follow-up and is unchanged.

## Tests

Every file was run in its own interpreter (`python -m pytest -q --rootdir=tests --confcutdir=tests tests/<file>.py`): **49/49 files green**.

New: `tests/test_6722_integration.py` (7 tests). Each fails when its fix is reverted.

Changed PR #37 tests, all to the integrated contract (explicit classifier identity, no `luna-default` default):

- `test_auto_inventory_dispatch.py`: `GenerationRouter(..., CONFIGURED_CLASSIFIER)`, 3 sites.
- `test_reasoning_format.py`, `test_v13_output.py`, `test_v9_candidate.py`: `CONFIGURED_CLASSIFIER`.
- `test_context_source_integrity.py` (3 sites), `test_demand_evidence.py`, `test_v13_qualification.py`, `test_v14_continuity.py`, `test_v14_lexical_runtime.py`: these lab routers name the catalog variant `'luna-default'` explicitly.
- `test_v14_release_bundle.py::test_real_installed_context_service_admits_qualified_transform_without_forecast`: `settings['classifier']` is set; previously the snapshot label supplied it.
- `test_quality_contract_v2.py::test_restricted_pool_and_custom_aliases_keep_configured_transport`: the configured classifier is the renamed alias of the snapshot classifier deployment.

## Enabling the calibrated / configured policy locally

**Where.** The `descriptor.config` of the plugin is built by `recursive_merge` (Pylon `descriptor.load_config`) from three layers:

1. the plugin's `config.yml`;
2. `pylon_main/pylon.yml` → `configs.runtime_interface_litellm`;
3. the folder config provider `/data/configs/runtime_interface_litellm.yml`. On this stack that file is `centry/pylon_main/configs/runtime_interface_litellm.yml`.

Use layer 3. It already holds `auto_routing_calibration_profiles` with an older project `'1'` binding (`local-quality-r48`). Add the project `'2'` key under the same map. Config is read at boot, so `pylon_main` must restart afterwards.

**Keys** (per project id, as a string), validated by `routing/bundle.py::runtime_settings`. Unknown keys fail closed with a 503.

| Key | Required | Meaning |
|---|---|---|
| `profile` | yes | `isolated-text`, `repository-fixture`, `bounded-text-conversation` or `long-text-conversation` |
| `revision` | yes | must equal `routing/calibration/manifest.json` revision: `v14-application-r35` |
| `coverage_fallback_variant` | no | an exact variant id from the profile (e.g. `m16-default`). It does **not** cover requests outside the profile scope (history/tools). |
| `coverage_fallback_native` | no | `{model_binding:{name,project_id,fingerprint}, effort, transport, reasoning_fields, reasoning_format, output_allowance, cache_write_mode}`. Labelled unmeasured. It covers out-of-scope requests (e.g. multi-turn chat). Mutually exclusive with `coverage_fallback_variant`. |
| `selection_policy` | no | configured pool (`selection_policy.validate`). With `quality.version: 4` it is the V4 development screen. Generation then uses provider-default output. |

The Auto gates are unchanged. Project and platform Auto must be enabled, and a classifier must resolve: the project's explicit classifier, else the project's Low-tier model, else the platform fallback. A binding never enables Auto.

**Ready to paste (project 2, bundled manifest, calibrated measured router).** Indent it under the existing `auto_routing_calibration_profiles:` key. Do not add a second top-level key.

```yaml
auto_routing_calibration_profiles:
  '2':
    profile: isolated-text
    revision: v14-application-r35
```

What to expect from this binding:

- First-turn isolated text requests route among the profile's variants whose canonical identity matches a project-2/shared deployment.
- Measured output is used: a deployment is admitted only if its `max_output_tokens` ≥ the measured allowance, usually 128000.
- `openai_compatible` deployments of the 15 `anthropic_messages` variants show up as `CALIBRATION_TRANSPORT_UNMEASURED` in `trace.inventory.unmeasured_contracts`.
- Requests with history or tools get a 422 `Auto cannot resolve this request` (`reason: null`).

To keep multi-turn chats working, add a native fallback bound to a real project-2 deployment:

```yaml
    coverage_fallback_native:
      model_binding: {name: gpt-5.4, project_id: 2, fingerprint: <configuration_fingerprint>}
      effort: medium
      transport: chat_completions
      reasoning_fields: {reasoning_effort: medium}
      reasoning_format: top_level
      output_allowance: 16000
      cache_write_mode: ordinary_input
```

`fingerprint` must equal the current `configuration_fingerprint` that Configurations returns for that deployment (`configurations_get_routing_models(2, user_id)`). The `model_binding` inside the base64 body of any Auto `pin` that selected the deployment also contains it. A mismatch is not fatal: the fallback is excluded with `CONFIGURED_FALLBACK_BINDING_CHANGED`. This was not verified on the local stack.

**V4 configured policy.** The latest V4 package is `autorouting_impl/experiments/stage-v14/analysis/implementation-integration-r78/selection-policy.json` (plus `fallback-native.json`). It cannot be pasted unchanged:

- Its `evidence_ref.revision` is `r78-development-offline`, which is not in the installed manifest, so the refs fail closed. Either set each `evidence_ref` to `null` or install that bundle.
- Its `model_binding`s are stage project 1 deployments. Rebind each `native.model_binding` to project-2 deployments (name/project_id/fingerprint).
- After rebinding, recompute `quality.native_contract_sha256[v] = routing.v7.retrieval.digest(native)`. That is an owner assertion that the stage evidence transfers to the local deployment.

Its scope is `text:isolated-text-r2`, one user turn, no tools. Pair it with `profile: isolated-text` and a `coverage_fallback_native`. Its `anthropic_messages` variants are excluded on `openai_compatible` deployments.

**No binding present.** `runtime_settings` returns the settings unchanged and `configured_router` returns `compiled_router()`. That is the legacy compiled V12 catalog (`catalog.json` + `calibration-v12.json`), with canonical joins, the configured classifier and provider-default output. It is the same router path as main / #6826, but it also carries PR #37's shared changes: classifier prompt/difficulty, request semantics, lexical shadow and output defaults. A project without a binding is therefore the baseline to compare against, but it is not byte-identical to main.
