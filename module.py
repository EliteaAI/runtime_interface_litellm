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

from tools import this  # pylint: disable=E0401


class Module(module.ModuleModel):  # pylint: disable=R0903
    """ Pylon module """

    def init(self):
        """ Initialize module """
        self.descriptor.init_all(
            url_prefix=self.descriptor.config.get("url_prefix", None),
        )
        #
        self.descriptor.register_tool("runtime_interface", self)

    def ready(self):
        """ Ready callback """
        self._register_admin_tasks()

    def _register_admin_tasks(self):
        try:
            this.for_module("admin").module.register_admin_task(
                "release_budget_ceilings", self.release_budget_ceilings, group="R-2.0.7",
            )
        except Exception as exc:  # pylint: disable=W0703
            log.exception("Failed to register admin tasks: %s", exc)
