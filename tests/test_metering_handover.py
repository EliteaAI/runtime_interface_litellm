"""What is left of metering in this plugin: the hand-over, and surviving without usage.

The policy moved to the usage plugin, so there is nothing here to test about billing. What
remains is a contract between two independently versioned repos, and that is exactly what
breaks silently: the facts only this plugin knows must arrive intact, and an older or absent
usage plugin must not turn every LLM call into a 500.

Run standalone (no pylon runtime needed):
    python3 tests/test_metering_handover.py
"""

import os
import sys
import types
import unittest


def _install_stubs():
    """Pylon/flask/werkzeug/tools stubs, returned so a test can reach into `tools`."""
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    #
    pylon = types.ModuleType("pylon")
    pylon_core = types.ModuleType("pylon.core")
    pylon_tools = types.ModuleType("pylon.core.tools")
    #
    log_stub = types.ModuleType("pylon.core.tools.log")
    for name in ("info", "debug", "warning", "error", "exception"):
        setattr(log_stub, name, lambda *a, **kw: None)
    #
    web_stub = types.ModuleType("pylon.core.tools.web")
    web_stub.method = lambda *a, **kw: (lambda func: func)
    #
    pylon_tools.log = log_stub
    pylon_tools.web = web_stub
    #
    tools_stub = types.ModuleType("tools")
    tools_stub.context = types.SimpleNamespace()
    tools_stub.project_constants = {"PROJECT_USER_NAME_PREFIX": "system_user_"}
    tools_stub.VaultClient = object
    tools_stub.this = types.SimpleNamespace()
    #
    flask_stub = types.ModuleType("flask")
    flask_stub.Response = object
    flask_stub.redirect = lambda *a, **kw: None
    #
    headers_pkg = types.ModuleType("werkzeug")
    datastructures_pkg = types.ModuleType("werkzeug.datastructures")
    headers_mod = types.ModuleType("werkzeug.datastructures.headers")

    class HeadersStub(dict):
        """Enough of werkzeug's Headers for the request path: removable keys."""

        def remove(self, key):
            self.pop(key, None)

    headers_mod.Headers = HeadersStub
    #
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("pylon.core.tools.web", web_stub),
        ("tools", tools_stub), ("flask", flask_stub),
        ("werkzeug", headers_pkg), ("werkzeug.datastructures", datastructures_pkg),
        ("werkzeug.datastructures.headers", headers_mod),
    ]:
        sys.modules.setdefault(name, mod)
    #
    package = types.ModuleType("plugin_under_test")
    package.__path__ = [plugin_root]
    sys.modules.setdefault("plugin_under_test", package)
    #
    import importlib  # pylint: disable=C0415
    #
    return (
        sys.modules["tools"],
        importlib.import_module("plugin_under_test.utils.metering"),
        importlib.import_module("plugin_under_test.methods.proxy"),
    )


tools, metering, proxy = _install_stubs()


class FormData(dict):
    """A werkzeug ImmutableMultiDict, near enough: a mapping that is not a dict of scalars.

    The real thing stores every field as a list and returns the first value from .get(), which
    is why dict(form) yields lists while .to_dict() yields the flat mapping the relay needs.
    """

    def __init__(self, fields):
        super().__init__({key: [value] for key, value in fields.items()})

    def get(self, key, default=None):
        values = super().get(key)
        #
        return values[0] if values else default

    def to_dict(self):
        return {key: values[0] for key, values in self.items()}


class RecordingHooks:
    """Stands in for the usage plugin's registered tool."""

    def __init__(self, explode=False, served="metered"):
        self.explode = explode
        self.served = served
        self.prepared = []
        self.metered = []

    def prepare_llm_call(self, proxy_target, proxy_auth, raw_model_name, model_project_id):
        if self.explode:
            raise RuntimeError("usage is broken")
        self.prepared.append((proxy_target, proxy_auth, raw_model_name, model_project_id))

    def meter_llm_call(self, proxy_target, proxy_auth, response, iterator):
        if self.explode:
            raise RuntimeError("usage is broken")
        self.metered.append((proxy_target, proxy_auth, response, iterator))
        #
        return self.served


class HooksInstalled:
    """Context manager putting a usage_hooks tool on the stubbed `tools` module."""

    def __init__(self, hooks):
        self.hooks = hooks

    def __enter__(self):
        tools.usage_hooks = self.hooks
        return self.hooks

    def __exit__(self, *exc):
        del tools.usage_hooks


