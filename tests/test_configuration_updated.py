import collections
import importlib.util
import itertools
import pathlib
import sys
import threading
import time
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "rilt_6891"


class FakeGateway:
    def __init__(self):
        self.credentials = {}
        self.models = {}
        self.calls = []
        self.model_ids = itertools.count()

    def __call__(self, method, *args, **kwargs):
        self.calls.append(method)
        return getattr(self, method)(*args, **kwargs)

    def credential_new(self, credential_name, credential_values, credential_info):
        assert credential_name not in self.credentials, "LiteLLM rejects a duplicate credential name"
        self.credentials[credential_name] = credential_values

    def credential_list(self):
        return [{"credential_name": name} for name in self.credentials]

    def credential_delete(self, credential_name):
        del self.credentials[credential_name]

    def model_new(self, model_name, litellm_params, model_info):
        model_id = f"id-{next(self.model_ids)}"
        self.models[model_id] = {"model_name": model_name, "litellm_params": litellm_params,
                                 "model_info": {**model_info, "id": model_id}}

    def model_info(self):
        return list(self.models.values())

    def model_delete(self, model_id):
        del self.models[model_id]

    def model_names(self):
        return sorted(model["model_name"] for model in self.models.values())


class FakeConfigurations:
    def __init__(self):
        self.saved = {}
        self.status_updates = []

    def save(self, configuration):
        key = (configuration["project_id"], configuration["uuid"])
        status_ok = self.saved[key].get("status_ok") if key in self.saved else None
        self.saved[key] = {**configuration, "status_ok": status_ok}

    def timeout(self, _seconds):
        return self

    def configurations_update(self, project_id, config_id, payload):
        self.status_updates.append((project_id, config_id, payload))
        for configuration in self.saved.values():
            if (configuration["project_id"], configuration["id"]) == (project_id, config_id) and "status_ok" in payload:
                configuration["status_ok"] = payload["status_ok"]

    def status_ok(self, project_id, configuration_uuid):
        return self.saved[(project_id, configuration_uuid)].get("status_ok")

    def configurations_get_filtered_project(self, project_id, include_shared, filter_fields):
        found = self.saved.get((project_id, filter_fields["uuid"]))
        return [found] if found else []

    def expand_configuration(self, data, **_kwargs):
        credentials = data["ai_credentials"]
        credential = self.saved[(credentials["project_id"], credentials["uuid"])]
        data["ai_credentials"] = {**credentials, "api_base": credential["data"]["api_base"]}


def _package(name, path):
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module


def _load_classes(configurations):
    web = types.SimpleNamespace(event=lambda *_: lambda fn: fn, method=lambda *_: lambda fn: fn)
    log = types.SimpleNamespace(info=lambda *_: None, warning=lambda *_: None, exception=lambda *_: None)
    _stub("pylon")
    _stub("pylon.core")
    _stub("pylon.core.tools", log=log, web=web)
    _stub("tools", context=types.SimpleNamespace(rpc_manager=configurations), VaultClient=None)
    _stub("plugins")
    _stub("plugins.configurations")
    _stub("plugins.configurations.utils", expand_configuration=configurations.expand_configuration)
    for suffix, path in [("", ROOT), (".events", ROOT / "events"), (".methods", ROOT / "methods"),
                         (".utils", ROOT / "utils")]:
        _package(PACKAGE + suffix, path)
    return (
        _load("events.configuration").Event,
        _load("methods.configuration_entities").Method,
        _load("methods.configuration_transformations").Method,
    )


