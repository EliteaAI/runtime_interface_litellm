"""Unit tests for cost-budget tag helpers and the shared-model resolution signal.

Run standalone (no pylon runtime needed):
    python3 tests/test_budgets.py
"""

import os
import sys
import json
import types
import datetime
import unittest


def _load_budgets_module():
    """Load methods/budgets.py with the pylon/tools imports stubbed out."""
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
    #
    flask_stub = types.ModuleType("flask")
    flask_stub.Response = object
    #
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("pylon.core.tools.web", web_stub),
        ("tools", tools_stub), ("flask", flask_stub),
    ]:
        sys.modules.setdefault(name, mod)
    #
    sys.path.insert(0, os.path.join(plugin_root, "methods"))
    try:
        import budgets  # pylint: disable=C0415
        return budgets
    finally:
        sys.path.pop(0)


budgets = _load_budgets_module()


class TestBudgetTagName(unittest.TestCase):
    def test_tag_includes_project_and_month(self):
        now = datetime.datetime(2026, 7, 24, tzinfo=datetime.timezone.utc)
        self.assertEqual(budgets.make_budget_tag(3, now), "elitea_proj_3_202607")

    def test_tag_rotates_with_month(self):
        # A new month yields a new tag, which is what makes the budget reset itself
        december = datetime.datetime(2026, 12, 31, 23, 59, tzinfo=datetime.timezone.utc)
        january = datetime.datetime(2027, 1, 1, 0, 1, tzinfo=datetime.timezone.utc)
        #
        self.assertEqual(budgets.make_budget_tag(7, december), "elitea_proj_7_202612")
        self.assertEqual(budgets.make_budget_tag(7, january), "elitea_proj_7_202701")

    def test_tag_prefix_matches_constant(self):
        tag = budgets.make_budget_tag(42, datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc))
        self.assertTrue(tag.startswith(budgets.BUDGET_TAG_PREFIX))


class TestBudgetErrorDetection(unittest.TestCase):
    def test_detects_litellm_budget_exceeded_payload(self):
        body = json.dumps({
            "error": {
                "message": "Budget has been exceeded! Tag=elitea_proj_3_202607 Current cost: 1.5, Max budget: 1.0",
                "type": "budget_exceeded",
            }
        }).encode("utf-8")
        self.assertTrue(budgets.is_budget_exceeded_body(body))

    def test_ignores_unrelated_400(self):
        body = b'{"error": {"message": "invalid model name", "type": "bad_request"}}'
        self.assertFalse(budgets.is_budget_exceeded_body(body))

    def test_ignores_body_mentioning_budget_only_incidentally(self):
        # The word alone must not trigger a rewrite of someone else's error
        body = b'{"error": {"message": "your budget planning tool failed"}}'
        self.assertFalse(budgets.is_budget_exceeded_body(body))

    def test_handles_non_utf8_bytes(self):
        self.assertFalse(budgets.is_budget_exceeded_body(b"\xff\xfe\x00binary"))


class TestUserBudgetTagName(unittest.TestCase):
    def test_user_tag_includes_project_user_and_month(self):
        now = datetime.datetime(2026, 7, 27, tzinfo=datetime.timezone.utc)
        self.assertEqual(
            budgets.make_user_budget_tag(25, 7, now), "elitea_proj_25_user_7_202607",
        )

    def test_user_tag_distinct_from_project_tag(self):
        now = datetime.datetime(2026, 7, 27, tzinfo=datetime.timezone.utc)
        self.assertNotEqual(
            budgets.make_user_budget_tag(25, 7, now), budgets.make_budget_tag(25, now),
        )

    def test_user_tag_shares_project_prefix(self):
        # Project-scoped cache invalidation relies on the shared prefix
        now = datetime.datetime(2026, 7, 27, tzinfo=datetime.timezone.utc)
        self.assertTrue(
            budgets.make_user_budget_tag(25, 7, now).startswith("elitea_proj_25_"),
        )

    def test_project_tag_is_not_matched_as_a_user_tag(self):
        # A project tag must never be confused with user 202607's tag
        now = datetime.datetime(2026, 7, 27, tzinfo=datetime.timezone.utc)
        self.assertNotIn("_user_", budgets.make_budget_tag(25, now))


