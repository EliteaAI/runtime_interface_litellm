"""Unit tests for the LiteLLM `allow_client_tags` opt-in fix (issue #6090).

Run standalone (no pylon runtime needed):
    python3 tests/test_llm_client_tags.py
"""

import os
import sys
import types
import unittest


def _stub_pylon_and_tools():
    """Register the same pylon/tools stubs used across this plugin's tests."""
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
    tools_stub.VaultClient = lambda *a, **kw: types.SimpleNamespace(update_secrets=lambda **kw2: None)
    #
    for name, mod in [
        ("pylon", pylon), ("pylon.core", pylon_core), ("pylon.core.tools", pylon_tools),
        ("pylon.core.tools.log", log_stub), ("pylon.core.tools.web", web_stub),
        ("tools", tools_stub),
    ]:
        sys.modules.setdefault(name, mod)
    #
    return tools_stub


def _load_module(name, plugin_root):
    sys.path.insert(0, os.path.join(plugin_root, "methods"))
    try:
        return __import__(name)
    finally:
        sys.path.remove(os.path.join(plugin_root, "methods"))


_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tools_stub = _stub_pylon_and_tools()

project_entities = _load_module("project_entities", _PLUGIN_ROOT)

# admin_tasks.py additionally needs `plugins.admin.tasks.logs.make_logger`
_plugins_mod = types.ModuleType("plugins")
_admin_mod = types.ModuleType("plugins.admin")
_tasks_mod = types.ModuleType("plugins.admin.tasks")
_logs_mod = types.ModuleType("plugins.admin.tasks.logs")


class _NullLogger:
    """Swallows every call; tests assert on fakes, not log output."""

    def __getattr__(self, _name):
        return lambda *a, **kw: None


class _FakeMakeLogger:
    def __enter__(self):
        return _NullLogger()

    def __exit__(self, *exc_info):
        return False


_logs_mod.make_logger = _FakeMakeLogger
_tasks_mod.logs = _logs_mod
_admin_mod.tasks = _tasks_mod
_plugins_mod.admin = _admin_mod

for _name, _mod in [
    ("plugins", _plugins_mod), ("plugins.admin", _admin_mod),
    ("plugins.admin.tasks", _tasks_mod), ("plugins.admin.tasks.logs", _logs_mod),
]:
    sys.modules[_name] = _mod

admin_tasks = _load_module("admin_tasks", _PLUGIN_ROOT)


class FakeLiteLLMCalls:
    """Records every `litellm_api_call` invocation made through `service_node.call`."""

    def __init__(self, team_list=None, model_info=None):
        self.calls = []
        self._team_list = team_list if team_list is not None else []
        self._model_info = model_info if model_info is not None else []

    def litellm_api_call(self, endpoint, *args, **kwargs):
        self.calls.append((endpoint, args, kwargs))
        #
        if endpoint == "team_list":
            return self._team_list
        if endpoint == "team_new":
            return {"team_id": "new-team-id"}
        if endpoint == "key_generate":
            return {"key": "sk-fake"}
        if endpoint == "model_info":
            return self._model_info
        return None

    def calls_for(self, endpoint):
        return [c for c in self.calls if c[0] == endpoint]


class TestMakeProjectEntitiesOptsIn(unittest.TestCase):
    """The creation path is the single chokepoint for new project teams."""

    def _make_self(self, model_info=None):
        fake_calls = FakeLiteLLMCalls(model_info=model_info)
        double = types.SimpleNamespace(
            service_node=types.SimpleNamespace(call=fake_calls),
            get_public_project_id=lambda: 1,
        )
        return double, fake_calls

    def test_team_new_is_followed_by_team_update_with_allow_client_tags(self):
        double, fake_calls = self._make_self()
        #
        project_entities.Method.make_project_entities(double, 42)
        #
        team_new_calls = fake_calls.calls_for("team_new")
        self.assertEqual(len(team_new_calls), 1)
        #
        update_calls = fake_calls.calls_for("team_update")
        self.assertEqual(len(update_calls), 1)
        _, args, _ = update_calls[0]
        self.assertEqual(args, ("new-team-id", {"metadata": {"allow_client_tags": True}}))

    def test_key_generate_still_uses_the_new_team_id(self):
        double, fake_calls = self._make_self()
        #
        project_entities.Method.make_project_entities(double, 42)
        #
        key_calls = fake_calls.calls_for("key_generate")
        self.assertEqual(key_calls[0][1][1], "new-team-id")


