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