class FakeModule:
    def __init__(self, gateway):
        self.service_node = types.SimpleNamespace(call=types.SimpleNamespace(litellm_api_call=gateway))
        self.configurations_lock = threading.Lock()
        self.configurations_blocklist = set()
        self.configuration_entity_locks = collections.defaultdict(threading.Lock)
        self.llm_allowed = True
        self.unbuildable = set()
        self.vault = {}

    def is_llm_allowed_for_project(self, _configuration):
        return self.llm_allowed

    def configuration_to_credential(self, configuration):
        if configuration["uuid"] in self.unbuildable:
            return None
        return {
            "credential_name": f'{configuration["project_id"]}_{configuration["uuid"]}',
            "credential_values": {"api_key": self.vault.get(configuration["data"]["api_key"],
                                                            configuration["data"]["api_key"]),
                                  "api_base": configuration["data"]["api_base"]},
            "credential_info": {},
        }

    def configuration_to_model(self, configuration):
        if configuration["uuid"] in self.unbuildable:
            return None
        credentials = configuration["data"]["ai_credentials"]
        return {
            "model_name": f'{configuration["project_id"]}_{configuration["data"]["name"]}',
            "litellm_params": {"litellm_credential_name": f'{credentials["project_id"]}_{credentials["uuid"]}',
                               "api_base": f'{credentials["api_base"]}/anthropic',
                               "model": configuration["data"]["name"]},
            "model_info": {"centry_configuration_uuid": configuration["uuid"]},
        }


def _credential(api_key, api_base="https://dial-a"):
    return {"id": 4, "uuid": "cred-uuid", "project_id": 2, "author_id": 1, "type": "ai_dial",
            "section": "ai_credentials", "data": {"api_key": api_key, "api_base": api_base}}


def _model(name, project_id=2, uuid="model-uuid", config_id=9):
    return {"id": config_id, "uuid": uuid, "project_id": project_id, "author_id": 1, "type": "llm_model",
            "section": "llm", "data": {"name": name, "ai_credentials": {"project_id": 2, "uuid": "cred-uuid"}}}


