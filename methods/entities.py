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

""" Method """

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def delete_all_entities(self):
        """ Method """
        #
        # Keys
        #
        keys = self.service_node.call.litellm_api_call(
            "key_list",
        )
        #
        for key in keys:
            self.service_node.call.litellm_api_call(
                "key_delete",
                key["token"],
            )
        #
        # Teams
        #
        teams = self.service_node.call.litellm_api_call(
            "team_list",
        )
        #
        for team in teams:
            self.service_node.call.litellm_api_call(
                "team_delete",
                team["team_id"],
            )
        #
        # Models
        #
        models = self.service_node.call.litellm_api_call(
            "model_info",
        )
        #
        for model in models:
            self.service_node.call.litellm_api_call(
                "model_delete",
                model["model_info"]["id"],
            )
        #
        # Credentials
        #
        credentials = self.service_node.call.litellm_api_call(
            "credential_list",
        )
        #
        for credential in credentials:
            self.service_node.call.litellm_api_call(
                "credential_delete",
                credential["credential_name"],
            )
