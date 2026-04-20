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

import flask  # pylint: disable=E0401

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from werkzeug.datastructures.headers import Headers  # pylint: disable=E0401

from tools import context, project_constants, VaultClient, this  # pylint: disable=E0401


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def preprocess_headers(self, raw_headers):
        """ Method """
        exclude_headers = {
            "Connection",
            "Keep-Alive",
            "Proxy-Authenticate",
            "Proxy-Authorization",
            "TE",
            "Trailers",
            "Transfer-Encoding",
            "Upgrade",
        }
        #
        headers = Headers(dict(raw_headers))
        #
        for header in exclude_headers:
            headers.remove(header)
        #
        return headers

    @web.method()
    def preprocess_data(self, raw_request):
        """ Method """
        data = None
        json = None
        files = None
        #
        if raw_request.method in ["POST", "PUT", "PATCH"]:
            if raw_request.files:
                files = {
                    key: (file.filename, file.stream.read(), file.content_type)  # TODO: obj proxies
                    for key, file in raw_request.files.items()
                }
                data = raw_request.form
            elif raw_request.content_type == "application/json":
                json = raw_request.get_json(silent=True)
                if json is None:
                    data = raw_request.data
            elif raw_request.content_type == "application/x-www-form-urlencoded":
                data = raw_request.form
            else:
                data = raw_request.data
        #
        return {
            "data": data,
            "json": json,
            "files": files,
        }

    @web.method()
    def check_access(self, proxy_target, proxy_auth):
        """ Method """
        #
        # Whitelist
        #
        endpoint_whitelist = [
            "/v1/models",
            "/v1/completions",
            "/v1/chat/completions",
            "/v1/responses",
            "/v1/messages",
            "/v1/embeddings",
            "/v1/images/generations",
            "/v1/images/edits",
            "/v1/images/variations",
        ]
        #
        endpoint_prefix_whitelist = [
            "/v1/models/",
            "/v1/chat/completions/",
            "/v1/responses/",
            "/v1/messages/",
        ]
        #
        target_endpoint = proxy_target["endpoint"]
        target_whitelisted = target_endpoint in endpoint_whitelist
        #
        if not target_whitelisted:
            for target_prefix in endpoint_prefix_whitelist:
                if target_endpoint.startswith(target_prefix):
                    target_whitelisted = True
                    break
        #
        if target_whitelisted:
            return None
        #
        # Check if user is admin
        #
        user_id = proxy_auth["user"]["id"]
        #
        user_administration_roles = context.rpc_manager.timeout(30).auth_get_user_roles(
            user_id, "administration",
        )
        #
        user_is_administration_admin = "admin" in user_administration_roles or "super_admin" in user_administration_roles
        #
        # Admin can access all targets
        #
        if user_is_administration_admin:
            return None
        #
        return "Forbidden", 403

    @web.method()
    def _map_model_name(self, raw_model_name, project_id, public_project_id):
        """
        Map raw model name to project-prefixed model name.
        
        Returns:
            Mapped model name (with project prefix if model exists)
        """
        model_name = f"{project_id}_{raw_model_name}"
        model_info = self.service_node.call.litellm_api_call(
            "model_group_info",
            model_name,
        )
        #
        if not model_info and public_project_id != project_id:
            model_name = f"{public_project_id}_{raw_model_name}"
            model_info = self.service_node.call.litellm_api_call(
                "model_group_info",
                model_name,
            )
        #
        if not model_info:
            model_name = raw_model_name
        #
        return model_name

    @web.method()
    def prepare_request(self, proxy_target, proxy_auth):  # pylint: disable=R0911,R0912,R0914
        """ Method """
        proxy_target_endpoint = proxy_target["endpoint"]
        cfg_url_prefix = self.descriptor.config.get("url_prefix", None)
        #
        if cfg_url_prefix is not None and proxy_target_endpoint.startswith(cfg_url_prefix):
            return flask.redirect(proxy_target_endpoint)
        #
        proxy_target["headers"] = self.preprocess_headers(proxy_target["headers"])
        proxy_target["headers"]["Accept-Encoding"] = "identity"
        #
        if proxy_auth["type"] == "token":
            user_name = proxy_auth["user"]["name"]
            #
            try:
                if user_name.startswith(project_constants["PROJECT_USER_NAME_PREFIX"]):
                    project_id = int(user_name.split(":")[-2])
                else:
                    project_id = context.rpc_manager.timeout(30).projects_get_personal_project_id(
                        proxy_auth["user"]["id"]
                    )
            except:  # pylint: disable=W0702
                log.exception("Failed to get project_id")
                project_id = None
            #
            if project_id is None:
                return "Error", 400
            #
            public_project_id = self.get_public_project_id()
            #
            if proxy_target_endpoint.startswith("/v1/models"):
                result = {
                    "data": [],
                    "object": "list",
                }
                #
                endpoint_parts = proxy_target_endpoint.strip("/").split("/", 2)
                target_model_name = None
                #
                if len(endpoint_parts) > 2:
                    target_model_name = endpoint_parts[-1]
                #
                models = self.service_node.call.litellm_api_call(
                    "model_info",
                )
                #
                import re as _re  # pylint: disable=C0415
                #
                for model in models:
                    model_name = model["model_name"]
                    #
                    if model_name.startswith(f"{project_id}_") or \
                            model_name.startswith(f"{public_project_id}_"):
                        model_obj_name = model_name.split("_", 1)[1]
                    elif not _re.match(r'^\d+_', model_name):
                        model_obj_name = model_name
                    else:
                        continue
                    #
                    model_obj = {
                        "id": model_obj_name,
                        "object": "model",
                        "created": 1677610602,
                        "owned_by": "openai",
                    }
                    #
                    if target_model_name is not None and model_obj_name == target_model_name:
                        return model_obj
                    #
                    result["data"].append(model_obj)
                #
                if target_model_name is not None:
                    return "Error", 404
                #
                return result
            #
            vault_client = VaultClient(project_id)
            project_secrets = vault_client.get_secrets()
            #
            if "project_llm_key" not in project_secrets:
                return "Error", 400
            #
            llm_key = project_secrets["project_llm_key"]
            #
            proxy_target["headers"]["Authorization"] = f"Bearer {llm_key}"
            #
            if "X-Api-Key" in proxy_target["headers"]:
                proxy_target["headers"]["X-Api-Key"] = f"{llm_key}"
            #
            additional_litellm_params = this.descriptor.config.get("additional_litellm_params", {})
            #
            if "additional_drop_params" in additional_litellm_params and \
                    isinstance(proxy_target["json"], dict):
                for drop_param in additional_litellm_params["additional_drop_params"]:
                    if drop_param in proxy_target["json"]:
                        log.debug("Dropping param: %s", drop_param)
                        proxy_target["json"].pop(drop_param, None)
            #
            if isinstance(proxy_target["json"], dict) and "model" in proxy_target["json"]:
                raw_model_name = proxy_target["json"]["model"]
                model_name = self._map_model_name(raw_model_name, project_id, public_project_id)
                #
                if model_name != raw_model_name:
                    log.debug("Mapped model name (JSON): %s -> %s", raw_model_name, model_name)
                    proxy_target["json"]["model"] = model_name
            #
            # Also handle model mapping for form data (multipart requests like image edits)
            #
            if proxy_target.get("data") and "model" in (proxy_target["data"] if isinstance(proxy_target["data"], dict) else {}):
                raw_model_name = proxy_target["data"]["model"]
                model_name = self._map_model_name(raw_model_name, project_id, public_project_id)
                #
                if model_name != raw_model_name:
                    log.debug("Mapped model name (form data): %s -> %s", raw_model_name, model_name)
                    #
                    if hasattr(proxy_target["data"], "to_dict"):
                        proxy_target["data"] = dict(proxy_target["data"])
                    proxy_target["data"]["model"] = model_name
        #
        return None

    @web.method()
    def prepare_response(self, proxy_target, proxy_auth, response):
        """ Method """
        response["headers"] = self.preprocess_headers(response["headers"])
        #
        if "Host" in proxy_target["headers"]:
            response["headers"]["Host"] = proxy_target["headers"]["Host"]
        else:
            response["headers"].remove("Host")
        #
        if response["headers"].get("Transfer-Encoding", "").lower() == "chunked":
            response["headers"].remove("Content-Length")
        #
        response["headers"]["Server"] = "Centry"
        #
        if proxy_auth["type"] == "token":
            for header_key in list(dict(response["headers"])):
                if header_key.lower().startswith("x-litellm-") or \
                        header_key.lower().startswith("llm_provider-"):
                    response["headers"].remove(header_key)
