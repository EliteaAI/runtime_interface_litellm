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

from ..methods.budgets import make_budget_tag, make_user_budget_tag


# Daily activity is paginated by day; a month never exceeds this
MAX_ACTIVITY_PAGE_SIZE = 100


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

        Batched so listing every project does not fan out into one call per row.
        """
        return {pid: self.get_project_budget_limit(pid) for pid in project_ids}

    @web.rpc("litellm_get_effective_user_limits", "litellm_get_effective_user_limits")
    def litellm_get_effective_user_limits(self, project_id, user_ids, **kwargs):
        """Effective per-user limits within a project, keyed by user id."""
        return {uid: self.get_user_budget_limit(project_id, uid) for uid in user_ids}

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
            "api_requests": 0,
            "available": False,
        }
        #
        try:
            activity = self.service_node.call.litellm_api_call(
                "tag_daily_activity",
                tags=[tag_name],
                start_date=f"{period_start:%Y-%m-%d}",
                end_date=f"{now:%Y-%m-%d}",
                page_size=MAX_ACTIVITY_PAGE_SIZE,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to read usage detail for tag %s", tag_name)
            return result
        #
        models = {}
        daily = []
        #
        for day in (activity or {}).get("results") or []:
            day_metrics = (day or {}).get("metrics") or {}
            #
            daily.append({
                "date": (day or {}).get("date"),
                "spend": float(day_metrics.get("spend", 0) or 0),
                "total_tokens": int(day_metrics.get("total_tokens", 0) or 0),
                "api_requests": int(day_metrics.get("successful_requests", 0) or 0),
            })
            #
            breakdown = ((day or {}).get("breakdown") or {}).get("models") or {}
            #
            for model_name, entry in breakdown.items():
                metrics = (entry or {}).get("metrics") or {}
                bucket = models.setdefault(
                    model_name,
                    {"model": model_name, "spend": 0.0, "total_tokens": 0, "api_requests": 0},
                )
                #
                bucket["spend"] += float(metrics.get("spend", 0) or 0)
                bucket["total_tokens"] += int(metrics.get("total_tokens", 0) or 0)
                # Rejected calls served nothing and cost nothing, so they are not usage.
                # They also log under the pre-routing model name, which would otherwise
                # split one model across two rows (e.g. "1_gpt-5" beside "gpt-5").
                bucket["api_requests"] += int(metrics.get("successful_requests", 0) or 0)
        #
        daily.sort(key=lambda item: item["date"] or "")
        #
        # Models with no spend and no calls only add noise to the UI
        model_rows = [row for row in models.values() if row["api_requests"] or row["spend"]]
        model_rows.sort(key=lambda row: row["spend"], reverse=True)
        #
        result["models"] = model_rows
        result["daily"] = daily
        result["spend"] = sum(row["spend"] for row in daily)
        result["total_tokens"] = sum(row["total_tokens"] for row in daily)
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

    @web.method()
    def read_tags_spend(self, tag_names, now):
        """Map tag -> current-month spend using a single multi-tag activity call.

        Per-tag figures come from each day's ``breakdown.entities``; the top-level
        metadata only carries a combined total across all requested tags.
        """
        result = {tag: 0.0 for tag in tag_names}
        #
        if not tag_names:
            return result
        #
        period_start = now.replace(day=1)
        #
        try:
            activity = self.service_node.call.litellm_api_call(
                "tag_daily_activity",
                tags=tag_names,
                start_date=f"{period_start:%Y-%m-%d}",
                end_date=f"{now:%Y-%m-%d}",
                page_size=MAX_ACTIVITY_PAGE_SIZE,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to read spend for %s tags", len(tag_names))
            return result
        #
        for day in (activity or {}).get("results") or []:
            entities = ((day or {}).get("breakdown") or {}).get("entities") or {}
            #
            for tag, entry in entities.items():
                if tag not in result:
                    continue
                #
                metrics = (entry or {}).get("metrics") or entry or {}
                result[tag] += float(metrics.get("spend", 0) or 0)
        #
        return result

    @web.method()
    def read_tag_spend(self, tag_name, now):
        """Sum current-month spend for one tag from LiteLLM's daily aggregate."""
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
        try:
            activity = self.service_node.call.litellm_api_call(
                "tag_daily_activity",
                tags=[tag_name],
                start_date=f"{period_start:%Y-%m-%d}",
                end_date=f"{now:%Y-%m-%d}",
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to read spend for tag %s", tag_name)
            return result
        #
        metadata = (activity or {}).get("metadata") or {}
        #
        result["spend"] = float(metadata.get("total_spend", 0) or 0)
        result["prompt_tokens"] = int(metadata.get("total_prompt_tokens", 0) or 0)
        result["completion_tokens"] = int(metadata.get("total_completion_tokens", 0) or 0)
        result["total_tokens"] = int(metadata.get("total_tokens", 0) or 0)
        result["available"] = True
        #
        return result
