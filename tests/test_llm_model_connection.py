"""The LLM model connection test sends one request built from unsaved form values through the named credential.

Guards: the request never matches another project's registered deployment, carries no reasoning params,
stays inside the timeout budget, never reaches the gateway for an unregistered credential, and every
failure comes back as a category plus a message without URLs, addresses, keys or stack traces.
"""

import importlib
import os
import sys
import types
import unittest
from unittest import mock

try:
    import litellm
except ImportError:
    litellm = None


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
    tools_stub = types.ModuleType("tools")
    tools_stub.this = types.SimpleNamespace(descriptor=types.SimpleNamespace(config={}))
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("pylon.core.tools.web", web_stub), ("tools", tools_stub),
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

connection = importlib.import_module("plugins.runtime_interface_litellm.utils.llm_model_connection")
connection_rpc = importlib.import_module("plugins.runtime_interface_litellm.rpc.llm_model_connection")
open_ai = importlib.import_module("plugins.runtime_interface_litellm.tools.mappers.configuration.open_ai")
azure_open_ai = importlib.import_module("plugins.runtime_interface_litellm.tools.mappers.configuration.azure_open_ai")
ai_dial = importlib.import_module("plugins.runtime_interface_litellm.tools.mappers.configuration.ai_dial")

CREDENTIAL_NAME = "7_cred-uuid"
PROVIDER_TIMEOUT = 25

AUTH_ERROR = (
    "litellm.AuthenticationError: AuthenticationError: OpenAIException - Authentication Error, Invalid proxy "
    "server token passed. Received API Key = sk-..., Key Hash (Token) "
    "=be77445bb1d0d820085396d1487dac19d24dc8d4e84b7856da1edf080f1c860e. Unable to find token in cache"
    "\nstack trace: Traceback (most recent call last):\n  File \"/data/litellm/venv/lib/python3.12/site-packages/"
    "litellm/llms/openai/openai.py\", line 930, in acompletion"
)
PROXY_MODEL_DENIED = (
    "litellm.APIError: APIError: OpenAIException - team not allowed to access model. This team can only access "
    "models=['535_*', '1_*']. Tried to access gpt-nope-9\nstack trace: Traceback (most recent call last):"
)
AZURE_DEPLOYMENT_MISSING = (
    "litellm.NotFoundError: AzureException NotFoundError - The API deployment for this resource does not exist. "
    "If you created the deployment within the last 5 minutes, please wait a moment and try again."
)
ROUTE_NOT_FOUND = "litellm.NotFoundError: NotFoundError: OpenAIException - 404 page not found"
METHOD_NOT_ALLOWED = "litellm.APIError: APIError: OpenAIException - Error code: 405 - Method Not Allowed"
CONNECTION_ERROR = (
    "litellm.InternalServerError: InternalServerError: OpenAIException - Connection error.\nstack trace: "
    "Traceback (most recent call last):\n  File \"aiohttp/connector.py\", line 1574\n"
    "    except (asyncio.TimeoutError, ReadTimeout):\n"
    "ConnectionRefusedError: [Errno 111] Connect call failed ('10.255.255.1', 80)"
)
LITELLM_TIMEOUT = (
    "litellm.Timeout: APITimeoutError - Request timed out. Error_str: Request timed out. - timeout value=25.0, "
    "time taken=25.01 seconds\nstack trace: Traceback (most recent call last):"
)
GATEWAY_READ_TIMEOUT = (
    "Traceback (most recent call last):\n  File \"/data/plugins/worker_core/tools/rpc.py\", line 36\n"
    "requests.exceptions.ReadTimeout: HTTPConnectionPool(host='127.0.0.1', port=8081): Read timed out. "
    "(read timeout=28)"
)
VERTEX_MODEL_MISSING = (
    "litellm.NotFoundError: VertexAIException - Publisher Model `projects/p/locations/l/publishers/google/models/"
    "gemini-nope` was not found or your project does not have access to it."
)
ANTHROPIC_MODEL_MISSING = (
    "litellm.NotFoundError: AnthropicException - {\"type\":\"error\",\"error\":{\"type\":\"not_found_error\","
    "\"message\":\"model: claude-nope\"}}"
)
RATE_LIMITED = "litellm.RateLimitError: RateLimitError: OpenAIException - You exceeded your current quota"


