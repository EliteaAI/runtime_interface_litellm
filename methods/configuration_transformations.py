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

import importlib

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import VaultClient  # pylint: disable=E0401


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def configuration_to_credential(self, configuration, standalone=True):
        """ Method """
        project_id = configuration["project_id"]
        config_type = configuration["type"]
        #
        vault_client = VaultClient(project_id)
        configuration_info = vault_client.unsecret(configuration)
        #
        try:
            mapper = importlib.import_module(
                f"plugins.runtime_interface_litellm.tools.mappers.configuration.{config_type}"
            )
            #
            return mapper.to_credential(
                configuration_info,
                standalone,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to map")
        #
        return None

    @web.method()
    def configuration_to_model(self, expanded_configuration):
        """ Method """
        try:
            project_id = expanded_configuration["project_id"]
            config_type = expanded_configuration["data"]["ai_credentials"]["configuration_type"]
            #
            vault_client = VaultClient(project_id)
            expanded_configuration_info = vault_client.unsecret(expanded_configuration)
            #
            mapper = importlib.import_module(
                f"plugins.runtime_interface_litellm.tools.mappers.configuration.{config_type}"
            )
            #
            return mapper.to_model(
                expanded_configuration_info,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to map")
        #
        return None


    @web.method()
    def configuration_to_credential_info(self, configuration):
        """ Method """
        project_id = configuration["project_id"]
        configuration_uuid = configuration["uuid"]
        #
        return {
            "credential_name": f"{project_id}_{configuration_uuid}",
        }


    @web.method()
    def configuration_to_model_info(self, configuration):
        """
        FRAGILE — CHANGE WITH EXTRA CAUTION.
        model_name MUST be the project-prefixed form ({project_id}_{name}) because
        LiteLLM stores all managed models under that prefix.  delete_configuration_entities
        uses this value as a lookup key against LiteLLM's model registry; returning the
        bare name causes a silent no-op — stale entries accumulate and can produce
        circular routing loops on the next sync.
        """
        try:
            configuration_uuid = configuration["uuid"]
            project_id = configuration["project_id"]
            model_name = configuration["data"]["name"]
            #
            return {
                "model_name": f"{project_id}_{model_name}",
                "configuration_uuid": f"{configuration_uuid}",
            }
        except KeyError:
            return None