class TestWithoutTheUsagePlugin(unittest.TestCase):
    """A pylon can be deployed with this interface and no usage plugin at all."""

    def test_no_hooks_are_found(self):
        self.assertIsNone(metering.usage_hooks())

    def test_preparing_is_a_no_op(self):
        proxy_target = {"endpoint": "/v1/chat/completions", "json": {}}
        proxy_auth = {}
        #
        metering.prepare_llm_call(proxy_target, proxy_auth, "gpt-4o", 1)
        #
        self.assertEqual(proxy_auth, {})
        self.assertEqual(proxy_target["json"], {})

    def test_the_iterator_is_served_by_identity(self):
        marker = iter(())
        #
        self.assertIs(metering.meter_llm_call({}, {}, None, marker), marker)


class TestTheHandOver(unittest.TestCase):
    """Both arguments are forwarded verbatim; nothing is interpreted on the way."""

    def test_prepare_forwards_the_facts_unchanged(self):
        proxy_target = {"endpoint": "/v1/chat/completions"}
        proxy_auth = {"project_id": 7}
        #
        with HooksInstalled(RecordingHooks()) as hooks:
            metering.prepare_llm_call(proxy_target, proxy_auth, "gpt-4o", 1)
        #
        target, auth, model, scope = hooks.prepared[0]
        self.assertIs(target, proxy_target)
        self.assertIs(auth, proxy_auth)
        self.assertEqual((model, scope), ("gpt-4o", 1))

    def test_meter_serves_whatever_usage_returns(self):
        with HooksInstalled(RecordingHooks(served="wrapped")) as hooks:
            served = metering.meter_llm_call({}, {}, "response", "iterator")
        #
        self.assertEqual(served, "wrapped")
        self.assertEqual(hooks.metered[0][2:], ("response", "iterator"))


class TestAnOlderOrBrokenUsagePlugin(unittest.TestCase):
    """The repos are versioned independently, so a mismatch must cost nothing but metering."""

    def test_a_failing_prepare_does_not_fail_the_request(self):
        proxy_target = {"endpoint": "/v1/chat/completions"}
        #
        with HooksInstalled(RecordingHooks(explode=True)):
            metering.prepare_llm_call(proxy_target, {}, "gpt-4o", 1)

    def test_a_failing_meter_still_serves_the_response(self):
        marker = iter(())
        #
        with HooksInstalled(RecordingHooks(explode=True)):
            served = metering.meter_llm_call({}, {}, None, marker)
        #
        self.assertIs(served, marker)

    def test_a_usage_plugin_without_the_surface_is_tolerated(self):
        # An older usage plugin registers the tool but has only the internal hooks on it.
        with HooksInstalled(types.SimpleNamespace()):
            marker = iter(())
            metering.prepare_llm_call({}, {}, "gpt-4o", 1)
            #
            self.assertIs(metering.meter_llm_call({}, {}, None, marker), marker)


