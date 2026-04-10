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


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def make_integration_entities(self, integration):
        """ Method """
        #
        # Credential
        #
        integration_credential = self.integration_to_credential(integration)
        #
        if integration_credential is None:
            credential_name = None
        else:
            self.service_node.call.litellm_api_call(
                "credential_new",
                **integration_credential,
            )
            #
            credential_name = integration_credential["credential_name"]
        #
        log.info("Added credential: %s", credential_name)
        #
        # Models
        #
        integration_models = self.integration_to_models(integration, credential_name)
        #
        for integration_model in integration_models:
            model_name = integration_model["model_name"]
            #
            self.service_node.call.litellm_api_call(
                "model_new",
                **integration_model,
            )
            #
            log.info("Added model: %s", model_name)

    @web.method()
    def delete_integration_entities(self, integration):
        """ Method """
        integration_uid = integration["integration_data"]["uid"]
        #
        # Transform
        #
        integration_credential = self.integration_to_credential(integration)
        #
        if integration_credential is None:
            integration_credential_name = None
        else:
            integration_credential_name = integration_credential["credential_name"]
        #
        integration_models = self.integration_to_models(integration, integration_credential_name)
        #
        # Models
        #
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
        for integration_model in integration_models:
            model_name = integration_model["model_name"]
            #
            for model in name_to_model.get(model_name, []):
                if "centry_integration_uid" in model["model_info"] and \
                        model["model_info"]["centry_integration_uid"] != integration_uid:
                    continue
                #
                log.info("Deleting model: %s", model_name)
                #
                self.service_node.call.litellm_api_call(
                    "model_delete",
                    model["model_info"]["id"],
                )
        #
        # Credentials
        #
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
        if integration_credential_name is not None:
            for credential in name_to_credential.get(integration_credential_name, []):
                log.info("Deleting credential: %s", integration_credential_name)
                #
                self.service_node.call.litellm_api_call(
                    "credential_delete",
                    credential["credential_name"],
                )
