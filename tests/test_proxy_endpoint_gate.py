"""Unit tests for the LLM-endpoint gate in methods/proxy.py.

Regression for #6491: #6486 widened prepare_request/prepare_response to also run for
session ("user") auth, but the swap-in-project-virtual-key block ran for ANY proxied
path — including LiteLLM's own native admin API (e.g. /user/info) — clobbering the
caller's original credential and breaking admin access to LiteLLM management endpoints.

Run standalone (no pylon runtime needed):
    python3 tests/test_proxy_endpoint_gate.py
"""

import os
import sys
import types
import unittest


def _load_proxy_module():
    """Load methods/proxy.py with the pylon/flask/tools imports stubbed out."""
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
    headers_mod.Headers = dict
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
    sys.path.insert(0, os.path.join(plugin_root, "methods"))
    try:
        import proxy  # pylint: disable=C0415
        return proxy
    finally:
        sys.path.pop(0)


proxy = _load_proxy_module()


class TestIsLlmEndpoint(unittest.TestCase):
    def setUp(self):
        self.method = proxy.Method()

    def test_whitelisted_endpoint_is_llm(self):
        self.assertTrue(self.method._is_llm_endpoint("/v1/chat/completions"))

    def test_prefixed_endpoint_is_llm(self):
        self.assertTrue(self.method._is_llm_endpoint("/v1/chat/completions/some-id"))

    def test_native_admin_endpoint_is_not_llm(self):
        self.assertFalse(self.method._is_llm_endpoint("/v2/user/info"))

    def test_check_access_skips_admin_check_for_llm_endpoints(self):
        # No rpc_manager stubbed — if check_access tried an admin-role lookup here it
        # would raise AttributeError instead of returning cleanly.
        result = self.method.check_access(
            {"endpoint": "/v1/chat/completions"}, {"user": {"id": 1}},
        )
        self.assertIsNone(result)

    def test_prepare_request_leaves_native_admin_calls_untouched(self):
        # A session-authenticated admin hitting LiteLLM's own admin API must reach it
        # with the original Authorization header, not a project-scoped virtual key.
        proxy_target = {
            "endpoint": "/v2/user/info",
            "headers": {"Authorization": "Bearer original-admin-cred"},
            "json": None,
            "data": None,
        }
        self.method.preprocess_headers = lambda headers: headers
        self.method.descriptor = types.SimpleNamespace(
            config=types.SimpleNamespace(get=lambda *a, **kw: None),
        )
        result = self.method.prepare_request(
            proxy_target, {"type": "user", "user": {"id": 1, "name": "admin"}},
        )
        self.assertIsNone(result)
        self.assertEqual(proxy_target["headers"]["Authorization"], "Bearer original-admin-cred")


if __name__ == "__main__":
    unittest.main()
