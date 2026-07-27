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

""" Module """

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import module  # pylint: disable=E0611,E0401,W0611


class Module(module.ModuleModel):  # pylint: disable=R0903
    """ Pylon module """

    def init(self):
        """ Initialize module """
        self.descriptor.init_all(
            url_prefix=self.descriptor.config.get("url_prefix", None),
        )
        #
        self.descriptor.register_tool("runtime_interface", self)

    def reconfig(self):
        """Re-config"""
        # Budget limits are cached per tag on the request path, so drop them when an
        # admin changes the flag or the defaults — otherwise the change would only
        # apply after the cache TTL expires.
        self.invalidate_budget_tag_cache()
        #
        log.info(
            "Cost budgets reconfigured: enabled=%s defaults=%s",
            self.budgets_enabled(),
            self.descriptor.config.get("cost_budgets", {}).get("defaults", {}),
        )
