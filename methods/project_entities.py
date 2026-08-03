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
        # Opt in to LiteLLM >=1.83.14 keeping our caller-supplied budget/routing
        # tags; harmless no-op on older versions that don't check this key.
        self.service_node.call.litellm_api_call(
            "team_update",
            team_result["team_id"],
            {"metadata": {"allow_client_tags": True}},
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
        vault_client.update_secrets(add={"project_llm_key": llm_key})

    @web.method()
    def delete_project_entities(self, project_id, dry_run=False):
        """ Method """
        team_name = f"project_{project_id}"
        key_name = f"project_key_{project_id}"
        name_prefix = f"{project_id}_"
        prefix = "[DRY RUN] " if dry_run else ""
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
                        log.info("%sDeleting key: %s", prefix, key_name)
                        if not dry_run:
                            self.service_node.call.litellm_api_call(
                                "key_delete",
                                team_key["token"],
                            )
                #
                log.info("%sDeleting team: %s", prefix, team_name)
                if not dry_run:
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
            model_name = model.get("model_name", "") or ""
            if not model_name.startswith(name_prefix):
                continue
            log.info("%sDeleting model: %s", prefix, model_name)
            if dry_run:
                continue
            model_id = (model.get("model_info") or {}).get("id")
            if not model_id:
                log.error(
                    "Cannot delete model %s: missing model_info.id", model_name,
                )
                continue
            self.service_node.call.litellm_api_call(
                "model_delete",
                model_id,
            )
        #
        credentials = self.service_node.call.litellm_api_call(
            "credential_list",
        )
        #
        for credential in credentials:
            if credential["credential_name"].startswith(name_prefix):
                log.info("%sDeleting credential: %s", prefix, credential["credential_name"])
                if not dry_run:
                    self.service_node.call.litellm_api_call(
                        "credential_delete",
                        credential["credential_name"],
                    )
