"""Unit tests for the provider-resolution cache in utils/metering.py.

The lookup behind `resolve_provider` runs a Postgres query in `configurations`, so the cache is
the only thing standing between metering and one DB query per LLM call. These tests pin the two
properties that matter: a repeat lookup does not re-query, and a failed lookup neither raises nor
gets remembered as a real answer.

Run standalone (no pylon runtime needed):
    python3 tests/test_metering_cache.py
"""

import os
import sys
import types
import unittest


def _load_metering_module():
    """Load utils/metering.py with the pylon/tools imports stubbed out."""
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
    pylon_tools.log = log_stub
    #
    tools_stub = types.ModuleType("tools")
    tools_stub.context = types.SimpleNamespace()
    #
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("tools", tools_stub),
    ]:
        sys.modules.setdefault(name, mod)
    #
    sys.path.insert(0, os.path.join(plugin_root, "utils"))
    try:
        import metering  # pylint: disable=C0415
        return metering
    finally:
        sys.path.pop(0)


metering = _load_metering_module()


class RecordingRpc:
    """Counts lookups so a cache hit is distinguishable from a repeat query."""

    def __init__(self, provider="ai_dial", explode=False):
        self.provider = provider
        self.explode = explode
        self.calls = []

    def timeout(self, _seconds):
        return self

    def configurations_get_model_provider(self, project_id, model_name):
        self.calls.append((project_id, model_name))
        #
        if self.explode:
            raise RuntimeError("configurations down")
        #
        return self.provider


class TestResolveProvider(unittest.TestCase):
    def setUp(self):
        metering._provider_cache.clear()
        self.rpc = RecordingRpc()
        metering.context.rpc_manager = self.rpc

    def test_first_lookup_queries_and_returns_the_provider(self):
        self.assertEqual(metering.resolve_provider(3, "gpt-4o"), "ai_dial")
        self.assertEqual(self.rpc.calls, [(3, "gpt-4o")])

    def test_a_repeat_lookup_is_served_from_the_cache(self):
        metering.resolve_provider(3, "gpt-4o")
        metering.resolve_provider(3, "gpt-4o")
        #
        self.assertEqual(len(self.rpc.calls), 1)

    def test_the_key_is_project_and_model(self):
        metering.resolve_provider(3, "gpt-4o")
        metering.resolve_provider(4, "gpt-4o")
        metering.resolve_provider(3, "claude")
        #
        self.assertEqual(len(self.rpc.calls), 3)

    def test_an_unknown_model_caches_the_absence(self):
        # None is a legitimate answer for an externally-managed model, and re-asking for it
        # every call is exactly the DB load this cache exists to prevent.
        self.rpc.provider = None
        #
        self.assertIsNone(metering.resolve_provider(3, "who-knows"))
        self.assertIsNone(metering.resolve_provider(3, "who-knows"))
        self.assertEqual(len(self.rpc.calls), 1)

    def test_a_failed_lookup_degrades_to_none_without_raising(self):
        self.rpc.explode = True
        #
        self.assertIsNone(metering.resolve_provider(3, "gpt-4o"))

    def test_a_failed_lookup_is_not_remembered(self):
        self.rpc.explode = True
        metering.resolve_provider(3, "gpt-4o")
        #
        self.rpc.explode = False
        self.assertEqual(metering.resolve_provider(3, "gpt-4o"), "ai_dial")

    def test_the_cache_expires(self):
        cache = metering._provider_cache
        #
        self.assertGreater(cache.maxsize, 0)
        self.assertEqual(cache.ttl, 60)


if __name__ == "__main__":
    unittest.main()
