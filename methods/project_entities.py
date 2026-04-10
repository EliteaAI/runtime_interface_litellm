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

from tools import VaultClient  # pylint: disable=E0401


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def make_project_entities(self, project_id):
        """ Method """
        team_name = f"project_{project_id}"
        key_name = f"project_key_{project_id}"
        #
        allowed_models = [f"{project_id}_*"]
        #
        public_project_id = self.get_public_project_id()
        #
        if public_project_id != project_id:
            allowed_models.append(f"{public_project_id}_*")
        #
        # Include non-prefixed (imported/external) models from LiteLLM
        import re as _re  # pylint: disable=C0415
        try:
            models = self.service_node.call.litellm_api_call("model_info")
            for m in (models or []):
                name = m.get("model_name", "")
                if name and not _re.match(r'^\d+_', name):
                    allowed_models.append(name)
        except:  # pylint: disable=W0702
            pass
        #
        team_result = self.service_node.call.litellm_api_call(
            "team_new",
            team_name,
            models=allowed_models,
        )
        key_result = self.service_node.call.litellm_api_call(
            "key_generate",
            key_name, team_result["team_id"],
            models=["all-team-models"],
        )
        #
        llm_key = key_result["key"]
        #
        vault_client = VaultClient(project_id)
        project_secrets = vault_client.get_secrets()
        project_secrets["project_llm_key"] = llm_key
        vault_client.set_secrets(project_secrets)

    @web.method()
    def delete_project_entities(self, project_id):
        """ Method """
        team_name = f"project_{project_id}"
        key_name = f"project_key_{project_id}"
        name_prefix = f"{project_id}_"
        #
        # Teams and keys
        #
        teams = self.service_node.call.litellm_api_call(
            "team_list",
            team_name,
        )
        #
        for team in teams:
            if team["team_alias"] == team_name:
                team_keys = self.service_node.call.litellm_api_call(
                    "key_list",
                    team["team_id"],
                )
                #
                for team_key in team_keys:
                    if team_key["key_alias"] == key_name:
                        self.service_node.call.litellm_api_call(
                            "key_delete",
                            team_key["token"],
                        )
                #
                self.service_node.call.litellm_api_call(
                    "team_delete",
                    team["team_id"],
                )
        #
        # Models and credentials
        #
        models = self.service_node.call.litellm_api_call(
            "model_info",
        )
        #
        for model in models:
            if model["model_name"].startswith(name_prefix):
                self.service_node.call.litellm_api_call(
                    "model_delete",
                    model["model_info"]["id"],
                )
        #
        credentials = self.service_node.call.litellm_api_call(
            "credential_list",
        )
        #
        for credential in credentials:
            if credential["credential_name"].startswith(name_prefix):
                self.service_node.call.litellm_api_call(
                    "credential_delete",
                    credential["credential_name"],
                )
