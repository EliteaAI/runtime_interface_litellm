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

    def __init__(self, explode=False, served="metered", denial=None):
        self.explode = explode
        self.served = served
        self.denial = denial
        self.prepared = []
        self.metered = []

    def prepare_llm_call(self, proxy_target, proxy_auth, raw_model_name, model_project_id):
        if self.explode:
            raise RuntimeError("usage is broken")
        self.prepared.append((proxy_target, proxy_auth, raw_model_name, model_project_id))
        #
        return self.denial

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

    def test_prepare_preserves_usage_admission_denial(self):
        denied = ({'error': 'project_budget_exceeded'}, 429)
        hooks = types.SimpleNamespace(prepare_llm_call=lambda *a: denied)
        with HooksInstalled(hooks):
            self.assertIs(metering.prepare_llm_call({}, {}, 'chosen', 7), denied)


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


class TestMapModelNameScope(unittest.TestCase):
    """_map_model_name only reports OWN on a positive {project}_{model} hit."""

    @staticmethod
    def _method(known):
        method = proxy.Method()
        method.service_node = types.SimpleNamespace(call=types.SimpleNamespace(
            litellm_api_call=lambda _op, name: known(name),
        ))
        return method

    def test_own_shared_and_unresolved(self):
        method = self._method(lambda name: {"model_group": name} if name in {"7_a", "1_b"} else None)
        #
        self.assertEqual(method._map_model_name("a", 7, 1), ("7_a", proxy.MODEL_SCOPE_OWN))
        self.assertEqual(method._map_model_name("b", 7, 1), ("1_b", proxy.MODEL_SCOPE_SHARED))
        self.assertEqual(method._map_model_name("c", 7, 1), ("c", proxy.MODEL_SCOPE_UNRESOLVED))

    def test_a_failed_lookup_is_unresolved_not_own(self):
        # A falsy model_group_info (timeout, error) must never read as project-own
        method = self._method(lambda name: None)
        #
        self.assertEqual(method._map_model_name("a", 7, 1), ("a", proxy.MODEL_SCOPE_UNRESOLVED))

    def test_the_public_project_calling_its_own_model_is_own(self):
        method = self._method(lambda name: {"model_group": name} if name == "1_a" else None)
        #
        self.assertEqual(method._map_model_name("a", 1, 1), ("1_a", proxy.MODEL_SCOPE_OWN))