def _expanded_row(name="gpt-4o", api_protocol=None, credential_type="open_ai"):
    data = {
        "name": name,
        "ai_credentials": {
            "elitea_title": "cred",
            "private": False,
            "api_base": "https://dial.internal.example",
            "api_key": "sk-live-secret",
            "configuration_uuid": "cred-uuid",
            "configuration_project_id": 7,
            "configuration_type": credential_type,
        },
    }
    if api_protocol is not None:
        data["api_protocol"] = api_protocol
    return {"project_id": 7, "uuid": "connection-test", "data": data}


class FakeGatewayCall:

    def __init__(self, gateway, timeout):
        self._gateway = gateway
        self._timeout = timeout

    def litellm_api_call(self, endpoint, *args, **kwargs):
        self._gateway.calls.append((endpoint, self._timeout, kwargs))
        if endpoint in self._gateway.raises:
            raise self._gateway.raises[endpoint]
        if endpoint == "credential_exists":
            return kwargs["credential_name"] in self._gateway.credentials
        return self._gateway.health_result


class FakeGateway:

    def __init__(self, credentials=(CREDENTIAL_NAME,), health_result=None, raises=None):
        self.credentials = list(credentials)
        self.health_result = health_result if health_result is not None else {"status": "success", "result": {}}
        self.raises = raises or {}
        self.calls = []

    def __call__(self, timeout=None):
        return FakeGatewayCall(self, timeout)

    def health_calls(self):
        return [call for call in self.calls if call[0] == "health_test_connection"]


def _mapped(name="gpt-4o", api_protocol=None, mapper=open_ai):
    return mapper.to_model(_expanded_row(name, api_protocol))


class TestBuildTestParams(unittest.TestCase):

    def test_open_ai_model_is_provider_qualified_and_keeps_the_named_credential(self):
        params = connection.build_test_params(_mapped()["litellm_params"], PROVIDER_TIMEOUT)
        self.assertEqual(params["model"], "openai/gpt-4o")
        self.assertEqual(params["litellm_credential_name"], CREDENTIAL_NAME)
        self.assertNotIn("api_key", params)

    def test_azure_model_is_provider_qualified_once(self):
        self.assertEqual(
            connection.build_test_params(_mapped(mapper=azure_open_ai)["litellm_params"], PROVIDER_TIMEOUT)["model"], "azure/gpt-4o",
        )
        self.assertEqual(
            connection.build_test_params(_mapped("whisper-1", mapper=azure_open_ai)["litellm_params"], PROVIDER_TIMEOUT)["model"],
            "azure/whisper-1",
        )

    def test_dial_routes_follow_the_protocol_with_unset_meaning_azure(self):
        cases = {
            None: ("azure/claude", "azure", None),
            "azure": ("azure/claude", "azure", None),
            "openai": ("openai/responses/claude", "openai", "https://dial.internal.example/openai/v1"),
            "anthropic": ("anthropic/claude", "anthropic", "https://dial.internal.example/anthropic"),
        }
        for protocol, (model, provider, api_base) in cases.items():
            with self.subTest(protocol=protocol):
                params = connection.build_test_params(_mapped("claude", protocol, ai_dial)["litellm_params"], PROVIDER_TIMEOUT)
                self.assertEqual(params["model"], model)
                self.assertEqual(params["custom_llm_provider"], provider)
                self.assertEqual(params.get("api_base"), api_base)
                self.assertEqual(params["litellm_credential_name"], CREDENTIAL_NAME)

    def test_the_request_never_equals_a_bare_registered_deployment_model(self):
        for mapper in (open_ai, azure_open_ai, ai_dial):
            with self.subTest(mapper=mapper.__name__):
                registered = _mapped(mapper=mapper)["litellm_params"]
                self.assertNotEqual(connection.build_test_params(registered, PROVIDER_TIMEOUT)["model"], registered["model"])

    def test_reasoning_params_are_never_sent(self):
        params = connection.build_test_params({
            "model": "gpt-5", "custom_llm_provider": "openai", "litellm_credential_name": CREDENTIAL_NAME,
            "reasoning_effort": "high", "thinking": {"type": "enabled"}, "reasoning": {"effort": "high"},
        }, PROVIDER_TIMEOUT)
        for key in ("reasoning_effort", "thinking", "reasoning"):
            self.assertNotIn(key, params)

    def test_the_provider_call_is_bounded_without_retries(self):
        params = connection.build_test_params(_mapped()["litellm_params"], PROVIDER_TIMEOUT)
        self.assertEqual(params["timeout"], PROVIDER_TIMEOUT)
        self.assertEqual(params["max_retries"], 0)

    @unittest.skipUnless(litellm, "litellm is installed only in the LiteLLM runtime")
    def test_the_qualified_model_reaches_the_same_provider_and_deployment(self):
        for provider, model in (("openai", "gpt-4o"), ("azure", "my-deployment"), ("bedrock", "anthropic.claude")):
            with self.subTest(provider=provider):
                qualified = connection.provider_qualified_model(model, provider)
                resolved_model, resolved_provider, _, _ = litellm.get_llm_provider(
                    model=qualified, custom_llm_provider=provider,
                )
                self.assertEqual((resolved_model, resolved_provider), (model, provider))