class FakeSyncSelf:
    """Minimal double for the `Method` mixins `sync_llm_entities` reaches into."""

    def __init__(self, teams, project_list, allow_own=True, public_project_id=1):
        self.service_node = types.SimpleNamespace(call=FakeLiteLLMCalls(team_list=teams))
        self.descriptor = types.SimpleNamespace(config={"allow_project_own_llms": allow_own})
        self._public_project_id = public_project_id
        self._project_list = project_list
        self.made_entities_for = []

    def get_public_project_id(self):
        return self._public_project_id

    def parse_admin_task_param(self, param):
        return {
            "project_ids": None,
            "dry_run": "dry_run" in (param or ""),
            "scope_requested": False,
            "scope_all_requested": False,
            "scope_parse_errors": [],
        }

    def make_project_entities(self, project_id):
        self.made_entities_for.append(project_id)


def _run_sync(fake_self, param=""):
    rpc_manager = types.SimpleNamespace(
        timeout=lambda *_a, **_kw: types.SimpleNamespace(
            project_list=lambda **_kw2: fake_self._project_list,
            configurations_get_filtered_project=lambda **_kw2: [],
        ),
    )
    original_context = admin_tasks.context
    admin_tasks.context = types.SimpleNamespace(rpc_manager=rpc_manager)
    try:
        admin_tasks.Method.sync_llm_entities(fake_self, param=param)
    finally:
        admin_tasks.context = original_context


class TestSyncRetrofitsExistingTeams(unittest.TestCase):
    """`sync_llm_entities` is the retrofit path: it must opt existing teams in
    without touching keys, models, or credentials.
    """

    def test_team_with_empty_metadata_gets_opted_in(self):
        fake_self = FakeSyncSelf(
            teams=[{"team_alias": "project_7", "team_id": "team-7", "metadata": {}}],
            project_list=[{"id": 7}],
        )
        #
        _run_sync(fake_self)
        #
        update_calls = fake_self.service_node.call.calls_for("team_update")
        self.assertEqual(len(update_calls), 1)
        _, args, _ = update_calls[0]
        self.assertEqual(args, ("team-7", {"metadata": {"allow_client_tags": True}}))
        self.assertEqual(fake_self.made_entities_for, [])

    def test_team_that_already_opted_in_is_left_alone(self):
        fake_self = FakeSyncSelf(
            teams=[{
                "team_alias": "project_7", "team_id": "team-7",
                "metadata": {"allow_client_tags": True},
            }],
            project_list=[{"id": 7}],
        )
        #
        _run_sync(fake_self)
        #
        self.assertEqual(fake_self.service_node.call.calls_for("team_update"), [])

    def test_existing_metadata_is_preserved_not_clobbered(self):
        fake_self = FakeSyncSelf(
            teams=[{
                "team_alias": "project_7", "team_id": "team-7",
                "metadata": {"some_other_key": "keep-me"},
            }],
            project_list=[{"id": 7}],
        )
        #
        _run_sync(fake_self)
        #
        _, args, _ = fake_self.service_node.call.calls_for("team_update")[0]
        self.assertEqual(
            args[1],
            {"metadata": {"some_other_key": "keep-me", "allow_client_tags": True}},
        )

    def test_dry_run_logs_but_makes_no_call(self):
        fake_self = FakeSyncSelf(
            teams=[{"team_alias": "project_7", "team_id": "team-7", "metadata": {}}],
            project_list=[{"id": 7}],
        )
        #
        _run_sync(fake_self, param="dry_run")
        #
        self.assertEqual(fake_self.service_node.call.calls_for("team_update"), [])

    def test_new_project_still_goes_through_creation_not_retrofit(self):
        # Team doesn't exist yet: creation path owns the opt-in, retrofit must not fire
        fake_self = FakeSyncSelf(teams=[], project_list=[{"id": 9}])
        #
        _run_sync(fake_self)
        #
        self.assertEqual(fake_self.made_entities_for, [9])
        self.assertEqual(fake_self.service_node.call.calls_for("team_update"), [])

    def test_missing_team_id_in_scope_is_not_touched(self):
        # Only the team matching this project's alias is a retrofit candidate
        fake_self = FakeSyncSelf(
            teams=[{"team_alias": "project_99", "team_id": "team-99", "metadata": {}}],
            project_list=[{"id": 7}],
        )
        #
        _run_sync(fake_self)
        #
        self.assertEqual(fake_self.service_node.call.calls_for("team_update"), [])
        self.assertEqual(fake_self.made_entities_for, [7])


if __name__ == "__main__":
    unittest.main(verbosity=2)
