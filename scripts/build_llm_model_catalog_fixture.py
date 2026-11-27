"""Build tests/fixtures/llm-model-catalog-efforts.json from a gateway catalog and the real LiteLLM resolver.

Run with LiteLLM 1.108.3 installed:
    python -I scripts/build_llm_model_catalog_fixture.py <catalog.json> tests/fixtures/llm-model-catalog-efforts.json

<catalog.json> is a saved GET /public/litellm_model_cost_map?catalog_only=true response.
One catalog key is kept per distinct combination of effort-relevant fields, together with its bare twin.
Synthetic Azure entries are added for the "none needs an explicit true" rule, which the catalog alone does not
exercise in both directions; their expected levels come from the real resolver as well.
"""

import json
import os
import sys
from importlib.metadata import version

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm  # pylint: disable=C0413,E0401
from litellm.llms.azure.chat.gpt_5_transformation import AzureOpenAIGPT5Config  # pylint: disable=C0413,E0401
from litellm.router_utils.reasoning_effort_capability import (  # pylint: disable=C0413,E0401
    resolve_supported_reasoning_efforts,
)

EFFORT_FIELDS = (
    "mode", "litellm_provider", "supports_reasoning", "reasoning_effort_levels",
    "supports_none_reasoning_effort", "supports_minimal_reasoning_effort", "supports_low_reasoning_effort",
    "supports_xhigh_reasoning_effort", "supports_max_reasoning_effort",
)
FORM_MODES = ("chat", "responses")
AZURE_WITHOUT_NONE_FLAG = {
    "mode": "chat", "litellm_provider": "azure", "supports_reasoning": True, "supports_minimal_reasoning_effort": True,
}
SYNTHETIC_ENTRIES = {
    "azure/gpt-5-synthetic-without-none-flag": AZURE_WITHOUT_NONE_FLAG,
    "azure/gpt5_series/synthetic-without-none-flag": AZURE_WITHOUT_NONE_FLAG,
    "azure/gpt-5-chat-synthetic-without-none-flag": AZURE_WITHOUT_NONE_FLAG,
    "azure/o9-synthetic-without-none-flag": AZURE_WITHOUT_NONE_FLAG,
}


def effort_fields(entry):
    return {field: entry[field] for field in EFFORT_FIELDS if field in entry}


def bare_key(key, entry):
    provider = entry.get("litellm_provider")
    if isinstance(provider, str) and key.startswith(f"{provider}/"):
        return key.removeprefix(f"{provider}/")
    return None


def main(catalog_path, fixture_path):
    with open(catalog_path, encoding="utf-8") as catalog_file:
        catalog = {key: entry for key, entry in json.load(catalog_file).items() if isinstance(entry, dict)}
    catalog.update(SYNTHETIC_ENTRIES)
    litellm.model_cost = catalog
    #
    kept = {key: key for key in SYNTHETIC_ENTRIES}
    for key in sorted(catalog):
        entry = catalog[key]
        if entry.get("mode") not in FORM_MODES:
            continue
        twin = catalog.get(bare_key(key, entry) or "", {})
        signature = json.dumps(
            [effort_fields(entry), effort_fields(twin), AzureOpenAIGPT5Config.is_model_gpt_5_model(key)],
            sort_keys=True,
        )
        kept.setdefault(signature, key)
    #
    entries, expected = {}, {}
    for key in sorted(kept.values()):
        entry = catalog[key]
        entries[key] = effort_fields(entry)
        twin_key = bare_key(key, entry)
        if twin_key in catalog:
            entries[twin_key] = effort_fields(catalog[twin_key])
        efforts = resolve_supported_reasoning_efforts({**entry, "key": key}, deployment_is_mapped=True)
        expected[key] = None if efforts is None else list(efforts)
    #
    with open(fixture_path, "w", encoding="utf-8") as fixture_file:
        json.dump(
            {"litellm_version": version("litellm"), "catalog": entries, "expected_efforts": expected},
            fixture_file, indent=1, sort_keys=True,
        )
        fixture_file.write("\n")


if __name__ == "__main__":
    main(*sys.argv[1:3])
