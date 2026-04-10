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

from tools import context, this, auth, constants as c  # pylint: disable=E0401


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def get_public_project_id(self, **kwargs):
        """ Method """
        if "public_project_id" in self.runtime_cache:
            return self.runtime_cache["public_project_id"]
        #
        from tools import elitea_config  # pylint: disable=C0415,E0401
        public_project_id = int(elitea_config.get("ai_project_id", 1))
        #
        self.runtime_cache["public_project_id"] = public_project_id
        #
        return public_project_id

    @web.method()
    def get_base_url(self, admin_secrets=None):
        """ Method """
        if "base_url" in self.runtime_cache:
            return self.runtime_cache["base_url"]
        #
        litellm_mode = self.descriptor.config.get("litellm_mode", "built-in")
        #
        if litellm_mode == "external":
            external_url = self.descriptor.config.get("external_litellm_url", "")
            if external_url:
                base_url = external_url.rstrip("/")
                if not base_url.endswith("/v1"):
                    base_url = f"{base_url}/v1"
            else:
                log.warning("litellm_mode is 'external' but external_litellm_url is empty, falling back to built-in")
                litellm_mode = "built-in"
        #
        if litellm_mode == "built-in":
            base_url = this.for_module("applications").descriptor.config.get(
                "base_url", c.APP_HOST
            )
            if base_url in ["http://localhost", "http://127.0.0.1"]:
                base_url = "http://pylon_main:8080"
            base_url = base_url.rstrip("/")
            base_url = f"{base_url}/llm/v1"
        #
        self.runtime_cache["base_url"] = base_url
        #
        return base_url

    @web.method()
    def get_system_user_token(self, project_id, name="api", create_if_not_exists=True):
        """ Method """
        system_user = context.rpc_manager.timeout(30).admin_get_project_system_user(project_id)
        #
        token_list = auth.list_tokens(system_user["id"])
        #
        for token in token_list:
            if token["name"] == name:
                return auth.encode_token(token["id"])
        #
        if create_if_not_exists:
            token_id = auth.add_token(system_user["id"], name)
            return auth.encode_token(token_id)
        #
        return None

    @web.method()
    def is_llm_allowed_for_project(self, entity, **kwargs):
        """ Check if LLM entity creation is allowed for the given project. """
        allow = self.descriptor.config.get("allow_project_own_llms", True)
        if allow:
            return True
        # Admin-level always allowed
        if entity.get("mode") == "administration" or entity.get("project_id") is None:
            return True
        # Public project always allowed
        public_project_id = self.get_public_project_id()
        project_id = entity.get("project_id")
        if project_id is not None and int(project_id) == public_project_id:
            return True
        return False

    @web.method()
    def get_llm_settings(self, **kwargs):
        """ Get LLM-related settings for UI consumption. """
        return {
            "allow_project_own_llms": self.descriptor.config.get("allow_project_own_llms", True),
        }