class TestTheDenialShortCircuit(unittest.TestCase):
    """A refused call must be answered here, before LiteLLM is ever dialled."""

    DENIAL = ({"error": {"code": "budget_exceeded"}}, 429)

    def test_a_denial_is_returned_verbatim(self):
        with HooksInstalled(RecordingHooks(denial=self.DENIAL)):
            served = metering.prepare_llm_call({"endpoint": "/v1/chat/completions"}, {}, "gpt-4o", 1)
        #
        self.assertIs(served, self.DENIAL)

    def test_an_admitted_call_proceeds(self):
        with HooksInstalled(RecordingHooks()):
            self.assertIsNone(metering.prepare_llm_call({}, {}, "gpt-4o", 1))

    def test_a_broken_hook_proceeds_rather_than_denying(self):
        # Fail-closed lives inside the usage plugin, which owns the mode; a failure out here is
        # a bug in the plumbing, and refusing every call over it would be worse
        with HooksInstalled(RecordingHooks(explode=True)):
            self.assertIsNone(metering.prepare_llm_call({}, {}, "gpt-4o", 1))

    def test_no_usage_plugin_never_denies(self):
        self.assertIsNone(metering.prepare_llm_call({}, {}, "gpt-4o", 1))


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

    def _prepare(self, is_shared, body=None, data=None, expected_response=None, scope=None):
        method = proxy.Method()
        method.preprocess_headers = lambda headers: headers
        method.descriptor = types.SimpleNamespace(
            config=types.SimpleNamespace(get=lambda *a, **kw: None),
        )
        method.get_public_project_id = lambda: 1
        if scope is None:
            scope = proxy.MODEL_SCOPE_SHARED if is_shared else proxy.MODEL_SCOPE_OWN
        method._map_model_name = lambda raw, project_id, public_id: (
            raw if scope == proxy.MODEL_SCOPE_UNRESOLVED
            else f"{1 if scope == proxy.MODEL_SCOPE_SHARED else project_id}_{raw}",
            scope,
        )
        #
        proxy_target = {
            "endpoint": "/v1/chat/completions",
            "headers": proxy.Headers({}),
            "json": {"model": "gpt-4o"} if body is None else body,
            "data": data,
        }
        proxy_auth = {"type": "token", "user": {"id": 42, "name": "someone"}}
        #
        self.assertIs(method.prepare_request(proxy_target, proxy_auth), expected_response)
        #
        return proxy_target, proxy_auth

    def test_usage_denial_reaches_existing_route_early_return(self):
        denied = ({'error': 'Usage accounting is temporarily unavailable'}, 503)
        proxy.prepare_llm_call = lambda *a: denied
        self._prepare(is_shared=True, expected_response=denied)

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

    def test_an_own_model_is_budget_exempt(self):
        # BYO: the provider bills the customer, so the shared-model budget must not gate it
        _, proxy_auth = self._prepare(is_shared=False)
        #
        self.assertIs(proxy_auth[proxy.BUDGET_EXEMPT_AUTH_KEY], True)
        self.assertIs(self.handed[0][1], proxy_auth)

    def test_a_shared_model_is_budgeted(self):
        _, proxy_auth = self._prepare(is_shared=True)
        #
        self.assertIs(proxy_auth[proxy.BUDGET_EXEMPT_AUTH_KEY], False)

    def test_no_model_in_the_body_parks_nothing(self):
        # Nothing resolved, nothing to exempt: the usage plugin's default (counted) applies
        _, proxy_auth = self._prepare(is_shared=False, body={"input": "x"})
        #
        self.assertNotIn(proxy.BUDGET_EXEMPT_AUTH_KEY, proxy_auth)

    def test_an_unresolved_model_is_budgeted(self):
        # The raw-name fallback (externally managed, or a failed model_group_info lookup) is
        # not a positive own match: exempting it would let any unprefixed platform-funded
        # model in LiteLLM bypass the shared budget, and fail open on a lookup timeout.
        proxy_target, proxy_auth = self._prepare(
            is_shared=False, scope=proxy.MODEL_SCOPE_UNRESOLVED,
        )
        #
        self.assertEqual(proxy_target["json"]["model"], "gpt-4o")
        self.assertIs(proxy_auth[proxy.BUDGET_EXEMPT_AUTH_KEY], False)
        self.assertEqual(self.handed[0][3], 7)  # still metered in the caller's project

    def test_a_form_data_unresolved_model_is_budgeted(self):
        _, proxy_auth = self._prepare(
            is_shared=False, body={}, data=FormData({"model": "gpt-image-1"}),
            scope=proxy.MODEL_SCOPE_UNRESOLVED,
        )
        #
        self.assertIs(proxy_auth[proxy.BUDGET_EXEMPT_AUTH_KEY], False)

    def test_json_and_form_models_resolve_to_one_decision(self):
        # Both branches feed one decision after the fact, so the metered model and the
        # exemption can never come from different lookups
        scopes = iter([proxy.MODEL_SCOPE_OWN, proxy.MODEL_SCOPE_SHARED])
        method_scopes = []
        #
        def prepare_with_two_lookups():
            method = proxy.Method()
            method.preprocess_headers = lambda headers: headers
            method.descriptor = types.SimpleNamespace(
                config=types.SimpleNamespace(get=lambda *a, **kw: None),
            )
            method.get_public_project_id = lambda: 1
            #
            def mapped(raw, project_id, public_id):
                scope = next(scopes)
                method_scopes.append(scope)
                return f"x_{raw}", scope
            #
            method._map_model_name = mapped
            proxy_auth = {"type": "token", "user": {"id": 42, "name": "someone"}}
            method.prepare_request({
                "endpoint": "/v1/images/edits", "headers": proxy.Headers({}),
                "json": {"model": "gpt-4o"}, "data": FormData({"model": "gpt-image-1"}),
            }, proxy_auth)
            return proxy_auth
        #
        proxy_auth = prepare_with_two_lookups()
        #
        # The form-data model is the one metered (last wins), and the exemption follows it
        self.assertEqual(method_scopes, [proxy.MODEL_SCOPE_OWN, proxy.MODEL_SCOPE_SHARED])
        self.assertEqual(self.handed[0][2], "gpt-image-1")
        self.assertEqual(self.handed[0][3], 1)
        self.assertIs(proxy_auth[proxy.BUDGET_EXEMPT_AUTH_KEY], False)

    def test_a_form_data_own_model_is_budget_exempt(self):
        _, proxy_auth = self._prepare(
            is_shared=False, body={}, data=FormData({"model": "gpt-image-1"}),
        )
        #
        self.assertIs(proxy_auth[proxy.BUDGET_EXEMPT_AUTH_KEY], True)

    def test_the_key_matches_what_the_usage_plugin_reads(self):
        import pathlib  # pylint: disable=C0415
        source = (pathlib.Path(proxy.__file__).parents[2] / "usage" / "interface.py")
        if not source.exists():
            self.skipTest("usage plugin not checked out next to this one")
        self.assertIn(f'BUDGET_EXEMPT_AUTH_KEY = "{proxy.BUDGET_EXEMPT_AUTH_KEY}"',
                      source.read_text())

    def test_a_denial_is_handed_back_out_of_prepare_request(self):
        # routes/proxy.py short-circuits on a non-None return before add_stream(), so the
        # refusal is what the caller sees and no stream is ever opened
        denial = ({"error": {"code": "budget_exceeded"}}, 429)
        proxy.prepare_llm_call = lambda *args: denial
        method = proxy.Method()
        method.preprocess_headers = lambda headers: headers
        method.descriptor = types.SimpleNamespace(
            config=types.SimpleNamespace(get=lambda *a, **kw: None),
        )
        method.get_public_project_id = lambda: 1
        method._map_model_name = lambda raw, project_id, public_id: (
            raw, proxy.MODEL_SCOPE_UNRESOLVED,
        )
        #
        served = method.prepare_request(
            {
                "endpoint": "/v1/chat/completions", "headers": proxy.Headers({}),
                "json": {"model": "gpt-4o"}, "data": None,
            },
            {"type": "token", "user": {"id": 42, "name": "someone"}},
        )
        #
        self.assertIs(served, denial)

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
        method._map_model_name = lambda raw, project_id, public_id: (
            raw, proxy.MODEL_SCOPE_UNRESOLVED,
        )
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
