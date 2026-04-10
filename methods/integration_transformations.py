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
    def integration_to_credential(self, integration, standalone=True):
        """ Method """
        integration_name = integration["integration_name"]
        integration_data = integration["integration_data"]
        #
        project_id = integration["project_id"]
        integration_uid = integration_data["uid"]
        #
        if project_id is None:
            target_project = self.get_public_project_id()
        else:
            project_id = int(project_id)
            target_project = project_id
        #
        vault_client = VaultClient(project_id)
        #
        try:
            mapper = importlib.import_module(
                f"plugins.runtime_interface_litellm.tools.mappers.integration.{integration_name}"
            )
            #
            return mapper.to_credential(
                integration,
                integration_name,
                integration_data,
                integration_uid,
                project_id,
                target_project,
                vault_client,
                standalone,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to map")
        #
        return None

    @web.method()
    def integration_to_test(self, integration):
        """ Method """
        integration_name = integration["integration_name"]
        integration_data = integration["integration_data"]
        #
        project_id = integration["project_id"]
        integration_uid = integration_data["uid"]
        #
        if project_id is None:
            target_project = self.get_public_project_id()
        else:
            project_id = int(project_id)
            target_project = project_id
        #
        vault_client = VaultClient(project_id)
        #
        try:
            mapper = importlib.import_module(
                f"plugins.runtime_interface_litellm.tools.mappers.integration.{integration_name}"
            )
            #
            return mapper.to_test(
                integration,
                integration_name,
                integration_data,
                integration_uid,
                project_id,
                target_project,
                vault_client,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to map")
        #
        return None

    @web.method()
    def integration_to_models(self, integration, credential_name=None):
        """ Method """
        integration_name = integration["integration_name"]
        integration_data = integration["integration_data"]
        #
        project_id = integration["project_id"]
        integration_uid = integration_data["uid"]
        #
        if project_id is None:
            target_project = self.get_public_project_id()
        else:
            project_id = int(project_id)
            target_project = project_id
        #
        vault_client = VaultClient(project_id)
        #
        result = []
        #
        try:
            mapper = importlib.import_module(
                f"plugins.runtime_interface_litellm.tools.mappers.integration.{integration_name}"
            )
            #
            result.extend(
                mapper.to_models(
                    integration,
                    integration_name,
                    integration_data,
                    integration_uid,
                    project_id,
                    target_project,
                    vault_client,
                    credential_name,
                )
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to map")
        #
        return result
