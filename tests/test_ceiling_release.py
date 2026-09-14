"""Unit tests for the legacy LiteLLM budget-ceiling release (issue #6573).

Enforcement moved to the `usage` plugin, so any `elitea_proj_*` ceiling still set in
LiteLLM would keep blocking calls with nothing left here able to lift it. The release
runs once per start and must be idempotent.

Run standalone (no pylon runtime needed):
    python3 tests/test_ceiling_release.py
"""

import os
import sys
import types
import unittest


def _stub_pylon():
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
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("pylon.core.tools.web", web_stub),
    ]:
        sys.modules.setdefault(name, mod)


_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_stub_pylon()
sys.path.insert(0, os.path.join(_PLUGIN_ROOT, "methods"))
import ceilings  # noqa: E402  pylint: disable=C0413
sys.path.remove(os.path.join(_PLUGIN_ROOT, "methods"))


class FakeCalls:
    """Records `litellm_api_call` invocations and answers `tag_list` from a canned list."""

    def __init__(self, tags=None, fail_on=None):
        self.calls = []
        self._tags = tags if tags is not None else []
        self._fail_on = fail_on or set()

    def litellm_api_call(self, endpoint, *args, **kwargs):
        self.calls.append((endpoint, args, kwargs))
        if endpoint in self._fail_on:
            raise RuntimeError(f"boom: {endpoint}")
        if endpoint == "tag_list":
            return self._tags
        return None

    def updated(self):
        return [c for c in self.calls if c[0] == "tag_update_if_exists"]


def _tag(name, max_budget):
    return {"name": name, "litellm_budget_table": {"max_budget": max_budget}}


def _method(tags=None, fail_on=None):
    calls = FakeCalls(tags=tags, fail_on=fail_on)
    method = ceilings.Method()
    method.service_node = types.SimpleNamespace(call=calls)
    return method, calls


class TestReleaseBudgetCeilings(unittest.TestCase):

    def test_a_capped_elitea_tag_is_lifted_to_unlimited(self):
        method, calls = _method([_tag("elitea_proj_25_202609", 5.0)])
        #
        self.assertEqual(method.release_budget_ceilings(), 1)
        self.assertEqual(
            calls.updated(),
            [("tag_update_if_exists", ("elitea_proj_25_202609",),
              {"max_budget": ceilings.UNLIMITED_BUDGET})],
        )

    def test_running_twice_is_a_no_op_the_second_time(self):
        # Second run sees the already-lifted value; idempotence is what makes a
        # per-start release safe.
        method, calls = _method([_tag("elitea_proj_25_202609", ceilings.UNLIMITED_BUDGET)])
        #
        self.assertEqual(method.release_budget_ceilings(), 0)
        self.assertEqual(calls.updated(), [])

    def test_tags_owned_by_someone_else_are_left_alone(self):
        method, calls = _method([_tag("customer_acme", 1.0), _tag("elitea_other", 2.0)])
        #
        self.assertEqual(method.release_budget_ceilings(), 0)
        self.assertEqual(calls.updated(), [])

    def test_a_tag_with_no_ceiling_is_skipped(self):
        method, calls = _method([_tag("elitea_proj_3_202609", None), {"name": "elitea_proj_4_202609"}])
        #
        self.assertEqual(method.release_budget_ceilings(), 0)
        self.assertEqual(calls.updated(), [])

    def test_one_failing_update_does_not_stop_the_rest(self):
        method, calls = _method(
            [_tag("elitea_proj_1_202609", 1.0), _tag("elitea_proj_2_202609", 2.0)],
            fail_on={"tag_update_if_exists"},
        )
        #
        self.assertEqual(method.release_budget_ceilings(), 0)
        self.assertEqual(len(calls.updated()), 2)

    def test_an_unreachable_litellm_is_reported_as_zero_released(self):
        method, _ = _method(fail_on={"tag_list"})
        #
        self.assertEqual(method.release_budget_ceilings(), 0)

    def test_scheduling_runs_the_release_on_a_daemon_thread(self):
        method, calls = _method([_tag("elitea_proj_9_202609", 3.0)])
        ceilings.RELEASE_DELAY_SECONDS = 0
        #
        method.schedule_budget_ceiling_release()
        for thread in __import__("threading").enumerate():
            if thread.name == "litellm-ceiling-release":
                self.assertTrue(thread.daemon)
                thread.join(timeout=5)
        #
        self.assertEqual(len(calls.updated()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
