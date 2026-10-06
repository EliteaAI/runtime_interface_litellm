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

from ..utils.utils import GATEWAY_CONFIGURATION_SECTIONS


class Event:  # pylint: disable=E1101,R0903,W0201
    """
        Event Resource

        self is pointing to current Module instance

        Note: web.event decorator must be the last decorator (at top)
    """

    @web.event("configuration_created")
    def on_configuration_created(self, _context, _event, configuration, *_args, **_kwargs):  # pylint: disable=R0914
        """ Event """
        with self.configuration_entity_locks[f'{configuration["project_id"]}:{configuration["id"]}']:
            log.info("Got configuration_created: %s", configuration)
            #
            if not self.is_llm_allowed_for_project(configuration):
                log.info("Skipping: allow_project_own_llms is disabled for project %s",
                         configuration.get("project_id"))
                return
            #
            self.make_configuration_entities(configuration)

    @web.event("configuration_deleted")
    def on_configuration_deleted(self, _context, _event, configuration, *_args, **_kwargs):  # pylint: disable=R0914
        """ Event """
        with self.configuration_entity_locks[f'{configuration["project_id"]}:{configuration["id"]}']:
            log.info("Got configuration_deleted: %s", configuration)
            #
            self.delete_configuration_entities(configuration)

    @web.event("configuration_status_changed")
    def on_configuration_status_changed(self, _context, _event, configuration, *_args, **_kwargs):
        """ Event """
        lock_key = f'{configuration["project_id"]}:{configuration["id"]}'
        #
        with self.configurations_lock:
            if self.configurations_blocklist[lock_key] > 0:
                self.configurations_blocklist[lock_key] -= 1
                if not self.configurations_blocklist[lock_key]:
                    del self.configurations_blocklist[lock_key]
                return
        #
        with self.configuration_entity_locks[lock_key]:
            log.info("Got configuration_status_changed: %s", configuration)
            #
            if not self.is_llm_allowed_for_project(configuration):
                log.info("Skipping: allow_project_own_llms is disabled for project %s",
                         configuration.get("project_id"))
                self.delete_configuration_entities(configuration)
                return
            #
            self.delete_configuration_entities(configuration)
            self.make_configuration_entities(configuration)

    @web.event("configuration_updated")
    def on_configuration_updated(self, _context, _event, configuration, *_args, **_kwargs):
        """ Event """
        if configuration["section"] not in GATEWAY_CONFIGURATION_SECTIONS:
            return
        #
        with self.configuration_entity_locks[f'{configuration["project_id"]}:{configuration["id"]}']:
            log.info("Got configuration_updated: %s", configuration)
            #
            saved_settings_applied = self.reapply_configuration_entities(
                configuration, configuration["previous_data"],
            )
        #
        if saved_settings_applied and configuration["section"] == "ai_credentials" and \
                configuration["previous_data"].get("api_base") != configuration["data"].get("api_base"):
            self.reapply_credential_models(configuration)
