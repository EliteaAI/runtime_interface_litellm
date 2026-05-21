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

""" RPC: resolve LiteLLM model key and API key for a given project/model/section. """

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import context, VaultClient  # pylint: disable=E0401


class RPC:  # pylint: disable=E1101,R0903,W0201

    @web.rpc("litellm_resolve_model", "litellm_resolve_model")
    def litellm_resolve_model(self, project_id: int, model_name: str, section: str) -> dict:
        """Return the LiteLLM model key and API key for the given model.

        Shared models are registered in LiteLLM under the config owner's project_id
        (e.g. ``1_whisper``).  A caller in project 2 using a shared config from
        project 1 must send ``1_whisper`` with project 1's API key, not ``2_whisper``.

        Resolution order:
        1. Query available models for the section (including shared) to find the
           config owner's project_id.
        2. Fetch the LiteLLM API key from the config owner's vault.
        3. Return ``{litellm_model, project_llm_key}``.

        Falls back to the caller's own project_id/key if resolution fails.
        """
        config_project_id = project_id

        try:
            response = context.rpc_manager.timeout(10).configurations_get_models(
                project_id=project_id, section=section, include_shared=True
            )
            for item in response.get("items", []):
                if item.get("name") == model_name:
                    config_project_id = item["project_id"]
                    break
        except Exception:  # pylint: disable=W0703
            log.exception(
                "litellm_resolve_model: failed to resolve config project_id "
                "for project=%s model=%s section=%s, falling back to caller's project",
                project_id, model_name, section,
            )

        try:
            project_llm_key = VaultClient(config_project_id).get_secrets().get("project_llm_key", "")
        except Exception:  # pylint: disable=W0703
            log.exception(
                "litellm_resolve_model: failed to fetch project_llm_key for project=%s",
                config_project_id,
            )
            project_llm_key = ""

        return {
            "litellm_model": f"{config_project_id}_{model_name}",
            "project_llm_key": project_llm_key,
            "config_project_id": config_project_id,
        }
