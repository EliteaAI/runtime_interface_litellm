"""The LLM model catalog is the gateway's LiteLLM catalog, reduced to chat models and the LLM model form's fields.

Guards: effort levels equal what LiteLLM 1.108.3 itself resolves, only chat entries and form fields leave the plugin,
Claude thinking models never offer levels ChatAnthropic rejects, and the gateway is asked at most once per cache window.
"""

import importlib
import json
import os
import sys
import threading
import types
import unittest
from unittest import mock


def _stub_pylon():
    pylon = types.ModuleType("pylon")
    pylon_core = types.ModuleType("pylon.core")
    pylon_tools = types.ModuleType("pylon.core.tools")
    log_stub = types.ModuleType("pylon.core.tools.log")
    for name in ("info", "debug", "warning", "error", "exception"):
        setattr(log_stub, name, lambda *a, **kw: None)
    web_stub = types.ModuleType("pylon.core.tools.web")
    web_stub.method = lambda *a, **kw: (lambda func: func)
    web_stub.rpc = lambda *a, **kw: (lambda func: func)
    pylon_tools.log = log_stub
    pylon_tools.web = web_stub
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("pylon.core.tools.web", web_stub),
    ]:
        sys.modules.setdefault(name, mod)


def _stub_plugin_package():
    plugins = types.ModuleType("plugins")
    plugins.__path__ = [os.path.dirname(_PLUGIN_ROOT)]
    plugin = types.ModuleType("plugins.runtime_interface_litellm")
    plugin.__path__ = [_PLUGIN_ROOT]
    sys.modules.setdefault("plugins", plugins)
    sys.modules.setdefault("plugins.runtime_interface_litellm", plugin)


_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stub_pylon()
_stub_plugin_package()

catalog = importlib.import_module("plugins.runtime_interface_litellm.utils.llm_model_catalog")
catalog_rpc = importlib.import_module("plugins.runtime_interface_litellm.rpc.llm_model_catalog")

with open(os.path.join(_PLUGIN_ROOT, "tests", "fixtures", "llm-model-catalog-efforts.json"), encoding="utf-8") as _file:
    LITELLM_FIXTURE = json.load(_file)

SYNTHETIC_AZURE_KEYS = (
    "azure/gpt-5-synthetic-without-none-flag", "azure/gpt5_series/synthetic-without-none-flag",
    "azure/gpt-5-chat-synthetic-without-none-flag", "azure/o9-synthetic-without-none-flag",
)

GATEWAY_CATALOG = {
    "sample_spec": {"mode": "one of: chat, embedding, completion", "supports_reasoning": True},
    "fallback_generalizations": {"capability": [{"match": "claude-[a-z]+-\\d+", "supports_reasoning": True}]},
    "gpt-5.6-luna": {
        "mode": "chat", "litellm_provider": "openai", "supports_reasoning": True, "supports_vision": True,
        "supports_none_reasoning_effort": True, "supports_minimal_reasoning_effort": False,
        "supports_xhigh_reasoning_effort": True, "input_cost_per_token": 1e-06, "max_input_tokens": 1050000,
    },
    "azure/gpt-5.6-luna": {"mode": "chat", "litellm_provider": "azure", "supports_reasoning": True},
    "gpt-5-pro": {
        "mode": "responses", "litellm_provider": "openai", "supports_reasoning": True,
        "supports_none_reasoning_effort": False, "supports_minimal_reasoning_effort": True,
    },
    "gpt-5.1": {
        "mode": "chat", "litellm_provider": "openai", "supports_reasoning": True,
        "supports_none_reasoning_effort": True, "default_reasoning_effort": "none",
    },
    "kimi-k3": {
        "mode": "chat", "litellm_provider": "moonshot", "supports_reasoning": True,
        "reasoning_effort_levels": ["max", "low", "high"], "default_reasoning_effort": "high",
    },
    "claude-sonnet-5-5": {
        "mode": "chat", "litellm_provider": "anthropic", "supports_reasoning": True, "supports_vision": True,
        "supports_adaptive_thinking": True, "thinking_always_on": True, "supports_low_reasoning_effort": True,
        "supports_xhigh_reasoning_effort": True, "supports_max_reasoning_effort": True,
    },
    "claude-sonnet-4-6": {
        "mode": "chat", "litellm_provider": "anthropic", "supports_reasoning": True,
        "supports_adaptive_thinking": True, "supports_max_reasoning_effort": True,
    },
    "claude-opus-4-5": {"mode": "chat", "litellm_provider": "anthropic", "supports_reasoning": True},
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0": {
        "mode": "chat", "litellm_provider": "bedrock_converse", "supports_reasoning": True,
    },
    "vertex_ai/claude-opus-4-5": {
        "mode": "chat", "litellm_provider": "vertex_ai-anthropic_models", "supports_reasoning": True,
    },
    "global.xai.grok-4.6": {"mode": "chat", "litellm_provider": "bedrock_converse", "supports_reasoning": True},
    "claude-3-haiku": {"mode": "chat", "litellm_provider": "anthropic"},
    "claude-opus-4-7": {
        "mode": "chat", "litellm_provider": "anthropic", "supports_reasoning": True,
        "supports_xhigh_reasoning_effort": True, "supports_max_reasoning_effort": True,
    },
    "claude-opus-4-5-20251101": {
        "mode": "chat", "litellm_provider": "anthropic", "supports_reasoning": True,
        "supports_low_reasoning_effort": True,
    },
    "claude-adaptive-without-levels": {
        "mode": "chat", "litellm_provider": "anthropic", "supports_reasoning": True,
        "supports_adaptive_thinking": True,
    },
    "gpt-4.1": {"mode": "chat", "litellm_provider": "openai", "supports_vision": True},
    "gpt-3.5-turbo": {"mode": "chat", "litellm_provider": "openai", "supports_reasoning": False, "supports_vision": False},
    "text-embedding-3-small": {"mode": "embedding", "litellm_provider": "openai"},
    "7_gpt-5.6-luna": {"litellm_provider": "openai", "supports_reasoning": True, "api_key": "sk-leaked"},
    "broken": "not an entry",
}


