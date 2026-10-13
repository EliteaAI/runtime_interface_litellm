#!/usr/bin/python3
# coding=utf-8

#   Copyright 2026 EPAM Systems
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

""" RPC: request-independent Auto pool health for readiness (#6826) """

from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import context  # pylint: disable=E0401

from ..methods.routing import auto_preflight


class RPC:  # pylint: disable=E1101,R0903,W0201

    @web.rpc("runtime_interface_litellm_auto_preflight", "runtime_interface_litellm_auto_preflight")
    def runtime_interface_litellm_auto_preflight(self, project_id: int, user_id: int) -> dict:
        return auto_preflight(context.rpc_manager.timeout(10), project_id, user_id)
