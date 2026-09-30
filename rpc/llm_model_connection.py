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

""" RPC: send one test request to an LLM model built from unsaved form values """

from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from ..utils.llm_model_connection import credential_secrets, run_connection_test

CONNECTION_TEST_CONFIGURATION_UUID = "llm-model-connection-test"


class RPC:  # pylint: disable=E1101,R0903,W0201

    @web.rpc("litellm_test_llm_model_connection", "litellm_test_llm_model_connection")
    def litellm_test_llm_model_connection(self, settings: dict) -> dict:
        credential = settings.get("ai_credentials") or {}
        mapped_model = self.configuration_to_model({
            "project_id": credential.get("configuration_project_id"),
            "uuid": CONNECTION_TEST_CONFIGURATION_UUID,
            "data": settings,
        })
        return run_connection_test(mapped_model, self.service_node.call, credential_secrets(credential))