def _gateway(result=None, error=None):
    client = mock.Mock()
    if error is not None:
        client.litellm_api_call.side_effect = error
    else:
        client.litellm_api_call.return_value = GATEWAY_CATALOG if result is None else result
    return mock.Mock(return_value=client)


class CatalogTestCase(unittest.TestCase):

    def setUp(self):
        catalog._cached_models = None  # pylint: disable=W0212
        catalog._cache_expires_at = 0.0  # pylint: disable=W0212
        self.clock = mock.patch.object(catalog.time, "monotonic", return_value=1000.0).start()
        self.addCleanup(mock.patch.stopall)


class TestEffortsFollowLiteLLM(unittest.TestCase):

    def test_every_fixture_entry_resolves_to_the_levels_litellm_resolved(self):
        self.assertEqual(LITELLM_FIXTURE["litellm_version"], "1.108.3")
        self.assertGreater(len(LITELLM_FIXTURE["expected_efforts"]), 400)
        resolved = {}
        for key in LITELLM_FIXTURE["expected_efforts"]:
            efforts = catalog.supported_reasoning_efforts(
                key, LITELLM_FIXTURE["catalog"][key], LITELLM_FIXTURE["catalog"],
            )
            resolved[key] = None if efforts is None else list(efforts)
        self.assertEqual(resolved, LITELLM_FIXTURE["expected_efforts"])

    def test_the_fixture_covers_every_branch_of_the_rule(self):
        expected = LITELLM_FIXTURE["expected_efforts"]
        entries = LITELLM_FIXTURE["catalog"]
        self.assertIn(None, expected.values())
        self.assertIn([], expected.values())
        self.assertTrue(any("reasoning_effort_levels" in entries[key] for key in expected))
        for key in SYNTHETIC_AZURE_KEYS:
            self.assertIn(key, expected)
        self.assertNotIn("none", expected["azure/gpt-5-synthetic-without-none-flag"])
        self.assertNotIn("none", expected["azure/gpt5_series/synthetic-without-none-flag"])
        self.assertIn("none", expected["azure/gpt-5-chat-synthetic-without-none-flag"])
        self.assertIn("none", expected["azure/o9-synthetic-without-none-flag"])
        self.assertTrue(any(
            key.startswith(f"{entries[key].get('litellm_provider')}/")
            and not any(field.endswith("_reasoning_effort") for field in entries[key])
            and efforts
            for key, efforts in expected.items()
        ))


