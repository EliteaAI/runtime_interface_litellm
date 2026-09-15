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


""" Legacy LiteLLM budget-ceiling release """

from pylon.core.tools import log  # pylint: disable=E0611,E0401
from pylon.core.tools import web  # pylint: disable=E0611,E0401

BUDGET_TAG_PREFIX = "elitea_proj_"
UNLIMITED_BUDGET = 1_000_000_000.0


class Method:  # pylint: disable=E1101,R0903,W0201
    """ Method Resource """

    @web.method("release_budget_ceilings")
    def release_budget_ceilings(self, *args, **kwargs):  # pylint: disable=W0613
        """Admin task: lift every legacy Elitea budget ceiling set in LiteLLM.

        Enforcement moved to the usage plugin, so a ceiling still set here blocks calls that
        nothing in this plugin can unblock any more. One-time migration, run per deployment.
        Idempotent: tags already unlimited are skipped, and tags plus their accrued spend are
        left intact.
        """
        try:
            tags = self.service_node.call.litellm_api_call("tag_list") or []
        except Exception as exc:  # pylint: disable=W0703
            log.exception("Failed to list tags while releasing budget ceilings")
            return {"ok": False, "released": 0, "error": str(exc)}
        #
        released = 0
        failed = []
        #
        for tag in tags:
            tag_name = (tag or {}).get("name") or ""
            #
            if not tag_name.startswith(BUDGET_TAG_PREFIX):
                continue
            #
            budget = (tag or {}).get("litellm_budget_table") or {}
            max_budget = budget.get("max_budget")
            #
            if max_budget is None or max_budget >= UNLIMITED_BUDGET:
                continue
            #
            try:
                self.service_node.call.litellm_api_call(
                    "tag_update_if_exists",
                    tag_name,
                    max_budget=UNLIMITED_BUDGET,
                )
                released += 1
            except Exception:  # pylint: disable=W0703
                log.exception("Failed to release budget ceiling for %s", tag_name)
                failed.append(tag_name)
        #
        log.info("Released %s legacy LiteLLM budget ceiling(s)", released)
        #
        return {"ok": not failed, "released": released, "failed": failed}
