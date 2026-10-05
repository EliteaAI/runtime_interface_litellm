#!/usr/bin/python3
# coding=utf-8

#   Copyright 2025 EPAM Systems
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

""" Method """

import copy

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import context  # pylint: disable=E0401

from ..utils.utils import MODEL_CONFIGURATION_SECTIONS


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def make_configuration_entities(self, configuration):
        """ Method """
        if not self.is_gateway_managed(configuration):
            if configuration["section"] in MODEL_CONFIGURATION_SECTIONS:
                log.info(
                    "Skipping LiteLLM provisioning for imported config %s (externally managed)",
                    configuration.get("id"),
                )
            return
        #
        entity = self.build_configuration_entity(configuration)
        #
        if entity is not None:
            self.register_configuration_entity(configuration, entity)

    @web.method()
    def is_gateway_managed(self, configuration):
        """ Method """
        configuration_section = configuration["section"]
        #
        if configuration_section == "ai_credentials":
            return True
        #
        return configuration_section in MODEL_CONFIGURATION_SECTIONS and \
            bool(configuration.get("data", {}).get("ai_credentials"))

    @web.method()
    def build_configuration_entity(self, configuration):
        """ Method """
        if configuration["section"] == "ai_credentials":
            configuration_credential = self.configuration_to_credential(configuration)
            #
            if configuration_credential is None:
                return None
            #
            return {"kind": "credential", "payload": configuration_credential}
        #
        from plugins.configurations.utils import expand_configuration  # pylint: disable=E0401,C0415
        #
        expanded_configuration = {**configuration, "data": copy.deepcopy(configuration["data"])}
        #
        try:
            expand_configuration(
                expanded_configuration["data"],
                current_project_id=configuration["project_id"],
                user_id=configuration["author_id"],
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to expand configuration, skipping")
            return None
        #
        configuration_model = self.configuration_to_model(expanded_configuration)
        #
        if configuration_model is None:
            return None
        #
        return {"kind": "model", "payload": configuration_model}

    @web.method()
    def register_configuration_entity(self, configuration, entity):
        """ Method """
        payload = entity["payload"]
        #
        if entity["kind"] == "credential":
            self.service_node.call.litellm_api_call("credential_new", **payload)
            log.info("Added credential: %s", payload["credential_name"])
        else:
            self.service_node.call.litellm_api_call("model_new", **payload)
            log.info("Added model: %s", payload["model_name"])
        #
        self.set_configuration_status(configuration, {"status_ok": True})
        log.info("Set status_ok for configuration: %s", configuration["id"])

    @web.method()
    def set_configuration_status(self, configuration, payload):
        """ Method """
        with self.configurations_lock:
            lock_key = f'{configuration["project_id"]}:{configuration["id"]}'
            self.configurations_blocklist[lock_key] += 1
        #
        try:
            context.rpc_manager.timeout(5).configurations_update(
                project_id=configuration["project_id"],
                config_id=configuration["id"],
                payload=payload,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to set configuration status")

    @web.method()
    def load_configuration(self, project_id, configuration_uuid):
        """ Method """
        configurations = context.rpc_manager.timeout(5).configurations_get_filtered_project(
            project_id=project_id,
            include_shared=False,
            filter_fields={"uuid": configuration_uuid},
        )
        #
        return configurations[0] if configurations else None

    @web.method()
    def reapply_configuration_entities(self, configuration, previous_data):
        """ Method """
        previous_configuration = {**configuration, "data": previous_data}
        #
        try:
            current_configuration = self.load_configuration(configuration["project_id"], configuration["uuid"])
        except:  # pylint: disable=W0702
            log.exception("Failed to load configuration %s, using the event payload", configuration["id"])
            current_configuration = configuration
        #
        if current_configuration is None:
            self.delete_configuration_entities(previous_configuration)
            return False
        #
        if not self.is_llm_allowed_for_project(current_configuration):
            log.info("Skipping: allow_project_own_llms is disabled for project %s",
                     current_configuration.get("project_id"))
            self.delete_configuration_entities(previous_configuration)
            self.delete_configuration_entities(current_configuration)
            return False
        #
        if not self.is_gateway_managed(current_configuration):
            self.delete_configuration_entities(previous_configuration)
            return False
        #
        entity = self.build_configuration_entity(current_configuration)
        #
        if entity is None:
            log.warning("Configuration %s could not be applied, keeping the registered one",
                        current_configuration["id"])
            status = {
                "status_logs": "The saved settings could not be applied to the LLM gateway; "
                               "the previous settings stay in use",
            }
            renamed_model_unknown_to_gateway = \
                previous_data.get("name") != current_configuration["data"].get("name")
            if renamed_model_unknown_to_gateway:
                status["status_ok"] = False
            self.set_configuration_status(current_configuration, status)
            return False
        #
        if entity["kind"] == "model" and self.is_model_deployed_as(current_configuration, entity["payload"]):
            if current_configuration.get("status_ok") is not True:
                self.set_configuration_status(current_configuration, {"status_ok": True})
            return True
        #
        try:
            if entity["kind"] == "model":
                superseded_model_ids = self.superseded_model_ids(previous_configuration, current_configuration)
                self.register_configuration_entity(current_configuration, entity)
            elif self.update_credential_in_place(previous_configuration, entity):
                self.set_configuration_status(current_configuration, {"status_ok": True})
            else:
                self.delete_configuration_entities(previous_configuration)
                self.delete_configuration_entities(current_configuration)
                self.register_configuration_entity(current_configuration, entity)
        except:  # pylint: disable=W0702
            log.exception("Failed to register configuration %s", current_configuration["id"])
            if entity["kind"] == "credential":
                self.restore_credential(previous_configuration)
            self.set_configuration_status(current_configuration, {
                "status_ok": False,
                "status_logs": "The LLM gateway rejected the saved settings; save again to retry",
            })
            return False
        #
        if entity["kind"] == "model":
            self.delete_models(superseded_model_ids)
        #
        return True

    @web.method()
    def update_credential_in_place(self, previous_configuration, entity):
        """ Method """
        payload = entity["payload"]
        #
        try:
            previous_entity = self.build_configuration_entity(previous_configuration)
            #
            if previous_entity is None:
                return False
            #
            removed_values = set(previous_entity["payload"]["credential_values"]) - set(payload["credential_values"])
            if removed_values:
                return False
            #
            updated = self.service_node.call.litellm_api_call("credential_update", **payload)
        except:  # pylint: disable=W0702
            log.exception("Failed to update credential %s in place", payload["credential_name"])
            return False
        #
        if updated:
            log.info("Updated credential: %s", payload["credential_name"])
        #
        return updated

    @web.method()
    def superseded_model_ids(self, previous_configuration, current_configuration):
        """ Method """
        project_prefix = f'{current_configuration["project_id"]}_'
        configuration_uuid = current_configuration["uuid"]
        model_names = {
            model_info["model_name"]
            for model_info in (self.configuration_to_model_info(previous_configuration),
                               self.configuration_to_model_info(current_configuration))
            if model_info is not None
        }
        #
        return [
            model["model_info"]["id"]
            for model in self.service_node.call.litellm_api_call("model_info")
            if (model["model_name"] in model_names and
                model["model_info"].get("centry_configuration_uuid", configuration_uuid) == configuration_uuid) or
            (model["model_info"].get("centry_configuration_uuid") == configuration_uuid and
             model["model_name"].startswith(project_prefix))
        ]

    @web.method()
    def delete_models(self, model_ids):
        """ Method """
        for model_id in model_ids:
            try:
                self.service_node.call.litellm_api_call("model_delete", model_id)
            except:  # pylint: disable=W0702
                log.exception("Failed to delete superseded model %s", model_id)

    @web.method()
    def restore_credential(self, configuration):
        """ Method """
        try:
            previous_entity = self.build_configuration_entity(configuration)
            #
            if previous_entity is None:
                return
            #
            self.service_node.call.litellm_api_call("credential_new", **previous_entity["payload"])
            log.info("Restored credential: %s", previous_entity["payload"]["credential_name"])
        except:  # pylint: disable=W0702
            log.exception("Failed to restore credential for configuration %s", configuration["id"])

    @web.method()
    def is_model_deployed_as(self, configuration, payload):
        """ Method """
        project_prefix = f'{configuration["project_id"]}_'
        deployments = [
            model
            for model in self.service_node.call.litellm_api_call("model_info")
            if model["model_info"].get("centry_configuration_uuid") == configuration["uuid"] and
            model["model_name"].startswith(project_prefix)
        ]
        #
        if len(deployments) != 1:
            return False
        #
        deployment = deployments[0]
        #
        return deployment["model_name"] == payload["model_name"] and all(
            deployment["litellm_params"].get(key) == value for key, value in payload["litellm_params"].items()
        ) and all(
            deployment["model_info"].get(key) == value for key, value in payload["model_info"].items()
        )

    @web.method()
    def reapply_credential_models(self, credential_configuration):
        """ Method """
        credential_name = self.configuration_to_credential_info(credential_configuration)["credential_name"]
        dependent_models = set()
        #
        for model in self.service_node.call.litellm_api_call("model_info"):
            configuration_uuid = model["model_info"].get("centry_configuration_uuid")
            #
            if configuration_uuid and \
                    model["litellm_params"].get("litellm_credential_name") == credential_name:
                project_id = int(model["model_name"].split("_", 1)[0])
                dependent_models.add((project_id, configuration_uuid))
        #
        for project_id, configuration_uuid in dependent_models:
            try:
                model_configuration = self.load_configuration(project_id, configuration_uuid)
                #
                if model_configuration is None or not self.is_llm_allowed_for_project(model_configuration):
                    continue
                #
                with self.configuration_entity_locks[f'{project_id}:{model_configuration["id"]}']:
                    self.reapply_configuration_entities(model_configuration, model_configuration["data"])
            except:  # pylint: disable=W0702
                log.exception("Failed to re-apply model %s in project %s", configuration_uuid, project_id)

    @web.method()
    def delete_configuration_entities(self, configuration):
        """ Method """
        configuration_section = configuration["section"]
        #
        if configuration_section == "ai_credentials":
            #
            # Credential
            #
            configuration_credential_info = self.configuration_to_credential_info(configuration)
            #
            if configuration_credential_info is not None:
                existing_credentials = self.service_node.call.litellm_api_call("credential_list")
                name_to_credential = {}
                #
                for credential in existing_credentials:
                    credential_name = credential["credential_name"]
                    #
                    if credential_name not in name_to_credential:
                        name_to_credential[credential_name] = []
                    #
                    name_to_credential[credential_name].append(credential)
                #
                configuration_credential_name = configuration_credential_info["credential_name"]
                #
                for credential in name_to_credential.get(configuration_credential_name, []):
                    log.info("Deleting credential: %s", configuration_credential_name)
                    #
                    self.service_node.call.litellm_api_call(
                        "credential_delete",
                        credential["credential_name"],
                    )
        #
        elif configuration_section in MODEL_CONFIGURATION_SECTIONS:
            #
            # Skip LiteLLM deletion for imported models (no credentials = externally managed)
            #
            if not configuration.get("data", {}).get("ai_credentials"):
                log.info(
                    "Skipping LiteLLM deletion for imported config %s (externally managed)",
                    configuration.get("id"),
                )
                return
            #
            # Model
            #
            configuration_model_info = self.configuration_to_model_info(configuration)
            #
            if configuration_model_info is not None:
                existing_models = self.service_node.call.litellm_api_call("model_info")
                name_to_model = {}
                #
                for model in existing_models:
                    model_name = model["model_name"]
                    #
                    if model_name not in name_to_model:
                        name_to_model[model_name] = []
                    #
                    name_to_model[model_name].append(model)
                #
                model_name = configuration_model_info["model_name"]
                configuration_uuid = configuration_model_info["configuration_uuid"]
                #
                for model in name_to_model.get(model_name, []):
                    if "centry_configuration_uuid" in model["model_info"] and \
                            model["model_info"]["centry_configuration_uuid"] != configuration_uuid:
                        continue
                    #
                    log.info("Deleting model: %s", model_name)
                    #
                    self.service_node.call.litellm_api_call(
                        "model_delete",
                        model["model_info"]["id"],
                    )
