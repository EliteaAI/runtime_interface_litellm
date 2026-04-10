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

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import context  # pylint: disable=E0401


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
        configuration_section = configuration["section"]
        #
        if configuration_section == "ai_credentials":
            #
            # Credential
            #
            configuration_credential = self.configuration_to_credential(configuration)
            #
            if configuration_credential is not None:
                self.service_node.call.litellm_api_call(
                    "credential_new",
                    **configuration_credential,
                )
                #
                credential_name = configuration_credential["credential_name"]
                #
                log.info("Added credential: %s", credential_name)
                #
                with self.configurations_lock:
                    lock_key = f'{configuration["project_id"]}:{configuration["id"]}'
                    self.configurations_blocklist.add(lock_key)
                #
                try:
                    context.rpc_manager.timeout(5).configurations_update(
                        project_id=configuration["project_id"],
                        config_id=configuration["id"],
                        payload={
                            "status_ok": True,
                        },
                    )
                    #
                    log.info("Set status_ok for configuration: %s", configuration["id"])
                except:  # pylint: disable=W0702
                    pass
        #
        elif configuration_section in ["llm", "embedding", "image_generation"]:
            #
            # Skip LiteLLM provisioning for imported models (no credentials = externally managed)
            #
            if not configuration.get("data", {}).get("ai_credentials"):
                log.info(
                    "Skipping LiteLLM provisioning for imported config %s (externally managed)",
                    configuration.get("id"),
                )
                return
            #
            # Model
            #
            from plugins.configurations.utils import expand_configuration  # pylint: disable=E0401,C0415
            #
            expanded_configuration = configuration.copy()
            #
            try:
                expand_configuration(
                    expanded_configuration["data"],
                    current_project_id=configuration["project_id"],
                    user_id=configuration["author_id"],
                )
            except:  # pylint: disable=W0702
                log.exception("Failed to expand configuration, skipping")
                return
            #
            configuration_model = self.configuration_to_model(expanded_configuration)
            #
            if configuration_model is not None:
                self.service_node.call.litellm_api_call(
                    "model_new",
                    **configuration_model,
                )
                #
                model_name = configuration_model["model_name"]
                #
                log.info("Added model: %s", model_name)
                #
                with self.configurations_lock:
                    lock_key = f'{configuration["project_id"]}:{configuration["id"]}'
                    self.configurations_blocklist.add(lock_key)
                #
                try:
                    context.rpc_manager.timeout(5).configurations_update(
                        project_id=configuration["project_id"],
                        config_id=configuration["id"],
                        payload={
                            "status_ok": True,
                        },
                    )
                    #
                    log.info("Set status_ok for configuration: %s", configuration["id"])
                except:  # pylint: disable=W0702
                    log.exception("Failed to set configuration status_ok")

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
        elif configuration_section in ["llm", "embedding", "image_generation"]:
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
