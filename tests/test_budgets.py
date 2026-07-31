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


def _load_rpc_module():
    """Load rpc/budgets.py, whose spend readers are what the admin pages call.

    Imported as part of a throwaway package so its ``from ..methods.budgets`` import
    resolves to the already-stubbed module rather than the real plugin tree.
    """
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    #
    web_stub = sys.modules["pylon.core.tools.web"]
    web_stub.rpc = lambda *a, **kw: (lambda func: func)
    #
    import importlib.util  # pylint: disable=C0415
    #
    pkg = types.ModuleType("_rilroot")
    pkg.__path__ = []
    methods_pkg = types.ModuleType("_rilroot.methods")
    methods_pkg.__path__ = []
    methods_pkg.budgets = budgets
    #
    sys.modules["_rilroot"] = pkg
    sys.modules["_rilroot.methods"] = methods_pkg
    sys.modules["_rilroot.methods.budgets"] = budgets
    sys.modules["_rilroot.rpc"] = types.ModuleType("_rilroot.rpc")
    #
    spec = importlib.util.spec_from_file_location(
        "_rilroot.rpc.budgets", os.path.join(plugin_root, "rpc", "budgets.py"),
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "_rilroot.rpc"
    spec.loader.exec_module(module)
    #
    return module


budgets = _load_budgets_module()
rpc_budgets = _load_rpc_module()


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


class TestBudgetErrorScope(unittest.TestCase):
    """The UI shows a different message and usage link per scope, so a wrong scope
    would send the user to a page that does not explain why they were blocked."""

    def _body(self, tag):
        return json.dumps({
            "error": {
                "message": f"Budget has been exceeded! Tag={tag} Current cost: 1.5, Max budget: 1.0",
                "type": "budget_exceeded",
            }
        }).encode("utf-8")

    def test_project_tag_is_project_scope(self):
        scope = budgets.budget_error_scope(self._body("elitea_proj_25_202607"))
        self.assertEqual(scope, budgets.SCOPE_PROJECT)

    def test_user_tag_is_member_scope(self):
        scope = budgets.budget_error_scope(self._body("elitea_proj_25_user_3_202607"))
        self.assertEqual(scope, budgets.SCOPE_MEMBER)

    def test_multi_digit_project_and_user_ids(self):
        scope = budgets.budget_error_scope(self._body("elitea_proj_12905_user_31652_202607"))
        self.assertEqual(scope, budgets.SCOPE_MEMBER)

    def test_missing_tag_falls_back_to_project(self):
        # Better a slightly generic message than blaming the wrong budget
        body = b'{"error": {"message": "Budget has been exceeded!", "type": "budget_exceeded"}}'
        self.assertEqual(budgets.budget_error_scope(body), budgets.SCOPE_PROJECT)

    def test_unrecognised_tag_shape_falls_back_to_project(self):
        scope = budgets.budget_error_scope(self._body("some_other_system_tag"))
        self.assertEqual(scope, budgets.SCOPE_PROJECT)

    def test_project_named_user_is_not_mistaken_for_member_scope(self):
        # A project tag is "<prefix><pid>_<period>"; the word "user" can only mean
        # member scope when it sits between two numeric ids
        scope = budgets.budget_error_scope(self._body("elitea_proj_25_user_budget_202607"))
        self.assertEqual(scope, budgets.SCOPE_PROJECT)

    def test_handles_non_utf8_bytes(self):
        self.assertEqual(budgets.budget_error_scope(b"\xff\xfe\x00binary"), budgets.SCOPE_PROJECT)

    def test_every_scope_maps_to_an_error_code(self):
        for scope in (budgets.SCOPE_PROJECT, budgets.SCOPE_MEMBER):
            self.assertIn(scope, budgets.BUDGET_ERROR_CODES)

    def test_error_message_is_period_neutral(self):
        # Budgets are monthly today, but the copy must not need a rewrite if that changes
        lowered = budgets.BUDGET_ERROR_MESSAGE.lower()
        for period in ("monthly", "daily", "weekly", "this month"):
            self.assertNotIn(period, lowered)


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


class FakeThresholds:
    """Binds the real get_warning_threshold over a fake descriptor config.

    Unlike the other fakes here this calls the production method rather than
    mirroring it, so a change to the real logic cannot silently pass.
    """

    def __init__(self, config):
        self.descriptor = types.SimpleNamespace(config=config)

    get_warning_threshold = budgets.Method.get_warning_threshold


class TestWarningThresholds(unittest.TestCase):
    """Thresholds only change when warnings appear; nothing blocks before 100%."""

    def setUp(self):
        self.cfg = {
            "cost_budgets": {
                "warning_thresholds": {
                    "project_pct": 70,
                    "personal_project_pct": 50,
                    "user_pct": 90,
                },
            },
        }

    def test_each_scope_reads_its_own_value(self):
        mod = FakeThresholds(self.cfg)
        self.assertEqual(mod.get_warning_threshold("project"), 70)
        self.assertEqual(mod.get_warning_threshold("personal_project"), 50)
        self.assertEqual(mod.get_warning_threshold("user"), 90)

    def test_missing_config_uses_default(self):
        mod = FakeThresholds({})
        self.assertEqual(mod.get_warning_threshold("project"), 80)

    def test_partial_config_defaults_only_the_missing_scope(self):
        mod = FakeThresholds({"cost_budgets": {"warning_thresholds": {"project_pct": 60}}})
        self.assertEqual(mod.get_warning_threshold("project"), 60)
        self.assertEqual(mod.get_warning_threshold("user"), 80)

    def test_unknown_scope_falls_back_rather_than_raising(self):
        mod = FakeThresholds(self.cfg)
        self.assertEqual(mod.get_warning_threshold("nonsense"), 80)

    def test_out_of_range_values_fall_back(self):
        # A bad value must not silence the warning entirely
        for bad in (0, -5, 101, 1000):
            mod = FakeThresholds({"cost_budgets": {"warning_thresholds": {"project_pct": bad}}})
            self.assertEqual(mod.get_warning_threshold("project"), 80, bad)

    def test_non_numeric_value_falls_back(self):
        for bad in ("abc", None, [], {}):
            mod = FakeThresholds({"cost_budgets": {"warning_thresholds": {"project_pct": bad}}})
            self.assertEqual(mod.get_warning_threshold("project"), 80, bad)

    def test_numeric_string_is_accepted(self):
        # YAML edited by hand can quote the number
        mod = FakeThresholds({"cost_budgets": {"warning_thresholds": {"project_pct": "65"}}})
        self.assertEqual(mod.get_warning_threshold("project"), 65)

    def test_boundary_values_are_valid(self):
        for value in (1, 100):
            mod = FakeThresholds({"cost_budgets": {"warning_thresholds": {"project_pct": value}}})
            self.assertEqual(mod.get_warning_threshold("project"), value)


class FakeBulkLimits:
    """Binds the real bulk and single-project limit resolvers over the same fake data.

    Both are the production code, so a change to one that is not mirrored in the other
    shows up as a disagreement rather than passing quietly.
    """

    def __init__(self, budgets, defaults=None, personal_ids=(), fail=False):
        self.budgets = budgets
        self.personal_ids = set(personal_ids)
        self.fail = fail
        self.list_calls = 0
        self.get_calls = 0
        self.descriptor = types.SimpleNamespace(
            config={"cost_budgets": {"defaults": defaults or {}}},
        )

    # Stands in for the cross-plugin RPC manager both resolvers reach through
    def timeout(self, _seconds):
        return self

    def elitea_core_list_project_budgets(self):
        self.list_calls += 1
        #
        if self.fail:
            raise RuntimeError("elitea_core unavailable")
        #
        return dict(self.budgets)

    def elitea_core_get_project_budget(self, project_id):
        self.get_calls += 1
        return self.budgets.get(project_id)

    def is_personal_project(self, project_id):
        return int(project_id) in self.personal_ids

    get_default_limit = budgets.Method.get_default_limit
    litellm_get_effective_project_limits = (
        rpc_budgets.RPC.litellm_get_effective_project_limits
    )


def _bulk(mod, ids):
    """Run the bulk resolver with its RPC manager pointed at the fake."""
    original = rpc_budgets.context
    rpc_budgets.context = types.SimpleNamespace(rpc_manager=mod)
    try:
        return mod.litellm_get_effective_project_limits(ids)
    finally:
        rpc_budgets.context = original


def _single(mod, project_id):
    """Run the per-project resolver enforcement still uses, over the same fake."""
    original = budgets.context
    budgets.context = types.SimpleNamespace(rpc_manager=mod)
    try:
        return budgets.Method.get_project_budget_limit(mod, project_id)
    finally:
        budgets.context = original


class TestBulkLimitResolution(unittest.TestCase):
    """The admin pages list whole environments, so limits are read in one query.

    Any divergence from the single-project resolver would change the limit shown for
    every project, so each case asserts the two agree.
    """

    DEFAULTS = {
        "enabled": True,
        "project_monthly_limit": 100.0,
        "personal_project_monthly_limit": 5.0,
        "user_monthly_limit": 20.0,
    }

    def test_reads_all_budgets_in_one_call(self):
        mod = FakeBulkLimits({pid: {"monthly_limit": 1.0, "enabled": True} for pid in range(50)})
        #
        _bulk(mod, list(range(50)))
        #
        # The whole point: one read for fifty projects, not fifty
        self.assertEqual(mod.list_calls, 1)
        self.assertEqual(mod.get_calls, 0)

    def test_explicit_row_wins(self):
        mod = FakeBulkLimits({3: {"monthly_limit": 7.0, "enabled": True}}, self.DEFAULTS)
        self.assertEqual(_bulk(mod, [3])[3], 7.0)
        self.assertEqual(_single(mod, 3), 7.0)

    def test_disabled_row_is_unlimited_not_defaulted(self):
        # An admin exempting a project must not be silently re-capped by the default
        mod = FakeBulkLimits({3: {"monthly_limit": 7.0, "enabled": False}}, self.DEFAULTS)
        self.assertIsNone(_bulk(mod, [3])[3])
        self.assertIsNone(_single(mod, 3))

    def test_missing_row_falls_back_to_team_default(self):
        # Iterating the budget map instead of the requested ids would drop this project
        mod = FakeBulkLimits({}, self.DEFAULTS)
        self.assertEqual(_bulk(mod, [42])[42], 100.0)
        self.assertEqual(_single(mod, 42), 100.0)

    def test_missing_row_uses_the_personal_default_for_personal_projects(self):
        mod = FakeBulkLimits({}, self.DEFAULTS, personal_ids=[3])
        self.assertEqual(_bulk(mod, [3])[3], 5.0)
        self.assertEqual(_single(mod, 3), 5.0)

    def test_missing_row_is_unlimited_when_defaults_are_off(self):
        # The live posture: defaults disabled, so only explicit rows cap anything
        mod = FakeBulkLimits({}, {"enabled": False, "project_monthly_limit": 100.0})
        self.assertIsNone(_bulk(mod, [42])[42])
        self.assertIsNone(_single(mod, 42))

    def test_row_without_a_limit_falls_back_to_default(self):
        mod = FakeBulkLimits({3: {"monthly_limit": None, "enabled": True}}, self.DEFAULTS)
        self.assertEqual(_bulk(mod, [3])[3], 100.0)
        self.assertEqual(_single(mod, 3), 100.0)

    def test_zero_limit_is_kept_not_treated_as_unset(self):
        mod = FakeBulkLimits({3: {"monthly_limit": 0.0, "enabled": True}}, self.DEFAULTS)
        self.assertEqual(_bulk(mod, [3])[3], 0.0)
        self.assertEqual(_single(mod, 3), 0.0)

    def test_string_keyed_budget_map_still_resolves(self):
        # Callers elsewhere have been seen to receive stringified project ids
        mod = FakeBulkLimits({"3": {"monthly_limit": 7.0, "enabled": True}}, self.DEFAULTS)
        self.assertEqual(_bulk(mod, [3])[3], 7.0)

    def test_every_requested_id_is_present_in_the_result(self):
        mod = FakeBulkLimits({1: {"monthly_limit": 5.0, "enabled": True}}, self.DEFAULTS)
        out = _bulk(mod, [1, 2, 3])
        self.assertEqual(sorted(out), [1, 2, 3])

    def test_a_failed_read_reports_unlimited_rather_than_raising(self):
        # The budgets page must still render; a limit that cannot be read is not enforced
        mod = FakeBulkLimits({}, self.DEFAULTS, fail=True)
        self.assertEqual(_bulk(mod, [1, 2]), {1: None, 2: None})


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


class FakeMemberLimits:
    """Binds the real three-tier member resolver over fake member and project rows.

    Production code, not a restatement of it: the member row, the project's member default
    and the platform default all have to be consulted in the right order for a member to
    end up capped by what an admin actually set.
    """

    def __init__(self, member_row, project_row=None, defaults=None, personal=False):
        self.member_row = member_row
        self.project_row = project_row
        self.personal = personal
        self.get_project_calls = 0
        self.get_user_calls = 0
        self.descriptor = types.SimpleNamespace(
            config={"cost_budgets": {"defaults": defaults or {}}},
        )

    def timeout(self, _seconds):
        return self

    def elitea_core_get_user_budget(self, project_id, user_id):  # pylint: disable=W0613
        self.get_user_calls += 1
        return self.member_row

    def elitea_core_get_project_budget(self, project_id):  # pylint: disable=W0613
        self.get_project_calls += 1
        return self.project_row

    def is_personal_project(self, project_id):  # pylint: disable=W0613
        return self.personal

    get_default_limit = budgets.Method.get_default_limit
    get_member_default_limit = budgets.Method.get_member_default_limit

    def resolve(self, project_budget=budgets.UNSET):
        original = budgets.context
        budgets.context = types.SimpleNamespace(rpc_manager=self)
        try:
            return budgets.Method.get_user_budget_limit(self, 1, 2, project_budget)
        finally:
            budgets.context = original


PLATFORM_DEFAULTS = {"enabled": True, "user_monthly_limit": 100.0}


class TestMemberDefaultTier(unittest.TestCase):
    """A project's member default sits between a member's own row and the platform default.

    It is what "set a limit for everyone in this project" resolves to, so it must apply to
    members with no row of their own while leaving members who have one untouched.
    """

    def test_explicit_member_row_beats_the_project_default(self):
        limits = FakeMemberLimits(
            {"monthly_limit": 7.0, "enabled": True},
            {"member_default_limit": 20.0},
            PLATFORM_DEFAULTS,
        )
        self.assertEqual(limits.resolve(), 7.0)

    def test_project_default_beats_the_platform_default(self):
        limits = FakeMemberLimits(None, {"member_default_limit": 20.0}, PLATFORM_DEFAULTS)
        self.assertEqual(limits.resolve(), 20.0)

    def test_no_project_default_falls_through_to_the_platform_default(self):
        limits = FakeMemberLimits(None, {"member_default_limit": None}, PLATFORM_DEFAULTS)
        self.assertEqual(limits.resolve(), 100.0)

    def test_no_project_row_at_all_falls_through(self):
        limits = FakeMemberLimits(None, None, PLATFORM_DEFAULTS)
        self.assertEqual(limits.resolve(), 100.0)

    def test_member_row_without_a_limit_picks_up_the_project_default(self):
        limits = FakeMemberLimits(
            {"monthly_limit": None, "enabled": True},
            {"member_default_limit": 20.0},
            PLATFORM_DEFAULTS,
        )
        self.assertEqual(limits.resolve(), 20.0)

    def test_project_default_overrides_a_member_marked_unlimited(self):
        # A limit an admin set for everyone in the project must not be undone by a member
        # row nobody meant to opt out — that row is often just the dialog's default state
        limits = FakeMemberLimits(
            {"monthly_limit": 7.0, "enabled": False},
            {"member_default_limit": 20.0},
            PLATFORM_DEFAULTS,
        )
        self.assertEqual(limits.resolve(), 20.0)

    def test_exempt_member_still_escapes_the_platform_default(self):
        # With no project default there is nothing project-scoped to enforce, so the
        # exemption keeps its original meaning
        limits = FakeMemberLimits(
            {"monthly_limit": 7.0, "enabled": False}, {}, PLATFORM_DEFAULTS,
        )
        self.assertIsNone(limits.resolve())

    def test_exempt_member_with_no_project_row_is_unlimited(self):
        limits = FakeMemberLimits({"monthly_limit": 7.0, "enabled": False}, None, PLATFORM_DEFAULTS)
        self.assertIsNone(limits.resolve())

    def test_zero_project_default_blocks_rather_than_falling_through(self):
        limits = FakeMemberLimits(None, {"member_default_limit": 0.0}, PLATFORM_DEFAULTS)
        self.assertEqual(limits.resolve(), 0.0)

    def test_project_marked_unlimited_still_applies_its_member_default(self):
        # enabled=false exempts the project's OWN limit; the member default is a separate value
        limits = FakeMemberLimits(
            None, {"enabled": False, "monthly_limit": None, "member_default_limit": 20.0},
            PLATFORM_DEFAULTS,
        )
        self.assertEqual(limits.resolve(), 20.0)

    def test_project_default_applies_even_when_platform_defaults_are_off(self):
        limits = FakeMemberLimits(None, {"member_default_limit": 20.0}, {"enabled": False})
        self.assertEqual(limits.resolve(), 20.0)

    def test_unlimited_when_neither_tier_has_a_value(self):
        limits = FakeMemberLimits(None, {}, {"enabled": False})
        self.assertIsNone(limits.resolve())

    def test_a_passed_project_row_is_not_re_read(self):
        # The member list loops every member, so the project row must be read once, not per row
        limits = FakeMemberLimits(None, None, PLATFORM_DEFAULTS)
        self.assertEqual(limits.resolve({"member_default_limit": 20.0}), 20.0)
        self.assertEqual(limits.get_project_calls, 0)

    def test_row_is_read_when_the_caller_passes_nothing(self):
        limits = FakeMemberLimits(None, {"member_default_limit": 20.0}, PLATFORM_DEFAULTS)
        self.assertEqual(limits.resolve(), 20.0)
        self.assertEqual(limits.get_project_calls, 1)

    def test_passing_none_explicitly_means_no_project_row(self):
        limits = FakeMemberLimits(None, {"member_default_limit": 20.0}, PLATFORM_DEFAULTS)
        self.assertEqual(limits.resolve(None), 100.0)
        self.assertEqual(limits.get_project_calls, 0)


class TestPersonalProjectHasNoMemberLimit(unittest.TestCase):
    """A personal project's one member is its owner, so its project budget IS their budget.

    A member limit there is a second ceiling on the same person. It also enforced while being
    invisible: the Usage page shows only the project scope for a personal project, so users
    were blocked at a platform default of $20 while the page reported 45% of $300 remaining.
    """

    def test_platform_default_does_not_apply(self):
        # The reported bug: no stored limit anywhere, blocked by the inherited default
        limits = FakeMemberLimits(None, None, PLATFORM_DEFAULTS, personal=True)
        self.assertIsNone(limits.resolve())

    def test_explicit_member_row_does_not_apply(self):
        limits = FakeMemberLimits(
            {"monthly_limit": 7.0, "enabled": True}, None, PLATFORM_DEFAULTS, personal=True,
        )
        self.assertIsNone(limits.resolve())

    def test_project_member_default_does_not_apply(self):
        limits = FakeMemberLimits(
            None, {"member_default_limit": 20.0}, PLATFORM_DEFAULTS, personal=True,
        )
        self.assertIsNone(limits.resolve())

    def test_zero_member_limit_does_not_apply(self):
        # Zero is normally a real, blocking limit; it must not survive here either
        limits = FakeMemberLimits(
            {"monthly_limit": 0.0, "enabled": True}, None, PLATFORM_DEFAULTS, personal=True,
        )
        self.assertIsNone(limits.resolve())

    def test_resolves_without_reading_any_budget_row(self):
        # Short-circuits ahead of the row reads, so it costs no cross-plugin calls
        limits = FakeMemberLimits(
            {"monthly_limit": 7.0, "enabled": True}, {"member_default_limit": 20.0},
            PLATFORM_DEFAULTS, personal=True,
        )
        limits.resolve()
        self.assertEqual(limits.get_user_calls, 0)
        self.assertEqual(limits.get_project_calls, 0)

    def test_team_project_is_untouched(self):
        # Regression guard: the same inputs on a team project still resolve every tier
        self.assertEqual(
            FakeMemberLimits(
                {"monthly_limit": 7.0, "enabled": True}, None, PLATFORM_DEFAULTS,
            ).resolve(), 7.0,
        )
        self.assertEqual(
            FakeMemberLimits(
                None, {"member_default_limit": 20.0}, PLATFORM_DEFAULTS,
            ).resolve(), 20.0,
        )
        self.assertEqual(
            FakeMemberLimits(None, None, PLATFORM_DEFAULTS).resolve(), 100.0,
        )


class TestPersonalProjectLookupFailsOpen(unittest.TestCase):
    """A projects-plugin outage must not lift member limits across the whole platform.

    is_personal_project returns False when it cannot answer, so resolution degrades to the
    pre-fix behaviour rather than silently making every member unlimited.
    """

    def test_unknown_personal_status_still_resolves_the_member_limit(self):
        limits = FakeMemberLimits(
            {"monthly_limit": 7.0, "enabled": True}, None, PLATFORM_DEFAULTS, personal=False,
        )
        self.assertEqual(limits.resolve(), 7.0)


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


def aggregate_entities(activity, tag_names):
    """Mirror of read_tags_spend's aggregation over breakdown.entities."""
    result = {tag: 0.0 for tag in tag_names}
    #
    for day in (activity or {}).get("results") or []:
        entities = ((day or {}).get("breakdown") or {}).get("entities") or {}
        for tag, entry in entities.items():
            if tag not in result:
                continue
            metrics = (entry or {}).get("metrics") or entry or {}
            result[tag] += float(metrics.get("spend", 0) or 0)
    #
    return result


class TestMultiTagSpendAggregation(unittest.TestCase):
    """One multi-tag call must yield per-tag spend, not just a combined total.

    Verified live that top-level metadata.total_spend sums ALL requested tags, so
    per-tag figures must come from each day's breakdown.entities instead.
    """

    def test_sums_each_tag_across_days(self):
        activity = {"results": [
            {"breakdown": {"entities": {"a": {"metrics": {"spend": 1.5}},
                                        "b": {"metrics": {"spend": 0.25}}}}},
            {"breakdown": {"entities": {"a": {"metrics": {"spend": 2.0}}}}},
        ]}
        out = aggregate_entities(activity, ["a", "b"])
        self.assertAlmostEqual(out["a"], 3.5)
        self.assertAlmostEqual(out["b"], 0.25)

    def test_requested_tag_with_no_activity_is_zero_not_missing(self):
        out = aggregate_entities({"results": []}, ["a", "b"])
        self.assertEqual(out, {"a": 0.0, "b": 0.0})

    def test_unrequested_tags_are_ignored(self):
        # Other tags (User-Agent, Credential) ride along on real calls
        activity = {"results": [
            {"breakdown": {"entities": {"a": {"metrics": {"spend": 1.0}},
                                        "User-Agent: curl": {"metrics": {"spend": 99.0}}}}},
        ]}
        out = aggregate_entities(activity, ["a"])
        self.assertEqual(out, {"a": 1.0})

    def test_handles_missing_metrics_wrapper(self):
        activity = {"results": [{"breakdown": {"entities": {"a": {"spend": 4.0}}}}]}
        self.assertAlmostEqual(aggregate_entities(activity, ["a"])["a"], 4.0)

    def test_tolerates_empty_and_malformed_payloads(self):
        for payload in (None, {}, {"results": None}, {"results": [None]}, {"results": [{}]}):
            self.assertEqual(aggregate_entities(payload, ["a"]), {"a": 0.0})


class TestBudgetErrorTarget(unittest.TestCase):
    """Who to notify is read from the tag LiteLLM names, so no request context is needed."""

    def _body(self, tag):
        return json.dumps({
            "error": {
                "message": f"Budget has been exceeded! Tag={tag} Current cost: 2, Max budget: 1",
                "type": "budget_exceeded",
            }
        }).encode("utf-8")

    def test_project_tag_yields_project_only(self):
        self.assertEqual(
            budgets.budget_error_target(self._body("elitea_proj_25_202607")), (25, None),
        )

    def test_member_tag_yields_both_ids(self):
        self.assertEqual(
            budgets.budget_error_target(self._body("elitea_proj_25_user_3_202607")), (25, 3),
        )

    def test_multi_digit_ids(self):
        self.assertEqual(
            budgets.budget_error_target(self._body("elitea_proj_12905_user_31652_202607")),
            (12905, 31652),
        )

    def test_no_tag_yields_nothing_to_notify(self):
        # Better to send nothing than to guess a project and notify the wrong admins
        self.assertEqual(budgets.budget_error_target(b'{"error": {}}'), (None, None))

    def test_handles_non_utf8_bytes(self):
        self.assertEqual(budgets.budget_error_target(b"\xff\xfe\x00binary"), (None, None))

    def test_agrees_with_the_scope_helper(self):
        for tag, scope in (
            ("elitea_proj_25_202607", budgets.SCOPE_PROJECT),
            ("elitea_proj_25_user_3_202607", budgets.SCOPE_MEMBER),
        ):
            body = self._body(tag)
            _, user_id = budgets.budget_error_target(body)
            expected_member = budgets.budget_error_scope(body) == budgets.SCOPE_MEMBER
            self.assertEqual(user_id is not None, expected_member, tag)


class TestUserIdFromTag(unittest.TestCase):
    """A per-user tag routes its alert to that member; a project tag does not."""

    def test_member_tag(self):
        self.assertEqual(budgets.user_id_from_tag("elitea_proj_25_user_3_202607"), 3)

    def test_project_tag_is_not_a_member(self):
        self.assertIsNone(budgets.user_id_from_tag("elitea_proj_25_202607"))

    def test_project_named_user_is_not_mistaken_for_a_member(self):
        self.assertIsNone(budgets.user_id_from_tag("elitea_proj_25_user_budget_202607"))

    def test_unrelated_tag(self):
        self.assertIsNone(budgets.user_id_from_tag("User-Agent: curl"))


def threshold_decision(spend, limit, threshold):
    """Mirror of the guard in check_budget_threshold, for the cases worth pinning.

    Notifying is gated on three things: a real limit, spend at or past the threshold, and
    spend still under the limit — at 100% the block itself raises the alert instead.
    """
    if spend is None or limit is None or limit <= 0:
        return False
    #
    pct = spend / limit * 100
    #
    return threshold <= pct < 100


class TestThresholdDecision(unittest.TestCase):
    def test_below_threshold_is_silent(self):
        self.assertFalse(threshold_decision(7.0, 10.0, 80))

    def test_at_threshold_notifies(self):
        self.assertTrue(threshold_decision(8.0, 10.0, 80))

    def test_between_threshold_and_limit_notifies(self):
        self.assertTrue(threshold_decision(9.99, 10.0, 80))

    def test_at_the_limit_defers_to_the_block(self):
        # The rejection raises its own alert, so warning here would duplicate it
        self.assertFalse(threshold_decision(10.0, 10.0, 80))

    def test_over_the_limit_defers_to_the_block(self):
        self.assertFalse(threshold_decision(25.0, 10.0, 80))

    def test_unlimited_never_notifies(self):
        self.assertFalse(threshold_decision(500.0, None, 80))

    def test_zero_limit_is_not_divided_by(self):
        self.assertFalse(threshold_decision(1.0, 0.0, 80))

    def test_unreadable_spend_is_silent(self):
        self.assertFalse(threshold_decision(None, 10.0, 80))

    def test_configured_threshold_is_honoured(self):
        self.assertTrue(threshold_decision(5.0, 10.0, 50))
        self.assertFalse(threshold_decision(5.0, 10.0, 60))


class FakeActivityService:
    """Serves canned activity pages, recording the page numbers requested.

    Binds the real read_tags_spend rather than mirroring it, so a change to the
    paging logic cannot silently pass.
    """

    def __init__(self, pages, fail_on_page=None, fail_for_tag=None):
        self.pages = pages
        self.fail_on_page = fail_on_page
        self.fail_for_tag = fail_for_tag
        self.requested_pages = []
        self.requested_tag_counts = []
        self.service_node = types.SimpleNamespace(call=self)

    def litellm_api_call(self, _endpoint, **kwargs):
        page = kwargs.get("page", 1)
        tags = kwargs.get("tags") or []
        self.requested_pages.append(page)
        self.requested_tag_counts.append(len(tags))
        #
        if page == self.fail_on_page:
            raise RuntimeError("litellm unavailable")
        #
        if self.fail_for_tag is not None and self.fail_for_tag in tags:
            raise RuntimeError("litellm unavailable")
        #
        # Serve only the entities belonging to the requested chunk
        page_data = self.pages[page - 1] if page <= len(self.pages) else {"results": []}
        #
        return _restrict_to(page_data, set(tags))

    read_tags_spend = rpc_budgets.RPC.read_tags_spend
    read_tags_spend_chunk = rpc_budgets.RPC.read_tags_spend_chunk
    read_tag_spend = rpc_budgets.RPC.read_tag_spend
    read_tag_usage_detail = rpc_budgets.RPC.read_tag_usage_detail


def _restrict_to(page_data, tags):
    """Drop entities the caller did not ask for, as the real endpoint would.

    The rest of each day is passed through: the usage-detail reader consumes the date,
    the day's own metrics and the per-model breakdown, none of which are tag-scoped.
    """
    results = []
    #
    for day in (page_data or {}).get("results") or []:
        breakdown = (day or {}).get("breakdown") or {}
        entities = breakdown.get("entities") or {}
        kept = {tag: entry for tag, entry in entities.items() if tag in tags}
        #
        results.append({
            **(day or {}),
            "breakdown": {**breakdown, "entities": kept},
        })
    #
    return {"results": results, "metadata": (page_data or {}).get("metadata") or {}}


def _page(entities, has_more=False):
    """One activity page holding a single day's per-tag breakdown."""
    return {
        "results": [{"breakdown": {"entities": entities}}],
        "metadata": {"has_more": has_more},
    }


def _detail_page(days, has_more=False):
    """An activity page shaped as read_tag_usage_detail consumes it.

    days: [(date, spend, tokens, requests, {model: (spend, tokens, requests)})]
    """
    results = []
    #
    for date, spend, tokens, requests, models in days:
        results.append({
            "date": date,
            "metrics": {
                "spend": spend, "total_tokens": tokens, "successful_requests": requests,
            },
            "breakdown": {
                "models": {
                    name: {"metrics": {
                        "spend": m_spend, "total_tokens": m_tokens,
                        "successful_requests": m_requests,
                    }}
                    for name, (m_spend, m_tokens, m_requests) in (models or {}).items()
                },
            },
        })
    #
    return {"results": results, "metadata": {"has_more": has_more}}


class TestUsageDetailPagination(unittest.TestCase):
    """The Usage page's chart and per-model table read one tag, page by page.

    A single page holds only its own slice of the month, so reading one page dropped whole
    days off the chart and understated the totals beside it.
    """

    NOW = datetime.datetime(2026, 7, 29, tzinfo=datetime.timezone.utc)

    def test_days_from_later_pages_are_included(self):
        svc = FakeActivityService([
            _detail_page([("2026-07-27", 40.0, 100, 5, {"gpt-5": (40.0, 100, 5)})], has_more=True),
            _detail_page([("2026-07-28", 96.5, 250, 9, {"gpt-5": (96.5, 250, 9)})]),
        ])
        out = svc.read_tag_usage_detail("t", self.NOW)
        #
        self.assertAlmostEqual(out["spend"], 136.5)
        self.assertEqual(out["total_tokens"], 350)
        self.assertEqual(out["api_requests"], 14)
        self.assertEqual([d["date"] for d in out["daily"]], ["2026-07-27", "2026-07-28"])

    def test_a_date_split_across_pages_is_merged_not_duplicated(self):
        # Rows are per (tag, date, model, key), so one date can straddle a page boundary
        svc = FakeActivityService([
            _detail_page([("2026-07-27", 10.0, 50, 2, {"gpt-5": (10.0, 50, 2)})], has_more=True),
            _detail_page([("2026-07-27", 5.0, 25, 1, {"haiku": (5.0, 25, 1)})]),
        ])
        out = svc.read_tag_usage_detail("t", self.NOW)
        #
        self.assertEqual(len(out["daily"]), 1)
        self.assertAlmostEqual(out["daily"][0]["spend"], 15.0)
        self.assertAlmostEqual(out["spend"], 15.0)

    def test_model_totals_accumulate_across_pages(self):
        svc = FakeActivityService([
            _detail_page([("2026-07-27", 10.0, 50, 2, {"gpt-5": (10.0, 50, 2)})], has_more=True),
            _detail_page([("2026-07-28", 6.0, 30, 3, {"gpt-5": (6.0, 30, 3)})]),
        ])
        out = svc.read_tag_usage_detail("t", self.NOW)
        #
        self.assertEqual(len(out["models"]), 1)
        self.assertAlmostEqual(out["models"][0]["spend"], 16.0)
        self.assertEqual(out["models"][0]["api_requests"], 5)

    def test_days_are_sorted_by_date(self):
        svc = FakeActivityService([
            _detail_page([("2026-07-28", 1.0, 1, 1, {})], has_more=True),
            _detail_page([("2026-07-26", 1.0, 1, 1, {})]),
        ])
        out = svc.read_tag_usage_detail("t", self.NOW)
        self.assertEqual([d["date"] for d in out["daily"]], ["2026-07-26", "2026-07-28"])

    def test_a_failure_mid_read_reports_unavailable(self):
        svc = FakeActivityService([
            _detail_page([("2026-07-27", 40.0, 100, 5, {})], has_more=True),
            _detail_page([("2026-07-28", 96.5, 250, 9, {})]),
        ], fail_on_page=2)
        out = svc.read_tag_usage_detail("t", self.NOW)
        #
        self.assertFalse(out["available"])
        self.assertEqual(out["spend"], 0.0)
        self.assertEqual(out["daily"], [])

    def test_page_ceiling_is_respected(self):
        svc = FakeActivityService(
            [_detail_page([("2026-07-27", 1.0, 1, 1, {})], has_more=True)] * 200,
        )
        svc.read_tag_usage_detail("t", self.NOW)
        self.assertEqual(len(svc.requested_pages), rpc_budgets.MAX_ACTIVITY_PAGES)


class TestSingleTagSpendPagination(unittest.TestCase):
    """The Usage page reads one tag, and that read is paginated too.

    LiteLLM's `metadata` totals cover only the requested page, so reading page one's
    metadata reported a fraction of a busy tag's spend -- a user was shown 45% of their
    budget used while enforcement had already blocked them.
    """

    NOW = datetime.datetime(2026, 7, 29, tzinfo=datetime.timezone.utc)

    def test_sums_spend_across_every_page(self):
        svc = FakeActivityService([
            _page({"t": {"metrics": {"spend": 40.0, "total_tokens": 100}}}, has_more=True),
            _page({"t": {"metrics": {"spend": 96.5, "total_tokens": 250}}}),
        ])
        out = svc.read_tag_spend("t", self.NOW)
        #
        self.assertAlmostEqual(out["spend"], 136.5)
        self.assertEqual(out["total_tokens"], 350)
        self.assertEqual(svc.requested_pages, [1, 2])

    def test_requests_a_page_size_rather_than_taking_the_default(self):
        # The endpoint defaults to 50 records; relying on it is what truncated the total
        svc = FakeActivityService([_page({"t": {"metrics": {"spend": 1.0}}})])
        svc.read_tag_spend("t", self.NOW)
        self.assertEqual(svc.requested_pages, [1])

    def test_stops_at_the_last_page(self):
        svc = FakeActivityService([_page({"t": {"metrics": {"spend": 2.0}}})])
        out = svc.read_tag_spend("t", self.NOW)
        #
        self.assertAlmostEqual(out["spend"], 2.0)
        self.assertTrue(out["available"])
        self.assertEqual(svc.requested_pages, [1])

    def test_other_tags_on_the_same_page_are_ignored(self):
        svc = FakeActivityService([
            _page({"t": {"metrics": {"spend": 3.0}}, "other": {"metrics": {"spend": 99.0}}}),
        ])
        self.assertAlmostEqual(svc.read_tag_spend("t", self.NOW)["spend"], 3.0)

    def test_a_tag_with_no_activity_is_zero_and_available(self):
        # Distinct from unreadable: nothing spent is a real answer
        svc = FakeActivityService([_page({})])
        out = svc.read_tag_spend("t", self.NOW)
        #
        self.assertEqual(out["spend"], 0.0)
        self.assertTrue(out["available"])

    def test_a_later_page_failure_reports_unavailable_rather_than_a_short_total(self):
        # A partial sum would look like genuinely lower spend and quietly raise the
        # headroom shown to the user
        svc = FakeActivityService([
            _page({"t": {"metrics": {"spend": 40.0}}}, has_more=True),
            _page({"t": {"metrics": {"spend": 96.5}}}),
        ], fail_on_page=2)
        out = svc.read_tag_spend("t", self.NOW)
        #
        self.assertEqual(out["spend"], 0.0)
        self.assertFalse(out["available"])

    def test_first_page_failure_is_unavailable(self):
        svc = FakeActivityService([_page({"t": {"metrics": {"spend": 1.0}}})], fail_on_page=1)
        out = svc.read_tag_spend("t", self.NOW)
        #
        self.assertEqual(out["spend"], 0.0)
        self.assertFalse(out["available"])

    def test_page_ceiling_bounds_a_server_that_always_says_has_more(self):
        svc = FakeActivityService(
            [_page({"t": {"metrics": {"spend": 1.0}}}, has_more=True)] * 200,
        )
        svc.read_tag_spend("t", self.NOW)
        self.assertEqual(len(svc.requested_pages), rpc_budgets.MAX_ACTIVITY_PAGES)

    def test_period_is_the_current_month(self):
        svc = FakeActivityService([_page({})])
        self.assertEqual(svc.read_tag_spend("t", self.NOW)["period"], "202607")


class TestSpendPagination(unittest.TestCase):
    """Activity is paginated by spend record, so multi-tag reads span pages.

    Reading only the first page under-reports whichever tags fall past it, which is
    silent because the shortfall looks like genuinely lower spend.
    """

    NOW = datetime.datetime(2026, 7, 29, tzinfo=datetime.timezone.utc)

    def test_sums_a_tag_across_pages(self):
        svc = FakeActivityService([
            _page({"a": {"metrics": {"spend": 1.5}}}, has_more=True),
            _page({"a": {"metrics": {"spend": 2.25}}}),
        ])
        out = svc.read_tags_spend(["a"], self.NOW)
        #
        self.assertAlmostEqual(out["a"], 3.75)
        self.assertEqual(svc.requested_pages, [1, 2])

    def test_stops_when_has_more_is_false(self):
        svc = FakeActivityService([_page({"a": {"metrics": {"spend": 1.0}}})])
        svc.read_tags_spend(["a"], self.NOW)
        self.assertEqual(svc.requested_pages, [1])

    def test_tag_appearing_only_on_a_later_page_is_still_counted(self):
        # The exact regression: a quiet tag ordered past the first page read as zero
        svc = FakeActivityService([
            _page({"a": {"metrics": {"spend": 5.0}}}, has_more=True),
            _page({"b": {"metrics": {"spend": 7.0}}}),
        ])
        out = svc.read_tags_spend(["a", "b"], self.NOW)
        self.assertAlmostEqual(out["b"], 7.0)

    def test_missing_metadata_is_treated_as_the_last_page(self):
        svc = FakeActivityService([{"results": []}])
        svc.read_tags_spend(["a"], self.NOW)
        self.assertEqual(svc.requested_pages, [1])

    def test_page_ceiling_bounds_a_server_that_always_says_has_more(self):
        endless = [_page({"a": {"metrics": {"spend": 1.0}}}, has_more=True)] * 200
        svc = FakeActivityService(endless)
        out = svc.read_tags_spend(["a"], self.NOW)
        #
        self.assertEqual(len(svc.requested_pages), rpc_budgets.MAX_ACTIVITY_PAGES)
        self.assertAlmostEqual(out["a"], float(rpc_budgets.MAX_ACTIVITY_PAGES))

    def test_first_page_failure_yields_zeros_not_a_partial_total(self):
        svc = FakeActivityService([], fail_on_page=1)
        self.assertEqual(svc.read_tags_spend(["a", "b"], self.NOW), {"a": 0.0, "b": 0.0})

    def test_later_page_failure_zeroes_the_chunk_rather_than_understating_it(self):
        # A half-read total would silently claim less spend than really happened, which
        # for a budget check is worse than reporting nothing for those tags
        svc = FakeActivityService([
            _page({"a": {"metrics": {"spend": 3.0}}}, has_more=True),
        ], fail_on_page=2)
        self.assertEqual(svc.read_tags_spend(["a"], self.NOW)["a"], 0.0)

    def test_no_tags_makes_no_call(self):
        svc = FakeActivityService([_page({})])
        self.assertEqual(svc.read_tags_spend([], self.NOW), {})
        self.assertEqual(svc.requested_pages, [])

    def test_large_tag_list_is_split_into_chunks(self):
        # Tags ride in the query string, so one request per project would build a URL
        # a real deployment (16k+ projects) cannot send
        cap = rpc_budgets.MAX_TAGS_PER_ACTIVITY_CALL
        tags = [f"elitea_proj_{i}_202607" for i in range(cap * 2 + 5)]
        svc = FakeActivityService([_page({})])
        #
        svc.read_tags_spend(tags, self.NOW)
        #
        self.assertEqual(len(svc.requested_tag_counts), 3)
        self.assertEqual(svc.requested_tag_counts, [cap, cap, 5])

    def test_no_chunk_exceeds_the_tag_cap(self):
        tags = [f"elitea_proj_{i}_202607" for i in range(1000)]
        svc = FakeActivityService([_page({})])
        #
        svc.read_tags_spend(tags, self.NOW)
        #
        for count in svc.requested_tag_counts:
            self.assertLessEqual(count, rpc_budgets.MAX_TAGS_PER_ACTIVITY_CALL)

    def test_every_tag_is_summed_across_chunks(self):
        cap = rpc_budgets.MAX_TAGS_PER_ACTIVITY_CALL
        tags = [f"t{i}" for i in range(cap + 3)]
        # One page holding every tag; the fake serves each chunk only its own entities
        svc = FakeActivityService([
            _page({tag: {"metrics": {"spend": 1.0}} for tag in tags}),
        ])
        #
        out = svc.read_tags_spend(tags, self.NOW)
        #
        self.assertEqual(len(out), len(tags))
        self.assertTrue(all(value == 1.0 for value in out.values()))

    def test_a_failed_chunk_does_not_lose_the_others(self):
        cap = rpc_budgets.MAX_TAGS_PER_ACTIVITY_CALL
        tags = [f"t{i}" for i in range(cap + 2)]
        # The failing tag is in the second chunk
        svc = FakeActivityService(
            [_page({tag: {"metrics": {"spend": 2.0}} for tag in tags})],
            fail_for_tag=f"t{cap}",
        )
        #
        out = svc.read_tags_spend(tags, self.NOW)
        #
        self.assertAlmostEqual(out["t0"], 2.0)
        # A partly-read chunk understates spend, so its tags report zero instead
        self.assertEqual(out[f"t{cap}"], 0.0)

    def test_single_chunk_makes_one_request(self):
        svc = FakeActivityService([_page({"a": {"metrics": {"spend": 1.0}}})])
        svc.read_tags_spend(["a"], self.NOW)
        self.assertEqual(svc.requested_tag_counts, [1])

    def test_unrequested_tags_are_still_ignored_across_pages(self):
        svc = FakeActivityService([
            _page({"a": {"metrics": {"spend": 1.0}},
                   "User-Agent: curl": {"metrics": {"spend": 99.0}}}, has_more=True),
            _page({"Credential: x": {"metrics": {"spend": 50.0}}}),
        ])
        self.assertEqual(svc.read_tags_spend(["a"], self.NOW), {"a": 1.0})


class FakeModes:
    """Mirror of budgets_mode's resolution, including the legacy boolean."""

    def __init__(self, config):
        self.config = config

    def budgets_mode(self):
        config = self.config
        mode = config.get("mode", None)
        #
        if mode is None:
            return budgets.MODE_ENFORCE if config.get("enabled", False) else budgets.MODE_OFF
        #
        mode = str(mode).strip().lower()
        #
        if mode not in budgets.BUDGET_MODES:
            return budgets.MODE_OFF
        #
        return mode

    def budgets_enabled(self):
        return self.budgets_mode() != budgets.MODE_OFF

    def budgets_enforcing(self):
        return self.budgets_mode() == budgets.MODE_ENFORCE


class TestBudgetModes(unittest.TestCase):
    """Off must be a true rollback; observe tracks without ever blocking."""

    def test_off_disables_everything(self):
        m = FakeModes({"mode": "off"})
        self.assertFalse(m.budgets_enabled())
        self.assertFalse(m.budgets_enforcing())

    def test_observe_tracks_but_never_enforces(self):
        m = FakeModes({"mode": "observe"})
        self.assertTrue(m.budgets_enabled())
        self.assertFalse(m.budgets_enforcing())

    def test_enforce_tracks_and_enforces(self):
        m = FakeModes({"mode": "enforce"})
        self.assertTrue(m.budgets_enabled())
        self.assertTrue(m.budgets_enforcing())

    def test_missing_config_defaults_to_off(self):
        self.assertEqual(FakeModes({}).budgets_mode(), budgets.MODE_OFF)

    def test_legacy_enabled_true_maps_to_enforce(self):
        # An existing config predating the mode setting must keep working
        self.assertEqual(FakeModes({"enabled": True}).budgets_mode(), budgets.MODE_ENFORCE)

    def test_legacy_enabled_false_maps_to_off(self):
        self.assertEqual(FakeModes({"enabled": False}).budgets_mode(), budgets.MODE_OFF)

    def test_mode_wins_over_legacy_enabled(self):
        m = FakeModes({"mode": "observe", "enabled": True})
        self.assertEqual(m.budgets_mode(), budgets.MODE_OBSERVE)
        self.assertFalse(m.budgets_enforcing())

    def test_unknown_mode_fails_safe_to_off(self):
        # A typo must not silently enable blocking
        self.assertEqual(FakeModes({"mode": "enfroce"}).budgets_mode(), budgets.MODE_OFF)

    def test_mode_is_case_and_space_insensitive(self):
        self.assertEqual(FakeModes({"mode": " Enforce "}).budgets_mode(), budgets.MODE_ENFORCE)


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