class ConfigurationUpdatedTest(unittest.TestCase):
    def setUp(self):
        self.configurations = FakeConfigurations()
        event, entities, transformations = _load_classes(self.configurations)
        self.gateway = FakeGateway()
        self.module = FakeModule(self.gateway)
        for cls in (event, entities, transformations):
            for name, fn in vars(cls).items():
                if callable(fn) and not hasattr(FakeModule, name):
                    setattr(self.module, name, types.MethodType(fn, self.module))

    def _create(self, configuration):
        self.configurations.save(configuration)
        self.module.on_configuration_created(None, "configuration_created", configuration)

    def _save(self, previous, current):
        self.configurations.save(current)
        return {**current, "previous_data": previous["data"]}

    def _fire(self, payload):
        self.module.on_configuration_updated(None, "configuration_updated", payload)

    def test_new_api_key_replaces_the_registered_one(self):
        self._create(_credential("old-key"))
        self._fire(self._save(_credential("old-key"), _credential("new-key")))
        self.assertEqual(self.gateway.credentials["2_cred-uuid"]["api_key"], "new-key")

    def test_renamed_model_is_registered_only_under_the_new_name(self):
        self._create(_credential("key"))
        self._create(_model("gpt-4o"))
        self._fire(self._save(_model("gpt-4o"), _model("gpt-5")))
        self.assertEqual(self.gateway.model_names(), ["2_gpt-5"])

    def test_edits_handled_out_of_order_end_on_the_latest_save(self):
        self._create(_credential("key"))
        self._create(_model("a"))
        to_b = self._save(_model("a"), _model("b"))
        to_c = self._save(_model("b"), _model("c"))
        self._fire(to_c)
        self._fire(to_b)
        self.assertEqual(self.gateway.model_names(), ["2_c"])

    def test_an_edit_waits_for_the_handler_already_working_on_that_configuration(self):
        self._create(_credential("old-key"))
        payload = self._save(_credential("old-key"), _credential("new-key"))
        lock = self.module.configuration_entity_locks["2:4"]
        lock.acquire()
        worker = threading.Thread(target=self._fire, args=[payload])
        worker.start()
        time.sleep(0.2)
        self.assertEqual(self.gateway.credentials["2_cred-uuid"]["api_key"], "old-key")
        lock.release()
        worker.join(5)
        self.assertEqual(self.gateway.credentials["2_cred-uuid"]["api_key"], "new-key")

    def test_unbuildable_edit_keeps_the_working_model_listed(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))
        self.module.unbuildable.add("model-uuid")
        self._fire(self._save(_model("gpt-5"), _model("gpt-5")))
        self.assertEqual(self.gateway.model_names(), ["2_gpt-5"])
        project_id, config_id, payload = self.configurations.status_updates[-1]
        self.assertEqual((project_id, config_id), (2, 9))
        self.assertNotIn("status_ok", payload)
        self.assertIn("previous settings stay in use", payload["status_logs"])

    def test_unbuildable_rename_marks_the_model_broken(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))
        self.module.unbuildable.add("model-uuid")
        self._fire(self._save(_model("gpt-5"), _model("gpt-6")))
        self.assertEqual(self.gateway.model_names(), ["2_gpt-5"])
        self.assertIs(self.configurations.status_updates[-1][2]["status_ok"], False)

    def test_fixing_a_failed_rename_drops_the_old_name(self):
        self._create(_credential("key"))
        self._create(_model("gpt-4o"))
        self.module.unbuildable.add("model-uuid")
        self._fire(self._save(_model("gpt-4o"), _model("gpt-5")))
        self.module.unbuildable.clear()
        self._fire(self._save(_model("gpt-5"), _model("gpt-5")))
        self.assertEqual(self.gateway.model_names(), ["2_gpt-5"])

    def test_same_uuid_in_another_project_is_left_alone(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))
        self.gateway.model_new("20_gpt-5", {"model": "gpt-5"}, {"centry_configuration_uuid": "model-uuid"})
        self._fire(self._save(_model("gpt-5"), _model("gpt-6")))
        self.assertEqual(self.gateway.model_names(), ["20_gpt-5", "2_gpt-6"])

    def test_gateway_rejecting_the_new_model_marks_it_broken(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))

        def rejecting_model_new(*_args, **_kwargs):
            raise RuntimeError("gateway 503")

        self.gateway.model_new = rejecting_model_new
        self._fire(self._save(_model("gpt-5"), _model("gpt-6")))
        project_id, config_id, payload = self.configurations.status_updates[-1]
        self.assertEqual((project_id, config_id, payload["status_ok"]), (2, 9, False))
        self.assertIn("rejected", payload["status_logs"])

    def test_failed_credential_edit_leaves_dependent_models_on_the_old_api_base(self):
        self._create(_credential("key", "https://dial-a"))
        self._create(_model("claude"))
        self.module.unbuildable.add("cred-uuid")
        self._fire(self._save(_credential("key", "https://dial-a"), _credential("key", "https://dial-b")))
        [model] = self.gateway.models.values()
        self.assertEqual(model["litellm_params"]["api_base"], "https://dial-a/anthropic")

    def test_model_resave_without_changes_leaves_the_gateway_untouched(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))
        self.gateway.calls.clear()
        self._fire(self._save(_model("gpt-5"), _model("gpt-5")))
        self.assertNotIn("model_delete", self.gateway.calls)
        self.assertNotIn("model_new", self.gateway.calls)

    def test_model_resave_repairs_an_api_base_that_went_stale_before(self):
        self._create(_credential("key", "https://dial-a"))
        self._create(_model("claude"))
        self.configurations.save(_credential("key", "https://dial-b"))
        self._fire(self._save(_model("claude"), _model("claude")))
        [model] = self.gateway.models.values()
        self.assertEqual(model["litellm_params"]["api_base"], "https://dial-b/anthropic")

    def test_model_resave_drops_a_leftover_deployment_under_another_name(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))
        self.gateway.model_new("2_gpt-4o", {"model": "gpt-4o"}, {"centry_configuration_uuid": "model-uuid"})
        self._fire(self._save(_model("gpt-5"), _model("gpt-5")))
        self.assertEqual(self.gateway.model_names(), ["2_gpt-5"])

    def test_reverting_a_failed_rename_lists_the_model_again(self):
        self._create(_credential("key"))
        self._create(_model("gpt-4o"))
        self.module.unbuildable.add("model-uuid")
        self._fire(self._save(_model("gpt-4o"), _model("gpt-5")))
        self.assertIs(self.configurations.status_ok(2, "model-uuid"), False)
        self.module.unbuildable.clear()
        self._fire(self._save(_model("gpt-5"), _model("gpt-4o")))
        self.assertEqual(self.gateway.model_names(), ["2_gpt-4o"])
        self.assertIs(self.configurations.status_ok(2, "model-uuid"), True)

    def test_credential_resave_pushes_a_secret_changed_in_the_vault(self):
        self.module.vault["{{secret.openai_key}}"] = "old-key"
        self._create(_credential("{{secret.openai_key}}"))
        self.module.vault["{{secret.openai_key}}"] = "new-key"
        self._fire(self._save(_credential("{{secret.openai_key}}"), _credential("{{secret.openai_key}}")))
        self.assertEqual(self.gateway.credentials["2_cred-uuid"]["api_key"], "new-key")

    def test_one_failing_dependent_model_does_not_stop_the_others(self):
        self._create(_credential("key", "https://dial-a"))
        self._create(_model("claude", project_id=2))
        self._create(_model("claude", project_id=3, uuid="other-model", config_id=5))
        load_configuration = self.module.load_configuration

        def flaky_load(project_id, configuration_uuid):
            if configuration_uuid == "model-uuid":
                raise TimeoutError("rpc timeout")
            return load_configuration(project_id, configuration_uuid)

        self.module.load_configuration = flaky_load
        self._fire(self._save(_credential("key", "https://dial-a"), _credential("key", "https://dial-b")))
        bases = {model["model_name"]: model["litellm_params"]["api_base"] for model in self.gateway.models.values()}
        self.assertEqual(bases["3_claude"], "https://dial-b/anthropic")

    def test_api_base_edit_reaches_models_that_copy_it(self):
        self._create(_credential("key", "https://dial-a"))
        self._create(_model("claude", project_id=2))
        self._create(_model("claude", project_id=3, uuid="other-model", config_id=5))
        self._fire(self._save(_credential("key", "https://dial-a"), _credential("key", "https://dial-b")))
        bases = sorted(model["litellm_params"]["api_base"] for model in self.gateway.models.values())
        self.assertEqual(bases, ["https://dial-b/anthropic", "https://dial-b/anthropic"])

    def test_key_only_edit_leaves_models_alone(self):
        self._create(_credential("old-key"))
        self._create(_model("gpt-5"))
        model_ids = set(self.gateway.models)
        self._fire(self._save(_credential("old-key"), _credential("new-key")))
        self.assertEqual(set(self.gateway.models), model_ids)

    def test_status_reply_is_swallowed_by_the_status_changed_lock(self):
        self._create(_credential("old-key"))
        self.module.configurations_blocklist.clear()
        self._fire(self._save(_credential("old-key"), _credential("new-key")))
        self.assertEqual(self.configurations.status_updates[-1], (2, 4, {"status_ok": True}))
        self.assertEqual(self.module.configurations_blocklist, {"2:4"})

    def test_edit_in_a_project_without_own_llms_only_unregisters(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))
        self.module.llm_allowed = False
        self._fire(self._save(_model("gpt-5"), _model("gpt-5")))
        self.assertEqual(self.gateway.models, {})

    def test_edit_of_a_deleted_configuration_only_unregisters(self):
        self._create(_credential("key"))
        self._create(_model("gpt-5"))
        payload = self._save(_model("gpt-5"), _model("gpt-6"))
        del self.configurations.saved[(2, "model-uuid")]
        self._fire(payload)
        self.assertEqual(self.gateway.models, {})

    def test_other_sections_never_reach_the_gateway(self):
        routing = {"id": 1, "uuid": "u", "project_id": 2, "type": "auto_routing", "section": "auto_routing",
                   "data": {}, "previous_data": {}}
        self._fire(routing)
        self.assertEqual(self.gateway.calls, [])


if __name__ == "__main__":
    unittest.main()