class TestFormVocabulary(CatalogTestCase):

    def setUp(self):
        super().setUp()
        self.models = catalog.form_models(GATEWAY_CATALOG)

    def test_only_chat_and_responses_entries_are_listed(self):
        self.assertEqual(set(self.models), {
            "gpt-5.6-luna", "azure/gpt-5.6-luna", "gpt-5-pro", "gpt-5.1", "kimi-k3", "claude-sonnet-5-5",
            "claude-sonnet-4-6", "claude-opus-4-5", "claude-adaptive-without-levels", "gpt-4.1", "gpt-3.5-turbo",
            "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "vertex_ai/claude-opus-4-5", "global.xai.grok-4.6",
            "claude-3-haiku", "claude-opus-4-5-20251101", "claude-opus-4-7",
        })

    def test_a_runtime_registered_deployment_from_an_older_gateway_is_not_listed(self):
        self.assertNotIn("7_gpt-5.6-luna", self.models)
        self.assertNotIn("sk-leaked", json.dumps(self.models))

    def test_only_form_fields_leave_the_plugin(self):
        allowed = {
            "provider", "supports_reasoning", "thinking_type", "supported_efforts", "default_effort", "supports_vision",
            "reasoning_locked",
        }
        for entry in self.models.values():
            self.assertLessEqual(set(entry), allowed)

    def test_an_openai_reasoning_model_offers_litellm_levels_and_can_be_turned_off(self):
        self.assertEqual(self.models["gpt-5.6-luna"], {
            "provider": "openai",
            "supports_reasoning": True,
            "supported_efforts": ["none", "low", "medium", "high", "xhigh"],
            "default_effort": "medium",
            "supports_vision": True,
        })

    def test_a_prefixed_entry_inherits_effort_flags_from_its_bare_twin(self):
        self.assertEqual(
            self.models["azure/gpt-5.6-luna"]["supported_efforts"], ["none", "low", "medium", "high", "xhigh"],
        )

    def test_an_always_on_claude_model_is_locked_and_never_offers_none_or_minimal(self):
        self.assertEqual(self.models["claude-sonnet-5-5"], {
            "provider": "anthropic",
            "supports_reasoning": True,
            "thinking_type": "always_on",
            "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
            "default_effort": "medium",
            "supports_vision": True,
            "reasoning_locked": True,
        })

    def test_an_adaptive_claude_model_is_locked_and_never_offers_none_or_minimal(self):
        self.assertEqual(self.models["claude-sonnet-4-6"], {
            "provider": "anthropic",
            "supports_reasoning": True,
            "thinking_type": "adaptive",
            "supported_efforts": ["low", "medium", "high", "max"],
            "default_effort": "medium",
            "reasoning_locked": True,
        })

    def test_an_adaptive_model_is_locked_even_without_effort_data(self):
        self.assertEqual(self.models["claude-adaptive-without-levels"], {
            "provider": "anthropic", "supports_reasoning": True, "thinking_type": "adaptive", "reasoning_locked": True,
        })

    def test_a_model_whose_levels_exclude_none_is_locked(self):
        self.assertEqual(self.models["gpt-5-pro"]["supported_efforts"], ["minimal", "low", "medium", "high"])
        self.assertIs(self.models["gpt-5-pro"]["reasoning_locked"], True)

    def test_a_claude_model_without_thinking_flags_or_levels_uses_the_token_budget_and_stays_unlocked(self):
        for key, provider in (
            ("claude-opus-4-5", "anthropic"),
            ("claude-opus-4-5-20251101", "anthropic"),
            ("eu.anthropic.claude-haiku-4-5-20251001-v1:0", "bedrock_converse"),
            ("vertex_ai/claude-opus-4-5", "vertex_ai-anthropic_models"),
        ):
            self.assertEqual(self.models[key], {
                "provider": provider, "supports_reasoning": True, "thinking_type": "enabled",
                "supported_efforts": ["low", "medium", "high"], "default_effort": "medium",
            })

    def test_a_non_claude_reasoning_model_without_effort_data_reports_no_levels_and_no_lock(self):
        self.assertEqual(
            self.models["global.xai.grok-4.6"], {"provider": "bedrock_converse", "supports_reasoning": True},
        )

    def test_a_claude_model_an_older_catalog_lists_with_levels_above_high_keeps_them(self):
        self.assertEqual(self.models["claude-opus-4-7"], {
            "provider": "anthropic", "supports_reasoning": True,
            "supported_efforts": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
            "default_effort": "medium",
        })

    def test_a_claude_model_without_reasoning_gets_no_thinking_mode(self):
        self.assertEqual(self.models["claude-3-haiku"], {"provider": "anthropic", "supports_reasoning": False})

    def test_a_model_litellm_resolves_to_no_levels_is_reported_as_not_reasoning(self):
        entry = {"mode": "chat", "litellm_provider": "openai", "supports_vision": True}
        self.assertIs(catalog.form_entry("gpt-4o", entry, {"gpt-4o": entry})["supports_reasoning"], False)

    def test_a_reasoning_model_without_level_data_stays_reasoning_with_levels_unknown(self):
        entry = {"mode": "responses", "litellm_provider": "openai", "supports_reasoning": True}
        self.assertEqual(
            catalog.form_entry("gpt-5.1-codex-max", entry, {"gpt-5.1-codex-max": entry}),
            {"provider": "openai", "supports_reasoning": True},
        )

    def test_a_route_that_inherits_levels_from_its_bare_twin_is_not_reported_as_not_reasoning(self):
        twin = {"mode": "chat", "supports_reasoning": True, "supports_minimal_reasoning_effort": True}
        entry = {"mode": "chat", "litellm_provider": "azure"}
        form = catalog.form_entry("azure/gpt-5-chat", entry, {"azure/gpt-5-chat": entry, "gpt-5-chat": twin})
        self.assertNotIn("supports_reasoning", form)
        self.assertIn("supported_efforts", form)

    def test_fields_litellm_does_not_report_are_omitted(self):
        self.assertEqual(
            self.models["gpt-4.1"], {"provider": "openai", "supports_reasoning": False, "supports_vision": True},
        )
        self.assertNotIn("supports_vision", self.models["claude-3-haiku"])

    def test_a_non_reasoning_model_reports_false_and_no_levels(self):
        self.assertEqual(
            self.models["gpt-3.5-turbo"], {"provider": "openai", "supports_reasoning": False, "supports_vision": False},
        )

    def test_a_litellm_default_of_none_gives_way_to_medium(self):
        self.assertEqual(self.models["gpt-5.1"]["default_effort"], "medium")

    def test_an_offered_litellm_default_is_kept(self):
        self.assertEqual(self.models["kimi-k3"]["supported_efforts"], ["low", "high", "max"])
        self.assertEqual(self.models["kimi-k3"]["default_effort"], "high")

    def test_none_is_never_the_default_even_when_it_is_the_first_offered_level(self):
        entry = {
            "mode": "chat", "supports_reasoning": True, "reasoning_effort_levels": ["none", "high"],
            "default_reasoning_effort": "none",
        }
        form = catalog.form_entry("m", entry, {"m": entry})
        self.assertEqual((form["supported_efforts"], form["default_effort"]), (["none", "high"], "high"))

    def test_levels_without_a_selectable_default_are_not_reported(self):
        entry = {"mode": "chat", "supports_reasoning": True, "reasoning_effort_levels": ["none"]}
        self.assertEqual(catalog.form_entry("m", entry, {"m": entry}), {"supports_reasoning": True})

    def test_the_first_offered_level_is_the_default_when_medium_is_not_offered(self):
        entry = {"mode": "chat", "supports_reasoning": True, "reasoning_effort_levels": ["max", "high"]}
        self.assertEqual(catalog.form_entry("m", entry, {"m": entry})["default_effort"], "high")


