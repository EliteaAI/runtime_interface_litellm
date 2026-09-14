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

import threading
import time

from pylon.core.tools import log  # pylint: disable=E0611,E0401
from pylon.core.tools import web  # pylint: disable=E0611,E0401

BUDGET_TAG_PREFIX = "elitea_proj_"
UNLIMITED_BUDGET = 1_000_000_000.0
RELEASE_DELAY_SECONDS = 30


class Method:  # pylint: disable=E1101,R0903,W0201
    """ Method Resource """

    @web.method()
    def schedule_budget_ceiling_release(self):
        """Enforcement moved to the usage plugin, so any ceiling still set here blocks calls
        that nothing in this plugin can unblock any more. Lift them once per start."""
        thread = threading.Thread(
            target=self._release_budget_ceilings_later,
            name="litellm-ceiling-release",
            daemon=True,
        )
        thread.start()

    @web.method()
    def _release_budget_ceilings_later(self):
        """Delayed so the LiteLLM service node has a chance to come up first."""
        time.sleep(RELEASE_DELAY_SECONDS)
        #
        try:
            self.release_budget_ceilings()
        except:  # pylint: disable=W0702
            log.exception("Failed to release legacy budget ceilings")

    @web.method()
    def release_budget_ceilings(self):
        """Lift every legacy Elitea budget ceiling set in LiteLLM. Idempotent: tags already
        unlimited are skipped, and tags plus their accrued spend are left intact."""
        try:
            tags = self.service_node.call.litellm_api_call("tag_list") or []
        except:  # pylint: disable=W0702
            log.exception("Failed to list tags while releasing budget ceilings")
            return 0
        #
        released = 0
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
            except:  # pylint: disable=W0702
                log.exception("Failed to release budget ceiling for %s", tag_name)
        #
        if released:
            log.info("Released %s legacy LiteLLM budget ceiling(s)", released)
        #
        return released
