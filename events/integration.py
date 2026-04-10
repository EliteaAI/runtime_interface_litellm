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

""" Event """

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611


class Event:  # pylint: disable=E1101,R0903,W0201
    """
        Event Resource

        self is pointing to current Module instance

        Note: web.event decorator must be the last decorator (at top)
    """

    @web.event("integration_created")
    def on_integration_created(self, _context, _event, integration, *_args, **_kwargs):  # pylint: disable=R0914
        """ Event """
        log.info("Got integration_created: %s", integration)
        #
        if not self.is_llm_allowed_for_project(integration):
            log.info("Skipping: allow_project_own_llms is disabled for project %s",
                     integration.get("project_id"))
            return
        #
        self.make_integration_entities(integration)

    @web.event("integration_deleted")
    def on_integration_deleted(self, _context, _event, integration, *_args, **_kwargs):  # pylint: disable=R0914
        """ Event """
        log.info("Got integration_deleted: %s", integration)
        #
        self.delete_integration_entities(integration)

    @web.event("integration_updated")
    def on_integration_updated(self, _context, _event, integration, *_args, **_kwargs):
        """ Event """
        log.info("Got integration_updated: %s", integration)
        #
        if not self.is_llm_allowed_for_project(integration):
            log.info("Skipping: allow_project_own_llms is disabled for project %s",
                     integration.get("project_id"))
            self.delete_integration_entities(integration)
            return
        #
        self.delete_integration_entities(integration)
        self.make_integration_entities(integration)