class TestCatalogCache(CatalogTestCase):

    def test_the_gateway_is_asked_inside_the_timeout_budget(self):
        gateway = _gateway()
        catalog.llm_model_catalog(gateway)
        gateway.assert_called_once_with(timeout=10)
        gateway.return_value.litellm_api_call.assert_called_once_with("model_cost_map", timeout=8)

    def test_the_response_carries_the_prefix_table_and_the_models(self):
        response = catalog.llm_model_catalog(_gateway())
        self.assertEqual(response["prefixes"], {
            "open_ai": "openai", "azure_open_ai": "azure", "amazon_bedrock": "bedrock", "vertex_ai": "vertex_ai",
            "ollama": "ollama", "ai_dial:azure": "azure", "ai_dial:openai": "openai",
            "ai_dial:anthropic": "anthropic",
        })
        self.assertIn("gpt-5.6-luna", response["models"])

    def test_a_second_call_inside_the_window_does_not_reach_the_gateway(self):
        gateway = _gateway()
        catalog.llm_model_catalog(gateway)
        self.clock.return_value += 599
        catalog.llm_model_catalog(gateway)
        self.assertEqual(gateway.return_value.litellm_api_call.call_count, 1)

    def test_the_catalog_is_fetched_again_after_the_window(self):
        gateway = _gateway()
        catalog.llm_model_catalog(gateway)
        self.clock.return_value += 601
        catalog.llm_model_catalog(gateway)
        self.assertEqual(gateway.return_value.litellm_api_call.call_count, 2)

    def test_a_gateway_failure_gives_an_empty_catalog(self):
        self.assertEqual(catalog.llm_model_catalog(_gateway(error=RuntimeError("boom")))["models"], {})

    def test_a_non_catalog_answer_gives_an_empty_catalog(self):
        self.assertEqual(catalog.llm_model_catalog(_gateway(result=["nope"]))["models"], {})

    def test_a_failure_is_remembered_for_a_minute_only(self):
        failing = _gateway(error=RuntimeError("boom"))
        catalog.llm_model_catalog(failing)
        self.clock.return_value += 59
        healthy = _gateway()
        self.assertEqual(catalog.llm_model_catalog(healthy)["models"], {})
        healthy.assert_not_called()
        self.clock.return_value += 2
        self.assertIn("gpt-5.6-luna", catalog.llm_model_catalog(healthy)["models"])

    def test_an_answer_without_chat_models_is_remembered_for_a_minute_only(self):
        catalog.llm_model_catalog(_gateway(result={"error": {"message": "starting"}}))
        self.clock.return_value += 61
        self.assertIn("gpt-5.6-luna", catalog.llm_model_catalog(_gateway())["models"])

    def test_a_failed_refresh_keeps_serving_the_last_loaded_catalog(self):
        catalog.llm_model_catalog(_gateway())
        self.clock.return_value += 601
        failing = _gateway(error=RuntimeError("boom"))
        self.assertIn("gpt-5.6-luna", catalog.llm_model_catalog(failing)["models"])
        self.clock.return_value += 59
        self.assertIn("gpt-5.6-luna", catalog.llm_model_catalog(failing)["models"])
        self.assertEqual(failing.return_value.litellm_api_call.call_count, 1)
        self.clock.return_value += 2
        catalog.llm_model_catalog(failing)
        self.assertEqual(failing.return_value.litellm_api_call.call_count, 2)

    def test_an_empty_refresh_keeps_serving_the_last_loaded_catalog(self):
        catalog.llm_model_catalog(_gateway())
        self.clock.return_value += 601
        self.assertIn("gpt-5.6-luna", catalog.llm_model_catalog(_gateway(result={}))["models"])

    def test_concurrent_first_calls_make_one_gateway_fetch(self):
        release = threading.Event()
        entered = threading.Event()

        def slow_fetch(*_args, **_kwargs):
            entered.set()
            release.wait(5)
            return GATEWAY_CATALOG

        gateway = _gateway()
        gateway.return_value.litellm_api_call.side_effect = slow_fetch
        results = []
        callers = [
            threading.Thread(target=lambda: results.append(catalog.llm_model_catalog(gateway)["models"]))
            for _ in range(4)
        ]
        for caller in callers:
            caller.start()
        entered.wait(5)
        release.set()
        for caller in callers:
            caller.join(5)
        self.assertEqual(gateway.return_value.litellm_api_call.call_count, 1)
        self.assertEqual(len(results), 4)
        self.assertTrue(all("gpt-5.6-luna" in models for models in results))


class TestCatalogRpc(CatalogTestCase):

    def test_the_rpc_serves_the_catalog_through_the_gateway_service_node(self):
        gateway = _gateway()
        module = types.SimpleNamespace(service_node=types.SimpleNamespace(call=gateway))
        response = catalog_rpc.RPC.litellm_llm_model_catalog(module)
        self.assertEqual(set(response), {"prefixes", "models"})
        gateway.return_value.litellm_api_call.assert_called_once_with("model_cost_map", timeout=8)


if __name__ == "__main__":
    unittest.main()
