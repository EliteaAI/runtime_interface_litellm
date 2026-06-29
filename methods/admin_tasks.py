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
        """Delete LiteLLM proxy entities (teams, keys, models, credentials). Destructive.

        Param format (optional):
            "[project_id=<all|N>;][project_ids=A,B,C;][dry_run]"

        Examples:
            ""                              - delete ALL entities (default, destructive)
            "dry_run"                       - dry run, no mutations
            "project_id=34"                 - delete only project 34 entities (prefix '34_')
            "project_ids=34,42;dry_run"     - dry run for projects 34 and 42
            "project_id=all;dry_run"        - dry run across everything

        When scoped to project_ids, only entities with the matching '{N}_' prefix
        (and team alias 'project_{N}', key alias 'project_key_{N}') are removed.
        """
        with make_logger() as log:
            log.info("Starting")
            start_ts = time.time()
            #
            try:
                opts = self.parse_admin_task_param(kwargs.get("param", ""))
                dry_run = opts["dry_run"]
                project_ids = opts["project_ids"]
                prefix = "[DRY RUN] " if dry_run else ""
                #
                # Safety: if operator asked for scoped run (project_id/project_ids)
                # but every token failed to parse, refuse to silently delete the
                # whole proxy. They must use project_id=all explicitly.
                #
                if opts["scope_requested"] and not project_ids:
                    log.error(
                        "Refusing to run: scoped param was given but no valid "
                        "project ids parsed (errors: %s). Use project_id=all to "
                        "explicitly target everything.",
                        opts["scope_parse_errors"],
                    )
                    end_ts = time.time()
                    log.info("Exiting (duration = %s)", end_ts - start_ts)
                    return
                #
                if project_ids:
                    log.info(
                        "%sDeleting LLM entities for projects: %s", prefix, project_ids,
                    )
                    for project_id in project_ids:
                        try:
                            self.delete_project_entities(project_id, dry_run=dry_run)
                            log.info(
                                "%sProject %s: delete_project_entities done", prefix, project_id,
                            )
                        except:  # pylint: disable=W0702
                            log.exception(
                                "%sFailed to delete entities for project %s",
                                prefix, project_id,
                            )
                else:
                    log.info("%sDeleting ALL LLM entities", prefix)
                    if not dry_run:
                        self.delete_all_entities()
                    else:
                        # Enumerate-only for dry run
                        try:
                            keys = self.service_node.call.litellm_api_call("key_list") or []
                            teams = self.service_node.call.litellm_api_call("team_list") or []
                            models = self.service_node.call.litellm_api_call("model_info") or []
                            credentials = self.service_node.call.litellm_api_call("credential_list") or []
                            log.info(
                                "%sWould delete: keys=%d teams=%d models=%d credentials=%d",
                                prefix, len(keys), len(teams), len(models), len(credentials),
                            )
                        except:  # pylint: disable=W0702
                            log.exception("Dry-run enumeration failed")
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
        """Sync LiteLLM proxy entities (teams, keys, models) for all projects and their AI integrations. Long-running.

        Param format (optional):
            "[project_id=<all|N>;][project_ids=A,B,C;][include_admin=<true|false>;][dry_run]"

        Defaults:
            - Unscoped run: include_admin=True (full sync of project + admin integrations)
            - Scoped run (project_id/project_ids set): include_admin=False unless explicitly overridden

        Examples:
            ""                                          - sync everything (project + admin)
            "dry_run"                                   - log planned actions, mutate nothing
            "project_id=34"                             - sync project 34 only, skip admin
            "project_ids=34,42;include_admin=true"      - sync those two projects AND admin integrations
            "project_id=34;dry_run"                     - dry run for project 34
        """
        with make_logger() as log:
            opts = self.parse_admin_task_param(kwargs.get("param", ""))
            dry_run = opts["dry_run"]
            project_ids_filter = opts["project_ids"]
            include_admin = opts["include_admin"]
            prefix = "[DRY RUN] " if dry_run else ""
            #
            log.info(
                "%sStarting (project_ids=%s include_admin=%s dry_run=%s)",
                prefix, project_ids_filter, include_admin, dry_run,
            )
            start_ts = time.time()
            #
            try:
                log.info("%sSyncing project LLM entities", prefix)
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
                if project_ids_filter is not None:
                    scope = set(int(p) for p in project_ids_filter)
                    project_list = [p for p in project_list if int(p["id"]) in scope]
                    log.info(
                        "%sFiltered project list to %d project(s): %s",
                        prefix, len(project_list), sorted(scope),
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
                        log.info(
                            "%sCreating LLM team and key: %s - %s",
                            prefix, team_name, key_name,
                        )
                        if not dry_run:
                            self.make_project_entities(project_id)
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
                        log.info(
                            "%sProject %s configuration: %s",
                            prefix, project_id, project_configuration,
                        )
                        if not dry_run:
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
                        log.info(
                            "%sProject %s AI integration: %s",
                            prefix, project_id, integration_payload,
                        )
                        if not dry_run:
                            self.delete_integration_entities(integration_payload)
                            self.make_integration_entities(integration_payload)
            except:  # pylint: disable=W0702
                log.exception("Got exception, stopping")
            #
            if not include_admin:
                log.info(
                    "%sSkipping admin LLM entities sync (include_admin=False)", prefix,
                )
            else:
                try:
                    log.info("%sSyncing admin LLM entities", prefix)
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
                        log.info("%sAdmin AI integration: %s", prefix, integration_payload)
                        if not dry_run:
                            self.delete_integration_entities(integration_payload)
                            self.make_integration_entities(integration_payload)
                except:  # pylint: disable=W0702
                    log.exception("Got exception, stopping")
            #
            end_ts = time.time()
            log.info("%sExiting (duration = %s)", prefix, end_ts - start_ts)

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
        """Seed LiteLLM teams and API keys for projects. Creates missing team/key pairs, updates existing teams with new models, and stores keys in vault.

        Param format (optional):
            "[project_id=<all|N>;][project_ids=A,B,C;][dry_run]"

        When scoped to project_ids, the team-with-non-prefixed-models update is
        also limited to those teams (so a scoped run never touches other teams).

        Examples:
            ""                              - seed all projects, update all teams
            "dry_run"                       - log planned actions only
            "project_id=34"                 - seed project 34 only, update its team
            "project_ids=34,42;dry_run"     - dry run for projects 34, 42
        """
        with make_logger() as log:
            opts = self.parse_admin_task_param(kwargs.get("param", ""))
            dry_run = opts["dry_run"]
            project_ids_filter = opts["project_ids"]
            prefix = "[DRY RUN] " if dry_run else ""
            #
            log.info(
                "%sStarting LLM key seeding (project_ids=%s dry_run=%s)",
                prefix, project_ids_filter, dry_run,
            )
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
                # Build scope-aware team filter for the "add new models to teams" step
                #
                if project_ids_filter is not None:
                    scoped_team_aliases = {f"project_{int(pid)}" for pid in project_ids_filter}
                    teams_to_update = [t for t in teams if t.get("team_alias") in scoped_team_aliases]
                else:
                    teams_to_update = teams
                #
                # Update teams with missing non-prefixed models
                #
                updated_count = 0
                if non_prefixed_models:
                    for team in teams_to_update:
                        existing_models = set(team.get("models", []))
                        missing_models = non_prefixed_models - existing_models
                        #
                        if missing_models:
                            log.info(
                                "%sAdding %d models to team %s",
                                prefix, len(missing_models), team.get("team_alias"),
                            )
                            if not dry_run:
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
                            else:
                                updated_count += 1
                    #
                    log.info(
                        "%sUpdated %d teams with new models", prefix, updated_count,
                    )
                #
                # Create teams and keys for new projects
                #
                log.info("Getting project list")
                project_list = context.rpc_manager.timeout(120).project_list(
                    filter_={"create_success": True},
                )
                #
                if project_ids_filter is not None:
                    scope = set(int(p) for p in project_ids_filter)
                    project_list = [p for p in project_list if int(p["id"]) in scope]
                    log.info(
                        "%sFiltered project list to %d project(s): %s",
                        prefix, len(project_list), sorted(scope),
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
                    log.info("%sCreating team and key for project %s", prefix, project_id)
                    if not dry_run:
                        try:
                            self.make_project_entities(project_id)
                            created_count += 1
                        except:  # pylint: disable=W0702
                            log.exception(
                                "Failed to create team/key for project %s", project_id,
                            )
                    else:
                        created_count += 1
                #
                log.info(
                    "%sSeeding complete: %d created, %d already existed, %d updated with new models",
                    prefix, created_count, skipped_count, updated_count,
                )
            except:  # pylint: disable=W0702
                log.exception("Got exception, stopping")
            #
            end_ts = time.time()
            log.info("%sExiting (duration = %s)", prefix, end_ts - start_ts)

    # pylint: disable=R,W0613
    @web.method()
    def cleanup_llm_orphans(self, *args, **kwargs):
        """Sweep LiteLLM for orphaned models/credentials whose backing Centry entity no longer exists.

        A LiteLLM model or credential is considered orphaned when it carries a
        Centry ownership marker (``centry_integration_uid`` or
        ``centry_configuration_uuid``) that does not appear in the current set
        of live integrations/configurations. Entries without any Centry marker
        are treated as externally managed and left alone.

        The live set is ALWAYS built from the full system (all projects + admin
        integrations) regardless of scope, so we never misclassify another
        project's entity as orphaned. ``project_id``/``project_ids`` only
        restrict WHICH orphans get deleted (by ``{N}_`` name prefix), to keep
        the blast radius small for targeted cleanups.

        Param format (optional):
            "[project_id=<all|N>;][project_ids=A,B,C;][dry_run]"

        Examples:
            ""                              - sweep all orphans system-wide (destructive)
            "dry_run"                       - dry run system-wide
            "project_id=14002;dry_run"      - dry run, only consider models/credentials prefixed '14002_'
            "project_id=14002"              - delete orphans prefixed '14002_'

        Always run with dry_run first.
        """
        with make_logger() as log:
            opts = self.parse_admin_task_param(kwargs.get("param", ""))
            dry_run = opts["dry_run"]
            project_ids_filter = opts["project_ids"]
            prefix = "[DRY RUN] " if dry_run else ""
            #
            log.info(
                "%sStarting LLM orphan sweep (project_ids=%s dry_run=%s)",
                prefix, project_ids_filter, dry_run,
            )
            start_ts = time.time()
            #
            # Safety: scoped param given but every token failed to parse — refuse
            # to silently widen blast radius to system-wide.
            #
            if opts["scope_requested"] and not project_ids_filter:
                log.error(
                    "Refusing to run: scoped param was given but no valid "
                    "project ids parsed (errors: %s). Use project_id=all to "
                    "explicitly target everything.",
                    opts["scope_parse_errors"],
                )
                end_ts = time.time()
                log.info("%sExiting (duration = %s)", prefix, end_ts - start_ts)
                return
            #
            scope = None
            if project_ids_filter is not None:
                scope = {f"{int(pid)}_" for pid in project_ids_filter}
                log.info("%sScope (name prefixes): %s", prefix, sorted(scope))
            #
            # --- Build live sets (system-wide) ---
            #
            live_integration_uids = set()
            live_configuration_uuids = set()
            #
            # If the integrations RPC never succeeds, an empty live_integration_uids
            # would misclassify EVERY integration-backed model as orphan. Track
            # health and refuse to delete integration-marked entries when unhealthy.
            #
            integration_rpcs_healthy = False
            #
            try:
                log.info("Collecting admin AI integrations")
                try:
                    admin_ai = context.rpc_manager.timeout(
                        10
                    ).integrations_get_administration_integrations_by_section("ai")
                    integration_rpcs_healthy = True
                except:  # pylint: disable=W0702
                    log.exception("Failed to list admin AI integrations")
                    admin_ai = []
                if admin_ai is None or admin_ai is ...:
                    admin_ai = []
                for integration in admin_ai:
                    try:
                        live_integration_uids.add(serialize(integration).get("uid"))
                    except:  # pylint: disable=W0702
                        log.exception("Failed to serialize admin integration")
                #
                log.info("Collecting project list")
                project_list = context.rpc_manager.timeout(120).project_list(
                    filter_={"create_success": True},
                ) or []
                #
                for project in project_list:
                    project_id = project["id"]
                    #
                    # Project integrations
                    try:
                        proj_ai = context.rpc_manager.timeout(
                            5
                        ).integrations_get_project_integrations_by_section(project_id, "ai")
                        integration_rpcs_healthy = True
                    except:  # pylint: disable=W0702
                        log.exception(
                            "Failed to list AI integrations for project %s", project_id,
                        )
                        proj_ai = []
                    if proj_ai is None or proj_ai is ...:
                        proj_ai = []
                    for integration in proj_ai:
                        try:
                            live_integration_uids.add(serialize(integration).get("uid"))
                        except:  # pylint: disable=W0702
                            log.exception(
                                "Failed to serialize integration for project %s", project_id,
                            )
                    #
                    # Project configurations
                    # NB: filter_fields is a SQLAlchemy filter_by dict, NOT a
                    # projection; passing a list silently fails the RPC and
                    # would empty the live set, misclassifying every
                    # configuration-backed model as orphan.
                    try:
                        proj_cfgs = context.rpc_manager.timeout(
                            5
                        ).configurations_get_filtered_project(
                            project_id=project_id,
                            include_shared=False,
                        )
                    except:  # pylint: disable=W0702
                        log.exception(
                            "Failed to list configurations for project %s", project_id,
                        )
                        proj_cfgs = []
                    if proj_cfgs is None or proj_cfgs is ...:
                        proj_cfgs = []
                    for cfg in proj_cfgs:
                        uuid = cfg.get("uuid") if isinstance(cfg, dict) else None
                        if uuid:
                            live_configuration_uuids.add(uuid)
                #
                # Discard None values from serialize() that lacked uid
                live_integration_uids.discard(None)
                live_integration_uids.discard("")
                #
                log.info(
                    "Live set: %d integration UIDs, %d configuration UUIDs "
                    "(integration RPCs healthy=%s)",
                    len(live_integration_uids), len(live_configuration_uuids),
                    integration_rpcs_healthy,
                )
                if not integration_rpcs_healthy:
                    log.warning(
                        "Integration RPCs never succeeded during live-set build. "
                        "Integration-marked entries will be SKIPPED (not classified "
                        "as orphan) for this run to prevent false positives. "
                        "Configuration-marked entries will still be evaluated.",
                    )
            except:  # pylint: disable=W0702
                log.exception("Failed to build live set; aborting (no deletions performed)")
                end_ts = time.time()
                log.info("%sExiting (duration = %s)", prefix, end_ts - start_ts)
                return
            #
            # --- Sweep LiteLLM models ---
            #
            orphan_models = 0
            deleted_models = 0
            skipped_unhealthy_models = 0
            try:
                models = self.service_node.call.litellm_api_call("model_info") or []
                for model in models:
                    info = model.get("model_info") or {}
                    name = model.get("model_name", "") or ""
                    #
                    uid = info.get("centry_integration_uid")
                    uuid = info.get("centry_configuration_uuid")
                    #
                    if not uid and not uuid:
                        continue  # externally managed; not our concern
                    #
                    # Safety: if integration RPCs failed, the live integration set
                    # is unreliable. Skip integration-only entries to avoid false
                    # positives. Entries also carrying a configuration uuid still
                    # get evaluated against the (healthy) configuration live set.
                    #
                    if uid and not uuid and not integration_rpcs_healthy:
                        skipped_unhealthy_models += 1
                        log.info(
                            "%sSkipping integration-marked model %s (uid=%s): "
                            "integration RPCs unhealthy this run",
                            prefix, name, uid,
                        )
                        continue
                    #
                    is_orphan = (uid and uid not in live_integration_uids) \
                        or (uuid and uuid not in live_configuration_uuids)
                    if not is_orphan:
                        continue
                    #
                    # Scope filter (by name prefix)
                    if scope is not None and not any(name.startswith(p) for p in scope):
                        continue
                    #
                    orphan_models += 1
                    log.warning(
                        "%sOrphan model: name=%s uid=%s uuid=%s",
                        prefix, name, uid, uuid,
                    )
                    if not dry_run:
                        model_id = info.get("id")
                        if not model_id:
                            log.error(
                                "Cannot delete orphan model %s: missing model_info.id",
                                name,
                            )
                            continue
                        try:
                            self.service_node.call.litellm_api_call(
                                "model_delete",
                                model_id,
                            )
                            deleted_models += 1
                        except:  # pylint: disable=W0702
                            log.exception("Failed to delete orphan model %s", name)
            except:  # pylint: disable=W0702
                log.exception("Failed to enumerate models")
            #
            # --- Sweep LiteLLM credentials ---
            #
            orphan_credentials = 0
            deleted_credentials = 0
            skipped_unhealthy_credentials = 0
            try:
                credentials = self.service_node.call.litellm_api_call("credential_list") or []
                for credential in credentials:
                    cred_name = credential.get("credential_name", "") or ""
                    cred_info = credential.get("credential_info") or {}
                    #
                    uid = cred_info.get("centry_integration_uid")
                    uuid = cred_info.get("centry_configuration_uuid")
                    #
                    if not uid and not uuid:
                        continue
                    #
                    # Same safety rule as models: skip integration-only entries
                    # if the integrations RPC was unreachable this run.
                    #
                    if uid and not uuid and not integration_rpcs_healthy:
                        skipped_unhealthy_credentials += 1
                        log.info(
                            "%sSkipping integration-marked credential %s (uid=%s): "
                            "integration RPCs unhealthy this run",
                            prefix, cred_name, uid,
                        )
                        continue
                    #
                    is_orphan = (uid and uid not in live_integration_uids) \
                        or (uuid and uuid not in live_configuration_uuids)
                    if not is_orphan:
                        continue
                    #
                    if scope is not None and not any(cred_name.startswith(p) for p in scope):
                        continue
                    #
                    orphan_credentials += 1
                    log.warning(
                        "%sOrphan credential: name=%s uid=%s uuid=%s",
                        prefix, cred_name, uid, uuid,
                    )
                    if not dry_run:
                        try:
                            self.service_node.call.litellm_api_call(
                                "credential_delete", cred_name,
                            )
                            deleted_credentials += 1
                        except:  # pylint: disable=W0702
                            log.exception(
                                "Failed to delete orphan credential %s", cred_name,
                            )
            except:  # pylint: disable=W0702
                log.exception("Failed to enumerate credentials")
            #
            log.info(
                "%sOrphan sweep complete: models orphan=%d deleted=%d skipped_unhealthy=%d; "
                "credentials orphan=%d deleted=%d skipped_unhealthy=%d",
                prefix,
                orphan_models, deleted_models, skipped_unhealthy_models,
                orphan_credentials, deleted_credentials, skipped_unhealthy_credentials,
            )
            end_ts = time.time()
            log.info("%sExiting (duration = %s)", prefix, end_ts - start_ts)