class TestClassifyFailure(unittest.TestCase):

    def test_each_gateway_error_maps_to_its_category(self):
        cases = [
            (AUTH_ERROR, connection.AUTHENTICATION_FAILED),
            (PROXY_MODEL_DENIED, connection.MODEL_NOT_FOUND),
            (AZURE_DEPLOYMENT_MISSING, connection.MODEL_NOT_FOUND),
            (ROUTE_NOT_FOUND, connection.WRONG_PROTOCOL_OR_ROUTE),
            (METHOD_NOT_ALLOWED, connection.WRONG_PROTOCOL_OR_ROUTE),
            (VERTEX_MODEL_MISSING, connection.MODEL_NOT_FOUND),
            (ANTHROPIC_MODEL_MISSING, connection.MODEL_NOT_FOUND),
            (CONNECTION_ERROR, connection.CONNECTION_FAILED),
            (LITELLM_TIMEOUT, connection.TIMED_OUT),
            (GATEWAY_READ_TIMEOUT, connection.TIMED_OUT),
            (RATE_LIMITED, connection.RATE_LIMITED),
            ("", connection.CONNECTION_FAILED),
            (None, connection.CONNECTION_FAILED),
        ]
        for error, category in cases:
            with self.subTest(category=category, error=(error or "")[:40]):
                self.assertEqual(connection.classify_failure(error)[0], category)

    def test_the_provider_message_loses_litellm_class_prefixes(self):
        self.assertEqual(
            connection.classify_failure(AZURE_DEPLOYMENT_MISSING)[1][:52],
            "The API deployment for this resource does not exist.",
        )
        self.assertEqual(connection.classify_failure(CONNECTION_ERROR)[1], "Connection error.")

    def test_an_error_relayed_by_an_upstream_litellm_proxy_loses_every_prefix(self):
        relayed = (
            "litellm.BadRequestError: OpenAIException - litellm.BadRequestError: BedrockException - "
            "{\"message\":\"Invalid 'max_output_tokens': integer below minimum value.\"}"
        )
        self.assertTrue(connection.classify_failure(relayed)[1].startswith("{\"message\":\"Invalid 'max_output_tokens'"))

    def test_a_timeout_word_in_stack_trace_source_does_not_mean_timed_out(self):
        self.assertEqual(connection.classify_failure(CONNECTION_ERROR)[0], connection.CONNECTION_FAILED)

    def test_messages_carry_no_stack_trace_key_hash_or_address(self):
        for error in (AUTH_ERROR, PROXY_MODEL_DENIED, CONNECTION_ERROR, GATEWAY_READ_TIMEOUT):
            with self.subTest(error=error[:40]):
                message = connection.classify_failure(error)[1]
                self.assertNotIn("stack trace", message)
                self.assertNotIn("Traceback", message)
                self.assertNotIn("be77445bb1d0d820085396d1487dac19", message)
                self.assertNotIn("10.255.255.1", message)
                self.assertNotIn("127.0.0.1", message)


