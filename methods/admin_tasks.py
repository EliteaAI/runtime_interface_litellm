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

import time

from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from plugins.admin.tasks.logs import make_logger  # pylint: disable=E0401

from tools import context, serialize  # pylint: disable=E0401


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    # pylint: disable=R,W0613
    @web.method()
    def delete_llm_entities(self, *args, **kwargs):
        """Delete all LiteLLM proxy entities (teams, keys, models). No params. Destructive."""
        with make_logger() as log:
            log.info("Starting")
            start_ts = time.time()
            #
            try:
                log.info("Deleting LLM entities")
                #
                self.delete_all_entities()
            except:  # pylint: disable=W0702
                log.exception("Got exception, stopping")
            #
            end_ts = time.time()
            log.info("Exiting (duration = %s)", end_ts - start_ts)

    # pylint: disable=R,W0613
    @web.method()
    def delete_llm_venv(self, *args, **kwargs):
        """Delete the LiteLLM proxy Python virtual environment. No params. Service restart required after."""
        with make_logger() as log:
            log.info("Starting")
            start_ts = time.time()
            #
            try:
                log.info("Deleting LLM venv")
                #
                self.service_node.call.litellm_delete_venv()
            except:  # pylint: disable=W0702
                log.exception("Got exception, stopping")
            #
            end_ts = time.time()
            log.info("Exiting (duration = %s)", end_ts - start_ts)

    # pylint: disable=R,W0613
    @web.method()
    def sync_llm_entities(self, *args, **kwargs):
        """Sync LiteLLM proxy entities (teams, keys, models) for all projects and their AI integrations. No params. Long-running."""
        with make_logger() as log:
            log.info("Starting")
            start_ts = time.time()
            #
            try:
                log.info("Syncing project LLM entities")
                #
                failed_integration_calls = 0
                failed_configuration_calls = 0
                #
                present_teams = set()
                #
                log.info("Getting team list")
                teams = self.service_node.call.litellm_api_call(
                    "team_list",
                )
                #
                for team in teams:
                    present_teams.add(team["team_alias"])
                #
                log.info("Getting project list")
                project_list = context.rpc_manager.timeout(120).project_list(
                    filter_={"create_success": True},
                )
                #
                allow_own = self.descriptor.config.get("allow_project_own_llms", True)
                public_project_id = self.get_public_project_id()
                #
                for project in project_list:
                    project_id = project["id"]
                    #
                    team_name = f"project_{project_id}"
                    key_name = f"project_key_{project_id}"
                    #
                    if team_name not in present_teams:
                        log.info("Creating LLM team and key: %s - %s", team_name, key_name)
                        #
                        self.make_project_entities(project_id)
                        #
                        present_teams.add(team_name)
                    #
                    # Skip configs/integrations for non-public projects when own LLMs disabled
                    #
                    if not allow_own and project_id != public_project_id:
                        log.info(
                            "Skipping configs/integrations for project %s "
                            "(allow_project_own_llms is disabled)", project_id,
                        )
                        continue
                    #
                    # Configurations
                    #
                    try:
                        if failed_configuration_calls < 3:
                            project_configurations = context.rpc_manager.timeout(
                                5
                            ).configurations_get_filtered_project(
                                project_id=project_id,
                                include_shared=False,
                            )
                        else:
                            project_configurations = []
                    except:  # pylint: disable=W0702
                        project_configurations = []
                        failed_configuration_calls += 1
                    else:
                        if project_configurations is None or project_configurations is ...:
                            project_configurations = []
                    #
                    for project_configuration in project_configurations:
                        log.info("Project %s configuration: %s", project_id, project_configuration)
                        #
                        self.delete_configuration_entities(project_configuration)
                        self.make_configuration_entities(project_configuration)
                    #
                    # Integrations
                    #
                    try:
                        if failed_integration_calls < 3:
                            project_ai_integrations = context.rpc_manager.timeout(
                                5
                            ).integrations_get_project_integrations_by_section(
                                project_id, "ai",
                            )
                        else:
                            project_ai_integrations = []
                    except:  # pylint: disable=W0702
                        project_ai_integrations = []
                        failed_integration_calls += 1
                    else:
                        if project_ai_integrations is None or project_ai_integrations is ...:
                            project_ai_integrations = []
                    #
                    for project_ai_integration in project_ai_integrations:
                        integration_payload = {
                            "mode": "default",
                            "project_id": project_id,
                            "integration_name": project_ai_integration.name,
                            "integration_data": serialize(project_ai_integration),
                        }
                        #
                        log.info("Project %s AI integration: %s", project_id, integration_payload)
                        #
                        self.delete_integration_entities(integration_payload)
                        self.make_integration_entities(integration_payload)
            except:  # pylint: disable=W0702
                log.exception("Got exception, stopping")
            #
            try:
                log.info("Syncing admin LLM entities")
                #
                try:
                    admin_ai_integrations = context.rpc_manager.timeout(
                        5
                    ).integrations_get_administration_integrations_by_section(
                        "ai",
                    )
                except:  # pylint: disable=W0702
                    admin_ai_integrations = []
                else:
                    if admin_ai_integrations is None or admin_ai_integrations is ...:
                        admin_ai_integrations = []
                #
                for admin_ai_integration in admin_ai_integrations:
                    integration_payload = {
                        "mode": "administration",
                        "project_id": None,
                        "integration_name": admin_ai_integration.name,
                        "integration_data": serialize(admin_ai_integration),
                    }
                    #
                    log.info("Admin AI integration: %s", integration_payload)
                    #
                    self.delete_integration_entities(integration_payload)
                    self.make_integration_entities(integration_payload)
            except:  # pylint: disable=W0702
                log.exception("Got exception, stopping")
            #
            end_ts = time.time()
            log.info("Exiting (duration = %s)", end_ts - start_ts)

    # pylint: disable=R,W0613
    @web.method()
    def import_llm_models(self, *args, **kwargs):
        """Discover unmanaged models in LiteLLM and create Configuration records for all projects."""
        with make_logger() as log:
            log.info("Starting LLM model import from LiteLLM")
            start_ts = time.time()
            #
            try:
                import re as _re  # pylint: disable=C0415
                models = self.service_node.call.litellm_api_call("model_info")
                #
                public_project_id = self.get_public_project_id()
                imported_count = 0
                #
                for m in (models or []):
                    info = m.get("model_info") or {}
                    name = m.get("model_name", "")
                    #
                    # Skip managed models (already have Elitea configuration)
                    if info.get("centry_integration_uid") \
                            or info.get("centry_configuration_uuid"):
                        continue
                    if _re.match(r'^\d+_', name):
                        continue
                    #
                    # Build elitea_title: alphanumeric + underscores, lowercase
                    elitea_title = _re.sub(r'[^a-z0-9_]', '_', name.lower())
                    #
                    # Extract model capabilities from model_info
                    max_output = info.get("max_output_tokens") \
                        or info.get("max_tokens") or 16000
                    max_input = info.get("max_input_tokens") or 128000
                    mode = info.get("mode", "chat")
                    #
                    # Determine config type from mode
                    if mode == "embedding":
                        config_type = "embedding_model"
                        config_data = {"name": name}
                    elif mode == "image_generation":
                        config_type = "image_generation_model"
                        config_data = {"name": name}
                    else:
                        config_type = "llm_model"
                        config_data = {
                            "name": name,
                            "context_window": max_input,
                            "max_output_tokens": max_output,
                            "supports_reasoning": False,
                            "supports_vision": True,
                        }
                    #
                    payload = {
                        "project_id": public_project_id,
                        "elitea_title": elitea_title,
                        "label": name,
                        "type": config_type,
                        "shared": True,
                        "source": "system",
                        "status_ok": True,
                        "data": config_data,
                    }
                    #
                    try:
                        result, created = context.rpc_manager.timeout(
                            10
                        ).configurations_create_if_not_exists(payload)
                        #
                        if created:
                            # ConfigurationCreate doesn't have status_ok field,
                            # so we must update it separately after creation
                            context.rpc_manager.timeout(5).configurations_update(
                                project_id=public_project_id,
                                config_id=result["id"],
                                payload={"status_ok": True},
                            )
                            imported_count += 1
                            log.info("Created configuration for model: %s", name)
                        else:
                            log.info("Configuration already exists for model: %s", name)
                    except:  # pylint: disable=W0702
                        log.exception(
                            "Failed to create configuration for model: %s", name,
                        )
                #
                log.info(
                    "Import complete: %d new configuration(s) created", imported_count,
                )
            except:  # pylint: disable=W0702
                log.exception("Got exception during model import")
            #
            # Update existing teams with non-prefixed models
            #
            try:
                import re as _re2  # pylint: disable=C0415
                #
                non_prefixed_models = set()
                all_models = self.service_node.call.litellm_api_call("model_info")
                for m in (all_models or []):
                    name = m.get("model_name", "")
                    if name and not _re2.match(r'^\d+_', name):
                        non_prefixed_models.add(name)
                #
                if non_prefixed_models:
                    log.info(
                        "Updating team access for %d non-prefixed models",
                        len(non_prefixed_models),
                    )
                    #
                    teams = self.service_node.call.litellm_api_call("team_list")
                    updated_count = 0
                    #
                    for team in teams:
                        existing_models = set(team.get("models", []))
                        missing_models = non_prefixed_models - existing_models
                        #
                        if missing_models:
                            try:
                                self.service_node.call.litellm_api_call(
                                    "team_model_add",
                                    team["team_id"],
                                    sorted(missing_models),
                                )
                                updated_count += 1
                            except:  # pylint: disable=W0702
                                log.exception(
                                    "Failed to update team %s",
                                    team.get("team_alias"),
                                )
                    #
                    log.info("Updated %d teams with new model access", updated_count)
                else:
                    log.info("No non-prefixed models found, skipping team update")
            except:  # pylint: disable=W0702
                log.exception("Got exception during team model update")
            #
            end_ts = time.time()
            log.info("Exiting (duration = %s)", end_ts - start_ts)

    # pylint: disable=R,W0613
    @web.method()
    def seed_llm_keys(self, *args, **kwargs):
        """Seed LiteLLM teams and API keys for all projects. Creates missing team/key pairs, updates existing teams with new models, and stores keys in vault."""
        with make_logger() as log:
            log.info("Starting LLM key seeding")
            start_ts = time.time()
            #
            try:
                import re as _re  # pylint: disable=C0415
                #
                # Get non-prefixed (imported/external) model names from LiteLLM
                #
                non_prefixed_models = set()
                try:
                    models = self.service_node.call.litellm_api_call("model_info")
                    for m in (models or []):
                        name = m.get("model_name", "")
                        if name and not _re.match(r'^\d+_', name):
                            non_prefixed_models.add(name)
                except:  # pylint: disable=W0702
                    log.exception("Failed to get model list from LiteLLM")
                #
                log.info("Found %d non-prefixed models", len(non_prefixed_models))
                #
                # Get existing teams
                #
                log.info("Getting team list from LiteLLM")
                teams = self.service_node.call.litellm_api_call(
                    "team_list",
                )
                #
                team_by_alias = {}
                for team in teams:
                    team_by_alias[team["team_alias"]] = team
                #
                log.info("Found %d existing teams", len(team_by_alias))
                #
                # Update existing teams with missing non-prefixed models
                #
                updated_count = 0
                if non_prefixed_models:
                    for team in teams:
                        existing_models = set(team.get("models", []))
                        missing_models = non_prefixed_models - existing_models
                        #
                        if missing_models:
                            try:
                                self.service_node.call.litellm_api_call(
                                    "team_model_add",
                                    team["team_id"],
                                    sorted(missing_models),
                                )
                                updated_count += 1
                            except:  # pylint: disable=W0702
                                log.exception(
                                    "Failed to update team %s", team.get("team_alias"),
                                )
                    #
                    log.info("Updated %d existing teams with new models", updated_count)
                #
                # Create teams and keys for new projects
                #
                log.info("Getting project list")
                project_list = context.rpc_manager.timeout(120).project_list(
                    filter_={"create_success": True},
                )
                #
                created_count = 0
                skipped_count = 0
                #
                for project in project_list:
                    project_id = project["id"]
                    team_name = f"project_{project_id}"
                    #
                    if team_name in team_by_alias:
                        skipped_count += 1
                        continue
                    #
                    try:
                        log.info("Creating team and key for project %s", project_id)
                        self.make_project_entities(project_id)
                        created_count += 1
                    except:  # pylint: disable=W0702
                        log.exception(
                            "Failed to create team/key for project %s", project_id,
                        )
                #
                log.info(
                    "Seeding complete: %d created, %d already existed, %d updated with new models",
                    created_count, skipped_count, updated_count,
                )
            except:  # pylint: disable=W0702
                log.exception("Got exception, stopping")
            #
            end_ts = time.time()
            log.info("Exiting (duration = %s)", end_ts - start_ts)
