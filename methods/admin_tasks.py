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

from tools import context  # pylint: disable=E0401


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
                # but every token failed to parse AND did not explicitly request
                # 'all', refuse to silently delete the whole proxy.
                #
                if opts["scope_requested"] \
                        and not project_ids \
                        and not opts["scope_all_requested"]:
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
        """Sync LiteLLM proxy entities (teams, keys, models) for all projects and their configurations. Long-running.

        Param format (optional):
            "[project_id=<all|N>;][project_ids=A,B,C;][dry_run]"

        Examples:
            ""                              - sync all projects
            "dry_run"                       - log planned actions, mutate nothing
            "project_id=34"                 - sync project 34 only
            "project_ids=34,42"             - sync projects 34 and 42
            "project_id=34;dry_run"         - dry run for project 34
            "project_id=all;dry_run"        - explicit system-wide dry run
        """
        with make_logger() as log:
            opts = self.parse_admin_task_param(kwargs.get("param", ""))
            dry_run = opts["dry_run"]
            project_ids_filter = opts["project_ids"]
            prefix = "[DRY RUN] " if dry_run else ""
            #
            log.info(
                "%sStarting (project_ids=%s dry_run=%s)",
                prefix, project_ids_filter, dry_run,
            )
            start_ts = time.time()
            #
            # Safety: scoped param given but every token failed to parse AND
            # the operator did not explicitly request 'all' — refuse to
            # silently widen blast radius to system-wide.
            #
            if opts["scope_requested"] \
                    and not project_ids_filter \
                    and not opts["scope_all_requested"]:
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
            try:
                log.info("%sSyncing project LLM entities", prefix)
                #
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
                    # Skip configurations for non-public projects when own LLMs disabled
                    #
                    if not allow_own and project_id != public_project_id:
                        log.info(
                            "Skipping configurations for project %s "
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
        """Sweep LiteLLM for orphaned models/credentials whose backing Centry configuration no longer exists.

        A LiteLLM model or credential is considered orphaned when it carries a
        ``centry_configuration_uuid`` marker that does not appear in the current
        set of live project configurations. Entries without that marker are
        treated as externally managed and left alone.

        The live set is ALWAYS built from the full system (all projects)
        regardless of scope, so we never misclassify another project's entity
        as orphaned. ``project_id``/``project_ids`` only restrict WHICH
        orphans get deleted (by ``{N}_`` name prefix), to keep the blast
        radius small for targeted cleanups.

        Safety: if the configurations RPC fails for too many projects, the
        live set is incomplete and the sweep aborts without deletions to
        avoid wiping real models.

        Param format (optional):
            "[project_id=<all|N>;][project_ids=A,B,C;][dry_run]"

        Examples:
            ""                              - sweep all orphans system-wide (destructive)
            "dry_run"                       - dry run system-wide
            "project_id=14002;dry_run"      - dry run, only consider models/credentials prefixed '14002_'
            "project_id=14002"              - delete orphans prefixed '14002_'
            "project_id=all;dry_run"        - explicit system-wide dry run

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
            # Safety: scoped param given but every token failed to parse AND
            # the operator did not explicitly request 'all' — refuse to
            # silently widen blast radius to system-wide.
            #
            if opts["scope_requested"] \
                    and not project_ids_filter \
                    and not opts["scope_all_requested"]:
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
            # --- Build live set (system-wide) ---
            #
            live_configuration_uuids = set()
            #
            # If too many configurations_get_filtered_project calls fail, the
            # live set is incomplete and would misclassify real models as
            # orphan. We abort instead of guessing.
            #
            failed_configuration_calls = 0
            attempted_configuration_calls = 0
            #
            try:
                log.info("Collecting project list")
                project_list = context.rpc_manager.timeout(120).project_list(
                    filter_={"create_success": True},
                ) or []
                #
                for project in project_list:
                    project_id = project["id"]
                    #
                    attempted_configuration_calls += 1
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
                        failed_configuration_calls += 1
                        proj_cfgs = []
                    if proj_cfgs is None or proj_cfgs is ...:
                        proj_cfgs = []
                    for cfg in proj_cfgs:
                        uuid = cfg.get("uuid") if isinstance(cfg, dict) else None
                        if uuid:
                            live_configuration_uuids.add(uuid)
                #
                log.info(
                    "Live set: %d configuration UUIDs "
                    "(configurations RPC: %d ok / %d failed of %d projects)",
                    len(live_configuration_uuids),
                    attempted_configuration_calls - failed_configuration_calls,
                    failed_configuration_calls,
                    attempted_configuration_calls,
                )
                #
                # If we couldn't reach the configurations RPC for a meaningful
                # share of projects, abort. We pick a conservative threshold:
                # more than 25% of attempts failing OR every attempt failing.
                #
                if attempted_configuration_calls > 0 and (
                    failed_configuration_calls == attempted_configuration_calls
                    or failed_configuration_calls * 4 > attempted_configuration_calls
                ):
                    log.error(
                        "Aborting orphan sweep: configurations RPC failed for "
                        "%d/%d project(s). Live set is incomplete; deleting "
                        "now would risk wiping real models. Re-run when the "
                        "configurations plugin is reachable.",
                        failed_configuration_calls, attempted_configuration_calls,
                    )
                    end_ts = time.time()
                    log.info("%sExiting (duration = %s)", prefix, end_ts - start_ts)
                    return
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
            unmanaged_integration_models = 0
            try:
                models = self.service_node.call.litellm_api_call("model_info") or []
                for model in models:
                    info = model.get("model_info") or {}
                    name = model.get("model_name", "") or ""
                    #
                    uuid = info.get("centry_configuration_uuid")
                    uid = info.get("centry_integration_uid")
                    #
                    # Skip entries with only an integration marker — the
                    # current platform has no integration liveness source, so
                    # we cannot decide whether they are orphan or live.
                    # Treat them as unmanaged from this sweep's perspective.
                    #
                    if uid and not uuid:
                        unmanaged_integration_models += 1
                        continue
                    #
                    if not uuid:
                        continue  # externally managed; not our concern
                    #
                    if uuid in live_configuration_uuids:
                        continue
                    #
                    # Scope filter (by name prefix)
                    if scope is not None and not any(name.startswith(p) for p in scope):
                        continue
                    #
                    orphan_models += 1
                    log.warning(
                        "%sOrphan model: name=%s uuid=%s",
                        prefix, name, uuid,
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
            unmanaged_integration_credentials = 0
            try:
                credentials = self.service_node.call.litellm_api_call("credential_list") or []
                for credential in credentials:
                    cred_name = credential.get("credential_name", "") or ""
                    cred_info = credential.get("credential_info") or {}
                    #
                    uuid = cred_info.get("centry_configuration_uuid")
                    uid = cred_info.get("centry_integration_uid")
                    #
                    if uid and not uuid:
                        unmanaged_integration_credentials += 1
                        continue
                    #
                    if not uuid:
                        continue
                    #
                    if uuid in live_configuration_uuids:
                        continue
                    #
                    if scope is not None and not any(cred_name.startswith(p) for p in scope):
                        continue
                    #
                    orphan_credentials += 1
                    log.warning(
                        "%sOrphan credential: name=%s uuid=%s",
                        prefix, cred_name, uuid,
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
                "%sOrphan sweep complete: models orphan=%d deleted=%d unmanaged_integration=%d; "
                "credentials orphan=%d deleted=%d unmanaged_integration=%d",
                prefix,
                orphan_models, deleted_models, unmanaged_integration_models,
                orphan_credentials, deleted_credentials, unmanaged_integration_credentials,
            )
            end_ts = time.time()
            log.info("%sExiting (duration = %s)", prefix, end_ts - start_ts)
