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

""" RPC """

import datetime

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import context  # pylint: disable=E0401

from ..methods.budgets import make_budget_tag, make_user_budget_tag


# Activity is paginated by spend record — one per tag, day, model and key — so a page
# holds far fewer tags than it looks. Kept large to keep the page count low when the
# admin pages read many projects at once.
MAX_ACTIVITY_PAGE_SIZE = 1000

# Tags are sent as one comma-joined query parameter, so the whole list cannot go in a
# single request: a large environment would build a URL megabytes long and be rejected
# before reaching LiteLLM. At ~25 bytes per tag this keeps the URL near 5KB, inside the
# 8KB most proxies allow.
MAX_TAGS_PER_ACTIVITY_CALL = 200

# Pages per chunk. A chunk of the size above yields a few thousand records in a busy
# month, so this leaves ample headroom while still bounding a runaway paging loop.
MAX_ACTIVITY_PAGES = 50


class RPC:  # pylint: disable=E1101,R0903,W0201
    """ RPC Resource """

    @web.rpc("litellm_push_project_budget", "litellm_push_project_budget")
    def litellm_push_project_budget(self, project_id, **kwargs):
        """Push a project's current monthly limit to its LiteLLM tag budget."""
        self.invalidate_budget_tag_cache(project_id)
        #
        if not self.budgets_enabled():
            return False
        #
        tag_name = make_budget_tag(project_id)
        self.ensure_budget_tag(project_id, tag_name, self.get_project_budget_limit)
        #
        return True

    @web.rpc("litellm_push_user_budget", "litellm_push_user_budget")
    def litellm_push_user_budget(self, project_id, user_id, **kwargs):
        """Push a per-user monthly limit to its LiteLLM tag budget."""
        tag_name = make_user_budget_tag(project_id, user_id)
        #
        self.invalidate_budget_tag(tag_name)
        #
        if not self.budgets_enabled():
            return False
        #
        self.ensure_budget_tag(
            project_id, tag_name,
            lambda pid: self.get_user_budget_limit(pid, user_id),
        )
        #
        return True

    @web.rpc("litellm_get_effective_project_limit", "litellm_get_effective_project_limit")
    def litellm_get_effective_project_limit(self, project_id, **kwargs):
        """Limit actually enforced for a project, including any platform default."""
        return self.get_project_budget_limit(project_id)

    @web.rpc("litellm_get_user_spend", "litellm_get_user_spend")
    def litellm_get_user_spend(self, project_id, user_id, **kwargs):
        """Read current-month spend for one user within a project."""
        now = datetime.datetime.now(datetime.timezone.utc)
        #
        return self.read_tag_spend(make_user_budget_tag(project_id, user_id), now)

    @web.rpc("litellm_get_project_spend", "litellm_get_project_spend")
    def litellm_get_project_spend(self, project_id, **kwargs):
        """Read current-month spend for a project from LiteLLM's per-tag daily aggregate.

        Reads the daily-aggregate endpoint rather than raw spend logs, which are
        subject to a retention setting we do not control.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        #
        return self.read_tag_spend(make_budget_tag(project_id, now), now)

    @web.rpc("litellm_get_effective_project_limits", "litellm_get_effective_project_limits")
    def litellm_get_effective_project_limits(self, project_ids, **kwargs):
        """Effective limits for many projects, keyed by project id.

        Reads every stored budget in one query rather than asking per project: the admin
        pages list whole environments, where a per-project lookup is thousands of
        cross-plugin calls. Resolution below must stay identical to the single-project
        get_project_budget_limit, which enforcement still uses on the request path.
        """
        try:
            budgets = context.rpc_manager.timeout(15).elitea_core_list_project_budgets() or {}
        except:  # pylint: disable=W0702
            log.exception("Failed to list project budgets")
            return {pid: None for pid in project_ids}
        #
        result = {}
        #
        # Iterate the requested ids, not the budget map: a project with no stored row
        # still has to fall through to the configured default.
        for project_id in project_ids:
            budget = budgets.get(project_id, budgets.get(str(project_id)))
            #
            if budget is not None:
                if not budget.get("enabled", True):
                    result[project_id] = None
                    continue
                #
                if budget.get("monthly_limit") is not None:
                    result[project_id] = budget["monthly_limit"]
                    continue
            #
            result[project_id] = self.get_default_limit("project", project_id)
        #
        return result

    @web.rpc("litellm_get_effective_user_limits", "litellm_get_effective_user_limits")
    def litellm_get_effective_user_limits(self, project_id, user_ids, **kwargs):
        """Effective per-user limits within a project, keyed by user id.

        The project row holds the member default every unset member falls back to, so it is
        read once here rather than per member — the member list can be a whole project.
        """
        try:
            project_budget = context.rpc_manager.timeout(5).elitea_core_get_project_budget(
                project_id=project_id,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to get budget for project %s", project_id)
            project_budget = None
        #
        return {
            uid: self.get_user_budget_limit(project_id, uid, project_budget)
            for uid in user_ids
        }

    @web.rpc("litellm_get_projects_spend", "litellm_get_projects_spend")
    def litellm_get_projects_spend(self, project_ids, **kwargs):
        """Current-month spend for many projects in ONE LiteLLM call.

        Avoids a per-row request when the admin UI lists every project.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        #
        tags = {make_budget_tag(pid, now): pid for pid in project_ids}
        spend_by_tag = self.read_tags_spend(list(tags), now)
        #
        return {pid: spend_by_tag.get(tag, 0.0) for tag, pid in tags.items()}

    @web.rpc("litellm_get_users_spend", "litellm_get_users_spend")
    def litellm_get_users_spend(self, project_id, user_ids, **kwargs):
        """Current-month spend for many users of one project in ONE LiteLLM call."""
        now = datetime.datetime.now(datetime.timezone.utc)
        #
        tags = {make_user_budget_tag(project_id, uid, now): uid for uid in user_ids}
        spend_by_tag = self.read_tags_spend(list(tags), now)
        #
        return {uid: spend_by_tag.get(tag, 0.0) for tag, uid in tags.items()}

    @web.rpc("litellm_get_project_usage_detail", "litellm_get_project_usage_detail")
    def litellm_get_project_usage_detail(self, project_id, **kwargs):
        """Per-model and per-day current-month usage for a project."""
        now = datetime.datetime.now(datetime.timezone.utc)
        #
        return self.read_tag_usage_detail(make_budget_tag(project_id, now), now)

    @web.rpc("litellm_get_user_usage_detail", "litellm_get_user_usage_detail")
    def litellm_get_user_usage_detail(self, project_id, user_id, **kwargs):
        """Per-model and per-day current-month usage for one user in a project."""
        now = datetime.datetime.now(datetime.timezone.utc)
        #
        return self.read_tag_usage_detail(make_user_budget_tag(project_id, user_id, now), now)

    @web.method()
    def read_tag_usage_detail(self, tag_name, now):
        """Per-model and per-day breakdown for a single tag's current month.

        Reads one tag only: LiteLLM stores one row per tag per request, so asking for a
        project tag together with its own user tags would count the shared traffic twice.
        """
        period_start = now.replace(day=1)
        #
        result = {
            "tag": tag_name,
            "period": f"{now:%Y%m}",
            "models": [],
            "daily": [],
            "spend": 0.0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "api_requests": 0,
            "available": False,
        }
        #
        models = {}
        by_date = {}
        #
        # Paged for the same reason as read_tag_spend: one page carries only its own slice
        # of the month, so a busy tag would lose whole days off its chart and its total
        for page in range(1, MAX_ACTIVITY_PAGES + 1):
            try:
                activity = self.service_node.call.litellm_api_call(
                    "tag_daily_activity",
                    tags=[tag_name],
                    start_date=f"{period_start:%Y-%m-%d}",
                    end_date=f"{now:%Y-%m-%d}",
                    page=page,
                    page_size=MAX_ACTIVITY_PAGE_SIZE,
                )
            except:  # pylint: disable=W0702
                log.exception(
                    "Failed to read usage detail for tag %s at page %s", tag_name, page,
                )
                return result
            #
            for day in (activity or {}).get("results") or []:
                day_metrics = (day or {}).get("metrics") or {}
                date = (day or {}).get("date")
                #
                # A date can span pages, so days accumulate rather than being appended
                bucket = by_date.setdefault(
                    date, {
                        "date": date, "spend": 0.0, "total_tokens": 0,
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": 0, "cache_creation_tokens": 0,
                        "api_requests": 0,
                    },
                )
                bucket["spend"] += float(day_metrics.get("spend", 0) or 0)
                bucket["total_tokens"] += int(day_metrics.get("total_tokens", 0) or 0)
                bucket["input_tokens"] += int(day_metrics.get("prompt_tokens", 0) or 0)
                bucket["output_tokens"] += int(day_metrics.get("completion_tokens", 0) or 0)
                bucket["cache_read_tokens"] += int(day_metrics.get("cache_read_input_tokens", 0) or 0)
                bucket["cache_creation_tokens"] += int(day_metrics.get("cache_creation_input_tokens", 0) or 0)
                bucket["api_requests"] += int(day_metrics.get("successful_requests", 0) or 0)
                #
                breakdown = ((day or {}).get("breakdown") or {}).get("models") or {}
                #
                for model_name, entry in breakdown.items():
                    metrics = (entry or {}).get("metrics") or {}
                    model = models.setdefault(
                        model_name,
                        {"model": model_name, "spend": 0.0, "total_tokens": 0, "api_requests": 0},
                    )
                    #
                    model["spend"] += float(metrics.get("spend", 0) or 0)
                    model["total_tokens"] += int(metrics.get("total_tokens", 0) or 0)
                    # Rejected calls served nothing and cost nothing, so they are not usage.
                    # They also log under the pre-routing model name, which would otherwise
                    # split one model across two rows (e.g. "1_gpt-5" beside "gpt-5").
                    model["api_requests"] += int(metrics.get("successful_requests", 0) or 0)
            #
            if not ((activity or {}).get("metadata") or {}).get("has_more"):
                break
        else:
            log.warning(
                "Usage detail for tag %s hit the %s page ceiling; totals may be short",
                tag_name, MAX_ACTIVITY_PAGES,
            )
        #
        daily = sorted(by_date.values(), key=lambda item: item["date"] or "")
        #
        # Models with no spend and no calls only add noise to the UI
        model_rows = [row for row in models.values() if row["api_requests"] or row["spend"]]
        model_rows.sort(key=lambda row: row["spend"], reverse=True)
        #
        result["models"] = model_rows
        result["daily"] = daily
        result["spend"] = sum(row["spend"] for row in daily)
        result["total_tokens"] = sum(row["total_tokens"] for row in daily)
        result["input_tokens"] = sum(row["input_tokens"] for row in daily)
        result["output_tokens"] = sum(row["output_tokens"] for row in daily)
        result["cache_read_tokens"] = sum(row["cache_read_tokens"] for row in daily)
        result["cache_creation_tokens"] = sum(row["cache_creation_tokens"] for row in daily)
        result["api_requests"] = sum(row["api_requests"] for row in daily)
        result["available"] = True
        #
        return result

    @web.rpc("litellm_budgets_mode", "litellm_budgets_mode")
    def litellm_budgets_mode(self, **kwargs):
        """Current cost-budget mode, so the UI can hide the feature when it is off."""
        return self.budgets_mode()

    @web.rpc("litellm_get_warning_threshold", "litellm_get_warning_threshold")
    def litellm_get_warning_threshold(self, scope, **kwargs):
        """Configured warning percentage for a budget scope, for the Usage page."""
        return self.get_warning_threshold(scope)

    @web.rpc("litellm_get_budget_warning_state", "litellm_get_budget_warning_state")
    def litellm_get_budget_warning_state(self, project_id, user_id, **kwargs):
        """Whether to warn this user a budget is nearing its limit, for the run pages.

        Deliberately small: the run pages ask on every open, so this returns a percentage
        and nothing else -- no per-model or per-day breakdown to build.
        """
        return self.get_budget_warning_state(project_id, user_id)

    @web.method()
    def read_tags_spend(self, tag_names, now):
        """Map tag -> current-month spend for any number of tags.

        Tags travel in the query string, so the list is split into chunks small enough
        to keep the URL well inside what a proxy will accept. Each chunk is then read
        page by page, because the report paginates by raw spend record — one per tag,
        day, model and key — not by day, so even a few hundred tags span pages.
        """
        result = {tag: 0.0 for tag in tag_names}
        #
        if not tag_names:
            return result
        #
        tags = list(tag_names)
        #
        for start in range(0, len(tags), MAX_TAGS_PER_ACTIVITY_CALL):
            chunk = tags[start:start + MAX_TAGS_PER_ACTIVITY_CALL]
            #
            if not self.read_tags_spend_chunk(chunk, now, result):
                # One failed chunk means the total is short for those tags only; the rest
                # of the report is still worth returning
                log.warning("Spend read failed for a chunk of %s tags", len(chunk))
        #
        return result

    @web.method()
    def read_tags_spend_chunk(self, tag_names, now, result):
        """Accumulate one chunk's spend into result. False if the chunk failed."""
        period_start = now.replace(day=1)
        wanted = set(tag_names)
        #
        for page in range(1, MAX_ACTIVITY_PAGES + 1):
            try:
                activity = self.service_node.call.litellm_api_call(
                    "tag_daily_activity",
                    tags=tag_names,
                    start_date=f"{period_start:%Y-%m-%d}",
                    end_date=f"{now:%Y-%m-%d}",
                    page=page,
                    page_size=MAX_ACTIVITY_PAGE_SIZE,
                )
            except:  # pylint: disable=W0702
                log.exception(
                    "Failed to read spend for %s tags at page %s", len(tag_names), page,
                )
                #
                # Zero out this chunk: a partly-read chunk understates spend, which for a
                # budget check is worse than reporting nothing for those tags.
                for tag in wanted:
                    result[tag] = 0.0
                #
                return False
            #
            for day in (activity or {}).get("results") or []:
                entities = ((day or {}).get("breakdown") or {}).get("entities") or {}
                #
                for tag, entry in entities.items():
                    if tag not in wanted:
                        continue
                    #
                    metrics = (entry or {}).get("metrics") or entry or {}
                    result[tag] += float(metrics.get("spend", 0) or 0)
            #
            if not ((activity or {}).get("metadata") or {}).get("has_more"):
                return True
        #
        log.warning(
            "Spend for %s tags hit the %s page ceiling; totals may be short",
            len(tag_names), MAX_ACTIVITY_PAGES,
        )
        #
        return True

    @web.method()
    def read_tag_spend(self, tag_name, now):
        """Sum current-month spend for one tag from LiteLLM's daily aggregate.

        The report's `metadata` totals cover only the rows on the requested page, not the
        whole date range, so they are summed page by page from the per-day breakdown
        instead. Reading page one's metadata alone silently understated a busy tag: rows
        are one per (tag, date, model, key), so a single project passes the default page
        size well within a month, and the Usage page then showed a fraction of real spend.
        """
        period_start = now.replace(day=1)
        #
        result = {
            "tag": tag_name,
            "period": f"{now:%Y%m}",
            "spend": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "available": False,
        }
        #
        for page in range(1, MAX_ACTIVITY_PAGES + 1):
            try:
                activity = self.service_node.call.litellm_api_call(
                    "tag_daily_activity",
                    tags=[tag_name],
                    start_date=f"{period_start:%Y-%m-%d}",
                    end_date=f"{now:%Y-%m-%d}",
                    page=page,
                    page_size=MAX_ACTIVITY_PAGE_SIZE,
                )
            except:  # pylint: disable=W0702
                log.exception("Failed to read spend for tag %s at page %s", tag_name, page)
                #
                # Every counter resets, not just spend: a partly-read total understates
                # usage, which for a budget check is worse than reporting nothing at all
                return {
                    "tag": tag_name,
                    "period": f"{now:%Y%m}",
                    "spend": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "available": False,
                }
            #
            for day in (activity or {}).get("results") or []:
                entities = ((day or {}).get("breakdown") or {}).get("entities") or {}
                entry = entities.get(tag_name)
                #
                if entry is None:
                    continue
                #
                metrics = (entry or {}).get("metrics") or entry or {}
                #
                result["spend"] += float(metrics.get("spend", 0) or 0)
                result["prompt_tokens"] += int(metrics.get("prompt_tokens", 0) or 0)
                result["completion_tokens"] += int(metrics.get("completion_tokens", 0) or 0)
                result["total_tokens"] += int(metrics.get("total_tokens", 0) or 0)
            #
            result["available"] = True
            #
            if not ((activity or {}).get("metadata") or {}).get("has_more"):
                return result
        #
        log.warning(
            "Spend for tag %s hit the %s page ceiling; total may be short",
            tag_name, MAX_ACTIVITY_PAGES,
        )
        #
        return result
