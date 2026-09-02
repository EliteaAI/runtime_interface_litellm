"""Unit tests for the spend-table helpers behind the per-member usage breakdown.

Run standalone (no pylon runtime needed):
    python3 tests/test_spend_db.py
"""

import os
import re
import sys
import types
import unittest


def _load_spend_db_module():
    """Load methods/spend_db.py with pylon, tools and sqlalchemy stubbed out."""
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
    web_stub.rpc = lambda *a, **kw: (lambda func: func)
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
    # text() only has to round-trip the SQL: nothing here executes a query
    sqlalchemy_stub = types.ModuleType("sqlalchemy")
    sqlalchemy_stub.text = lambda value: value
    sqlalchemy_stub.create_engine = lambda *a, **kw: None
    #
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("pylon.core.tools.web", web_stub),
        ("tools", tools_stub), ("flask", flask_stub), ("sqlalchemy", sqlalchemy_stub),
    ]:
        sys.modules.setdefault(name, mod)
    #
    sys.path.insert(0, os.path.join(plugin_root, "methods"))
    try:
        import budgets  # pylint: disable=C0415
    finally:
        sys.path.pop(0)
    #
    # spend_db imports its tag helpers relatively, so it is loaded as part of a throwaway
    # package whose "budgets" is the module already loaded above
    import importlib.util  # pylint: disable=C0415
    #
    pkg = types.ModuleType("_rilmethods")
    pkg.__path__ = []
    sys.modules["_rilmethods"] = pkg
    sys.modules["_rilmethods.budgets"] = budgets
    #
    spec = importlib.util.spec_from_file_location(
        "_rilmethods.spend_db", os.path.join(plugin_root, "methods", "spend_db.py"),
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "_rilmethods"
    spec.loader.exec_module(module)
    #
    return module


spend_db = _load_spend_db_module()


def like_to_regex(pattern):
    r"""Translate a LIKE pattern with ESCAPE '\' into an equivalent regex.

    Lets the tests assert what the pattern actually matches, rather than only asserting
    the literal string the builder produced.
    """
    result = ""
    index = 0
    #
    while index < len(pattern):
        char = pattern[index]
        #
        if char == "\\":
            index += 1
            result += re.escape(pattern[index])
        elif char == "%":
            result += ".*"
        elif char == "_":
            result += "."
        else:
            result += re.escape(char)
        #
        index += 1
    #
    return re.compile(f"^{result}$")


class TestEscapeLike(unittest.TestCase):
    """Budget tags are full of underscores, which LIKE reads as a wildcard by default."""

    def test_underscores_are_escaped(self):
        self.assertEqual(spend_db.escape_like("a_b"), r"a\_b")

    def test_percent_is_escaped(self):
        self.assertEqual(spend_db.escape_like("50%"), r"50\%")

    def test_backslash_is_escaped_first(self):
        # Escaping the backslash after the underscore would corrupt the escape itself
        self.assertEqual(spend_db.escape_like("a\\_b"), r"a\\\_b")


class TestMemberTagPattern(unittest.TestCase):
    """The pattern must select exactly one project's member tags for one period."""

    def setUp(self):
        self.pattern = spend_db.make_member_tag_pattern(3, "202608")
        self.regex = like_to_regex(self.pattern)

    def test_every_underscore_is_literal(self):
        # An unescaped "_" would let elitea_projX3... match, i.e. leak another project
        self.assertNotIn("_", self.pattern.replace("\\_", ""))

    def test_matches_a_member_tag(self):
        self.assertTrue(self.regex.match("elitea_proj_3_user_10_202608"))

    def test_rejects_another_project(self):
        self.assertFalse(self.regex.match("elitea_proj_30_user_10_202608"))

    def test_rejects_another_period(self):
        self.assertFalse(self.regex.match("elitea_proj_3_user_10_202607"))

    def test_rejects_the_project_tag_itself(self):
        # Project and member tags are separate spend rows; summing both double-counts
        self.assertFalse(self.regex.match("elitea_proj_3_202608"))


class TestPeriodBounds(unittest.TestCase):
    """ISO date strings, so the text date column can still use its index."""

    def test_full_month(self):
        self.assertEqual(spend_db.period_bounds("202608"), ("2026-08-01", "2026-08-31"))

    def test_leap_february(self):
        self.assertEqual(spend_db.period_bounds("202402"), ("2024-02-01", "2024-02-29"))

    def test_non_leap_february(self):
        self.assertEqual(spend_db.period_bounds("202602"), ("2026-02-01", "2026-02-28"))

    def test_single_digit_month_is_zero_padded(self):
        self.assertEqual(spend_db.period_bounds("202601"), ("2026-01-01", "2026-01-31"))


class TestQueryShape(unittest.TestCase):
    """Guards the collation trap: a tag range silently matches nothing under en_US.utf8."""

    def test_bounds_on_date_not_on_a_tag_range(self):
        sql = spend_db.MEMBER_SPEND_QUERY
        #
        self.assertIn(":period_start", sql)
        self.assertIn("LIKE :member_pattern ESCAPE", sql)
        # tag >= / tag < collate as equal when the bound differs only by punctuation
        self.assertNotIn("tag >=", sql)
        self.assertNotIn("tag <", sql)


if __name__ == "__main__":
    unittest.main(verbosity=2)