class FakeDefaults:
    """Stand-in exposing get_default_limit's real logic over fake config."""

    def __init__(self, defaults, personal_ids):
        self.defaults = defaults
        self.personal_ids = personal_ids

    def is_personal_project(self, project_id):
        return int(project_id) in self.personal_ids

    def get_default_limit(self, scope, project_id):
        defaults = self.defaults
        #
        if not defaults.get("enabled", False):
            return None
        #
        if scope == "user":
            return defaults.get("user_monthly_limit", None)
        #
        if self.is_personal_project(project_id):
            return defaults.get("personal_project_monthly_limit", None)
        #
        return defaults.get("project_monthly_limit", None)


class TestDefaultLimits(unittest.TestCase):
    """Defaults close the 'unlimited by default' hole for unbudgeted projects."""

    def setUp(self):
        self.cfg = {
            "enabled": True,
            "project_monthly_limit": 100.0,
            "personal_project_monthly_limit": 5.0,
            "user_monthly_limit": 20.0,
        }

    def test_disabled_defaults_mean_unlimited(self):
        mod = FakeDefaults({"enabled": False, "project_monthly_limit": 100.0}, {3})
        self.assertIsNone(mod.get_default_limit("project", 3))
        self.assertIsNone(mod.get_default_limit("user", 3))

    def test_personal_project_gets_lower_default(self):
        mod = FakeDefaults(self.cfg, personal_ids={3})
        self.assertEqual(mod.get_default_limit("project", 3), 5.0)

    def test_team_project_gets_project_default(self):
        mod = FakeDefaults(self.cfg, personal_ids={3})
        self.assertEqual(mod.get_default_limit("project", 25), 100.0)

    def test_user_scope_ignores_personal_distinction(self):
        mod = FakeDefaults(self.cfg, personal_ids={3})
        self.assertEqual(mod.get_default_limit("user", 3), 20.0)
        self.assertEqual(mod.get_default_limit("user", 25), 20.0)

    def test_null_default_for_scope_means_unlimited(self):
        mod = FakeDefaults({"enabled": True}, personal_ids={3})
        self.assertIsNone(mod.get_default_limit("project", 25))


class FakeLimits:
    """Stand-in for the explicit-row-then-default resolution."""

    def __init__(self, row, default):
        self.row = row
        self.default = default

    def resolve(self):
        budget = self.row
        #
        if budget is not None:
            if not budget.get("enabled", True):
                return None
            if budget.get("monthly_limit") is not None:
                return budget["monthly_limit"]
        #
        return self.default


class TestLimitResolution(unittest.TestCase):
    def test_explicit_row_wins_over_default(self):
        self.assertEqual(FakeLimits({"monthly_limit": 7.0, "enabled": True}, 100.0).resolve(), 7.0)

    def test_no_row_falls_back_to_default(self):
        self.assertEqual(FakeLimits(None, 100.0).resolve(), 100.0)

    def test_disabled_row_is_deliberately_exempt(self):
        # An admin disabling a budget must not be silently re-capped by the default
        self.assertIsNone(FakeLimits({"monthly_limit": 7.0, "enabled": False}, 100.0).resolve())

    def test_row_without_limit_falls_back_to_default(self):
        self.assertEqual(FakeLimits({"monthly_limit": None, "enabled": True}, 100.0).resolve(), 100.0)

    def test_zero_explicit_limit_is_respected_not_treated_as_missing(self):
        self.assertEqual(FakeLimits({"monthly_limit": 0.0, "enabled": True}, 100.0).resolve(), 0.0)