class TestScrub(unittest.TestCase):

    def test_urls_addresses_and_tokens_are_removed(self):
        scrubbed = connection.scrub(
            "POST https://dial.internal.example/openai/v1/responses failed via 10.0.3.7:8080 "
            "with Bearer abc.def api-key=0123 key sk-proj-AbCdEf123 hash 0123456789abcdef0123456789abcdef"
        )
        for leaked in ("dial.internal.example", "10.0.3.7", "abc.def", "0123 ", "sk-proj", "0123456789abcdef"):
            self.assertNotIn(leaked, scrubbed)

    def test_library_internals_are_removed(self):
        scrubbed = connection.scrub(
            "Cannot connect to host dial.example:443 ssl:<ssl.SSLContext object at 0xffff7ca234d0> "
            "[Name or service not known]. Connection error.. Handle with `litellm.InternalServerError`."
        )
        self.assertNotIn("0xffff7ca234d0", scrubbed)
        self.assertNotIn("SSLContext", scrubbed)
        self.assertNotIn("litellm.InternalServerError", scrubbed)
        self.assertNotIn("..", scrubbed)
        self.assertIn("Name or service not known", scrubbed)

    def test_long_model_names_survive(self):
        self.assertIn(
            "anthropic-claude-3-5-sonnet-20241022-v2",
            connection.scrub("Unknown model anthropic-claude-3-5-sonnet-20241022-v2"),
        )

    def test_explicit_secrets_are_replaced(self):
        self.assertNotIn("hunter2", connection.scrub("bad key hunter2", secrets=("hunter2",)))

    def test_messages_are_bounded(self):
        self.assertLessEqual(len(connection.scrub("word " * 200)), connection.MAX_MESSAGE_LENGTH)