class TestWhatPrepareRequestHandsOver(unittest.TestCase):
    """The raw model name and the project the model actually resolved in.

    Both are gone by the time the response is metered — the body carries the mapped name, and
    a shared model is billed against the public project, not the caller's. Getting the scope
    wrong sends the provider lookup to a project that does not hold the credential.
    """

    def setUp(self):
        self.handed = []
        self._original = proxy.prepare_llm_call
        proxy.prepare_llm_call = lambda *args: self.handed.append(args)
        #
        self._context = getattr(proxy, "context", None)
        self._this = getattr(proxy, "this", None)
        self._vault = getattr(proxy, "VaultClient", None)
        #
        proxy.context = types.SimpleNamespace(
            rpc_manager=types.SimpleNamespace(
                timeout=lambda _t: types.SimpleNamespace(
                    projects_get_personal_project_id=lambda _user_id: 7,
                ),
            ),
        )
        proxy.this = types.SimpleNamespace(
            descriptor=types.SimpleNamespace(config={"additional_litellm_params": {}}),
        )
        proxy.VaultClient = lambda project_id: types.SimpleNamespace(
            get_secrets=lambda: {"project_llm_key": "sk-project"},
        )

    def tearDown(self):
        proxy.prepare_llm_call = self._original
        proxy.context = self._context
        proxy.this = self._this
        proxy.VaultClient = self._vault

    def _prepare(self, is_shared, body=None, data=None):
        method = proxy.Method()
        method.preprocess_headers = lambda headers: headers
        method.descriptor = types.SimpleNamespace(
            config=types.SimpleNamespace(get=lambda *a, **kw: None),
        )
        method.get_public_project_id = lambda: 1
        method._map_model_name = lambda raw, project_id, public_id: (
            f"{1 if is_shared else project_id}_{raw}", is_shared,
        )
        method.apply_budget_tag = lambda *a, **kw: None
        #
        proxy_target = {
            "endpoint": "/v1/chat/completions",
            "headers": proxy.Headers({}),
            "json": {"model": "gpt-4o"} if body is None else body,
            "data": data,
        }
        proxy_auth = {"type": "token", "user": {"id": 42, "name": "someone"}}
        #
        self.assertIsNone(method.prepare_request(proxy_target, proxy_auth))
        #
        return proxy_target, proxy_auth

    def test_the_raw_name_is_handed_over_not_the_mapped_one(self):
        proxy_target, _ = self._prepare(is_shared=False)
        #
        _, _, raw_model_name, _ = self.handed[0]
        self.assertEqual(raw_model_name, "gpt-4o")
        self.assertEqual(proxy_target["json"]["model"], "7_gpt-4o")

    def test_an_own_model_is_scoped_to_the_callers_project(self):
        self._prepare(is_shared=False)
        #
        self.assertEqual(self.handed[0][3], 7)

    def test_a_shared_model_is_scoped_to_the_public_project(self):
        # The credential lives in the public project; asking the caller's project about the
        # model yields no provider and metering falls back to sniffing.
        self._prepare(is_shared=True)
        #
        self.assertEqual(self.handed[0][3], 1)

    def test_a_form_data_model_is_handed_over_too(self):
        self._prepare(
            is_shared=False, body=None, data={"model": "dall-e-3"},
        )
        #
        _, _, raw_model_name, scope = self.handed[-1]
        self.assertEqual((raw_model_name, scope), ("dall-e-3", 7))

    def test_a_real_multipart_form_is_not_read_as_modelless(self):
        # A multipart request arrives as werkzeug's ImmutableMultiDict, never a dict. An
        # isinstance check reads it as having no model, so image edits went out unmapped and
        # unmetered while the plain-dict test above stayed green.
        proxy_target, _ = self._prepare(
            is_shared=False, body=None, data=FormData({"model": "dall-e-3"}),
        )
        #
        _, _, raw_model_name, scope = self.handed[-1]
        self.assertEqual((raw_model_name, scope), ("dall-e-3", 7))
        self.assertEqual(proxy_target["data"]["model"], "7_dall-e-3")

    def test_a_rewritten_multipart_field_stays_a_scalar(self):
        # dict(multi_dict) copies values as lists, so litellm would receive
        # {"model": ["7_dall-e-3"]} and reject the request.
        proxy_target, _ = self._prepare(
            is_shared=False, body=None,
            data=FormData({"model": "dall-e-3", "size": "1024x1024"}),
        )
        #
        self.assertIsInstance(proxy_target["data"]["model"], str)
        self.assertEqual(proxy_target["data"]["size"], "1024x1024")

    def test_a_multipart_form_with_no_model_is_left_alone(self):
        # /v1/audio/transcriptions posts a file and no model field.
        form = FormData({"file": "audio.mp3"})
        proxy_target, _ = self._prepare(is_shared=False, body={}, data=form)
        #
        self.assertIs(proxy_target["data"], form)
        self.assertEqual(self.handed[-1][2], None)

    def test_a_call_with_no_model_still_reaches_the_hook(self):
        # Nothing to name, but usage still decides whether the call is metered.
        self._prepare(is_shared=False, body={"input": "hello"})
        #
        _, _, raw_model_name, scope = self.handed[0]
        self.assertEqual((raw_model_name, scope), (None, None))

    def test_the_run_id_is_parked_before_the_hook_sees_it(self):
        run_id = "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"
        method = proxy.Method()
        method.preprocess_headers = lambda headers: headers
        method.descriptor = types.SimpleNamespace(
            config=types.SimpleNamespace(get=lambda *a, **kw: None),
        )
        method.get_public_project_id = lambda: 1
        method._map_model_name = lambda raw, project_id, public_id: (raw, False)
        method.apply_budget_tag = lambda *a, **kw: None
        #
        proxy_target = {
            "endpoint": "/v1/chat/completions",
            "headers": proxy.Headers({"X-Elitea-Run-Id": run_id}),
            "json": {"model": "gpt-4o"},
            "data": None,
        }
        proxy_auth = {"type": "token", "user": {"id": 42, "name": "someone"}}
        #
        method.prepare_request(proxy_target, proxy_auth)
        #
        # Parked on proxy_auth and stripped from the outbound headers: by metering time the
        # header no longer exists, so the parked value is the only source left.
        self.assertEqual(proxy_auth[proxy.PLATFORM_RUN_ID_AUTH_KEY], run_id)
        self.assertNotIn("X-Elitea-Run-Id", proxy_target["headers"])


if __name__ == "__main__":
    unittest.main()