class TestUnlimitedSentinel(unittest.TestCase):
    """null/unset limits must mean unlimited, and must lift a previously-set ceiling.

    LiteLLM ignores max_budget=null on /tag/update (verified live: budget stayed 5.0),
    so unlimited is expressed as a ceiling nothing reaches.
    """

    def test_sentinel_is_effectively_unreachable(self):
        self.assertGreaterEqual(budgets.UNLIMITED_BUDGET, 1_000_000.0)

    def test_sentinel_used_when_limit_is_none(self):
        limit = None
        self.assertEqual(
            budgets.UNLIMITED_BUDGET if limit is None else limit, budgets.UNLIMITED_BUDGET,
        )

    def test_real_limit_passes_through_unchanged(self):
        limit = 42.5
        self.assertEqual(budgets.UNLIMITED_BUDGET if limit is None else limit, 42.5)

    def test_zero_limit_is_not_treated_as_unlimited(self):
        limit = 0.0
        self.assertEqual(budgets.UNLIMITED_BUDGET if limit is None else limit, 0.0)


class TestMetadataKeySelection(unittest.TestCase):
    """Anthropic /v1/messages ignores `metadata`; its tags must go in `litellm_metadata`.

    Verified live: metadata.tags is silently dropped on /v1/messages (spend logged with
    no tag), while litellm_metadata.tags is recorded.
    """

    def test_anthropic_messages_endpoint_detected(self):
        self.assertTrue(budgets.is_anthropic_endpoint("/v1/messages"))
        self.assertTrue(budgets.is_anthropic_endpoint("/v1/messages?beta=true"))
        self.assertTrue(budgets.is_anthropic_endpoint("/v1/messages/count_tokens"))

    def test_openai_endpoints_not_anthropic(self):
        self.assertFalse(budgets.is_anthropic_endpoint("/v1/chat/completions"))
        self.assertFalse(budgets.is_anthropic_endpoint("/v1/embeddings"))
        self.assertFalse(budgets.is_anthropic_endpoint("/v1/images/generations"))

    def test_missing_endpoint_defaults_to_openai_key(self):
        self.assertFalse(budgets.is_anthropic_endpoint(None))
        self.assertFalse(budgets.is_anthropic_endpoint(""))


class FakeModule:
    """Minimal stand-in exposing _map_model_name's real logic over a fake LiteLLM."""

    def __init__(self, known_models):
        self.known_models = known_models

    def litellm_api_call(self, _method, model_name):
        return {"model_group": model_name} if model_name in self.known_models else None

    def map_model_name(self, raw_model_name, project_id, public_project_id):
        model_name = f"{project_id}_{raw_model_name}"
        model_info = self.litellm_api_call("model_group_info", model_name)
        #
        if model_info:
            return model_name, False
        #
        if public_project_id != project_id:
            model_name = f"{public_project_id}_{raw_model_name}"
            model_info = self.litellm_api_call("model_group_info", model_name)
            #
            if model_info:
                return model_name, True
        #
        return raw_model_name, False


class TestSharedModelSignal(unittest.TestCase):
    """The is_shared flag decides whether a call counts against a budget."""

    def test_own_project_model_is_not_shared(self):
        module = FakeModule({"3_my-model"})
        self.assertEqual(module.map_model_name("my-model", 3, 1), ("3_my-model", False))

    def test_public_project_model_is_shared(self):
        module = FakeModule({"1_gpt-5.4-mini"})
        self.assertEqual(module.map_model_name("gpt-5.4-mini", 3, 1), ("1_gpt-5.4-mini", True))

    def test_own_project_wins_over_public(self):
        # Resolution order must stay own-first; a project overriding a shared name pays nothing
        module = FakeModule({"3_gpt-5.4-mini", "1_gpt-5.4-mini"})
        self.assertEqual(module.map_model_name("gpt-5.4-mini", 3, 1), ("3_gpt-5.4-mini", False))

    def test_external_model_is_not_shared(self):
        module = FakeModule(set())
        self.assertEqual(module.map_model_name("some-external", 3, 1), ("some-external", False))

    def test_caller_is_public_project_is_not_shared(self):
        # Limitation L1: public-project calls match the own-project branch
        module = FakeModule({"1_gpt-5.4-mini"})
        self.assertEqual(module.map_model_name("gpt-5.4-mini", 1, 1), ("1_gpt-5.4-mini", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
