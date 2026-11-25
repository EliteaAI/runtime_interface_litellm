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

""" RPC: the gateway's LiteLLM catalog of chat models, in the LLM model form's vocabulary """

from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from ..utils.llm_model_catalog import llm_model_catalog


class RPC:  # pylint: disable=E1101,R0903,W0201

    @web.rpc("litellm_llm_model_catalog", "litellm_llm_model_catalog")
    def litellm_llm_model_catalog(self) -> dict:
        return llm_model_catalog(self.service_node.call)
