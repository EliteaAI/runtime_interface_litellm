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

    @web.event("project_created")
    def on_project_created(self, _context, _event, project, *_args, **_kwargs):
        """ Event """
        log.info("Creating LLM key for project: %s", project)
        #
        project_id = project["id"]
        #
        self.make_project_entities(project_id)
        #
        log.info("Created LLM key for project: %s", project)

    @web.event("project_deleted")
    def on_project_deleted(self, _context, _event, project, *_args, **_kwargs):  # pylint: disable=R0914
        """ Event """
        log.info("Deleting LLM key for project: %s", project)
        #
        project_id = project["id"]
        #
        self.delete_project_entities(project_id)
        #
        log.info("Deleted LLM key for project: %s", project)