class TestRunConnectionTest(unittest.TestCase):

    def test_success_returns_latency_without_a_success_key(self):
        gateway = FakeGateway()
        result = connection.run_connection_test(_mapped(), gateway)
        self.assertIn("latency_ms", result)
        self.assertNotIn("success", result)

    def test_the_health_call_uses_the_test_params(self):
        gateway = FakeGateway()
        connection.run_connection_test(_mapped(), gateway)
        [(_, _, kwargs)] = gateway.health_calls()
        self.assertEqual(kwargs["mode"], "chat")
        self.assertEqual(kwargs["litellm_params"]["model"], "openai/gpt-4o")

    def test_the_credential_is_looked_up_by_name_with_a_short_timeout(self):
        gateway = FakeGateway()
        connection.run_connection_test(_mapped(), gateway)
        [(endpoint, service_timeout, kwargs)] = [call for call in gateway.calls if call[0] != "health_test_connection"]
        self.assertEqual(endpoint, "credential_exists")
        self.assertEqual(kwargs["credential_name"], CREDENTIAL_NAME)
        self.assertLess(kwargs["timeout"], service_timeout)
        self.assertLessEqual(service_timeout, 5)

    def test_both_calls_share_one_deadline_and_each_hop_gives_up_before_the_one_above(self):
        clock = iter([100.0, 104.0])
        gateway = FakeGateway()
        with mock.patch.object(connection.time, "monotonic", lambda: next(clock, 104.0)):
            connection.run_connection_test(_mapped(), gateway)
        [(_, service_timeout, kwargs)] = gateway.health_calls()
        provider_timeout = kwargs["litellm_params"]["timeout"]
        self.assertEqual(service_timeout, connection.TEST_DEADLINE_SECONDS - 4)
        self.assertLess(provider_timeout, kwargs["timeout"])
        self.assertLess(kwargs["timeout"], service_timeout)

    def test_an_unregistered_credential_never_reaches_the_provider(self):
        gateway = FakeGateway(credentials=["7_other"])
        result = connection.run_connection_test(_mapped(), gateway)
        self.assertEqual(gateway.health_calls(), [])
        self.assertEqual(result["success"], False)
        self.assertTrue(result["message"].startswith(connection.CONNECTION_FAILED))

    def test_unmappable_settings_fail_without_gateway_calls(self):
        gateway = FakeGateway()
        result = connection.run_connection_test(None, gateway)
        self.assertEqual(gateway.calls, [])
        self.assertTrue(result["message"].startswith(connection.CONNECTION_FAILED))

    def test_a_provider_error_becomes_category_and_message(self):
        gateway = FakeGateway(health_result={"status": "error", "result": {"error": AZURE_DEPLOYMENT_MISSING}})
        result = connection.run_connection_test(_mapped(), gateway)
        self.assertEqual(result["success"], False)
        self.assertTrue(result["message"].startswith("Model not found: The API deployment"))

    def test_a_gateway_exception_is_classified_not_leaked(self):
        gateway = FakeGateway(raises={"health_test_connection": RuntimeError(GATEWAY_READ_TIMEOUT)})
        result = connection.run_connection_test(_mapped(), gateway)
        self.assertEqual(result["message"], f"{connection.TIMED_OUT}: {connection.TIMED_OUT_MESSAGE}")

    def test_the_credential_key_is_redacted_before_the_message_is_bounded(self):
        key = "AZUREKEY-" + "x" * 20
        error = "litellm.AuthenticationError: AuthenticationError: AzureException - " + "w " * 140 + f"bad key {key}"
        gateway = FakeGateway(health_result={"status": "error", "result": {"error": error}})
        result = connection.run_connection_test(
            _mapped(), gateway, connection.credential_secrets({"api_key": key, "api_base": ""}),
        )
        self.assertNotIn("AZUREKEY", result["message"])

    def test_credential_secrets_cover_key_base_and_host(self):
        self.assertEqual(
            connection.credential_secrets({"api_key": "k-123456", "api_base": "https://dial.corp.example/v1"}),
            ["https://dial.corp.example/v1", "dial.corp.example", "k-123456"],
        )

    def test_a_failing_credential_lookup_is_classified_not_leaked(self):
        gateway = FakeGateway(raises={"credential_exists": RuntimeError("Traceback ...\nConnectionError: http://x")})
        result = connection.run_connection_test(_mapped(), gateway)
        self.assertEqual(gateway.health_calls(), [])
        self.assertNotIn("http://x", result["message"])


class TestRpc(unittest.TestCase):

    def test_the_mapped_row_uses_the_credential_project_and_the_form_values(self):
        seen = {}

        def configuration_to_model(row):
            seen.update(row)
            return open_ai.to_model(row)

        module = types.SimpleNamespace(configuration_to_model=configuration_to_model, service_node=None)
        gateway = FakeGateway()
        module.service_node = types.SimpleNamespace(call=gateway)
        settings = _expanded_row("unsaved-name")["data"]
        gateway.health_result = {
            "status": "error", "result": {"error": "litellm.APIError: host dial.internal.example refused"},
        }
        failure_result = connection_rpc.RPC.litellm_test_llm_model_connection(module, settings)
        self.assertNotIn("dial.internal.example", failure_result["message"])
        gateway.health_result = {"status": "success", "result": {}}
        result = connection_rpc.RPC.litellm_test_llm_model_connection(module, settings)
        self.assertEqual(seen["project_id"], 7)
        self.assertIs(seen["data"], settings)
        self.assertIn("latency_ms", result)
        self.assertEqual(gateway.health_calls()[0][2]["litellm_params"]["model"], "openai/unsaved-name")


if __name__ == "__main__":
    unittest.main()
