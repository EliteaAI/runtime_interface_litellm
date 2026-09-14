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

import uuid

import flask  # pylint: disable=E0401

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from werkzeug.datastructures.headers import Headers  # pylint: disable=E0401

from tools import context, project_constants, VaultClient, this  # pylint: disable=E0401

from ..utils.metering import prepare_llm_call


LLM_ENDPOINT_WHITELIST = [
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

LLM_ENDPOINT_PREFIX_WHITELIST = [
    "/v1/models/",
    "/v1/chat/completions/",
    "/v1/responses/",
    "/v1/messages/",
]

# Twin contract: the indexer worker lifts elitea_core's platform run id onto the LLM
# request under this name (#6569). Duplicated as a literal because this plugin cannot
# import elitea_core. Consumed at this gateway — never forwarded to the model provider.
ELITEA_RUN_ID_HEADER = "X-Elitea-Run-Id"

# Where the extracted id is parked on proxy_auth — the dict this plugin already uses to carry
# project_id from prepare_request to prepare_response. The usage plugin reads the id from there
# when the response is metered, by which time the header itself is gone from the request.
PLATFORM_RUN_ID_AUTH_KEY = "platform_run_id"


def extract_run_id(headers):
    """Canonical platform run id from the request headers, or None.

    Normalised: the usage plugin stores it in a Postgres ``uuid`` column, which canonicalises
    on write, so an unhyphenated form here would not compare equal to what a report reads back.
    Validated: the value arrives on a caller-supplied header, so an unparseable one is dropped
    here rather than left to fail a usage insert later.
    """
    raw = headers.get(ELITEA_RUN_ID_HEADER)
    #
    if not raw:
        return None
    #
    try:
        return str(uuid.UUID(raw))
    except (AttributeError, TypeError, ValueError):
        log.warning("Ignoring malformed %s header", ELITEA_RUN_ID_HEADER)
        return None


def model_of(body):
    """The model a request body names, whether it arrived as JSON or as form fields.

    Form bodies are werkzeug multi-dicts, not dicts, so an isinstance check reads them as
    modelless and silently drops multipart calls (image edits) out of mapping and metering.
    """
    if not hasattr(body, "get"):
        return None
    #
    name = body.get("model")
    #
    return name if isinstance(name, str) else None


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
    def _is_llm_endpoint(self, target_endpoint):
        """ Method """
        if target_endpoint in LLM_ENDPOINT_WHITELIST:
            return True
        #
        for target_prefix in LLM_ENDPOINT_PREFIX_WHITELIST:
            if target_endpoint.startswith(target_prefix):
                return True
        #
        return False

    @web.method()
    def check_access(self, proxy_target, proxy_auth):
        """ Method """
        if self._is_llm_endpoint(proxy_target["endpoint"]):
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

        FRAGILE — CHANGE WITH EXTRA CAUTION.
        This three-step fallback is the result of production incidents and deliberate
        rollback from a DB-based resolution approach (litellm_resolve_model RPC).
        The invariants that must hold:
          1. Always checks caller's own project first ({project_id}_{model}).
          2. Falls back to the public project ({public_project_id}_{model}).
          3. Falls back to the raw name for externally-managed models.
          4. ALWAYS uses the caller's own project_llm_key — never a third project's key.
        Violating invariant 4 risks circular routing loops when a credential's
        api_base points back to this proxy (e.g. an OpenAI-compatible credential
        routing through dev.elitea.ai/llm/v1).

        Returns:
            (mapped_model_name, is_shared) — is_shared is True only when the model
            resolved via the public project, i.e. the caller consumes a shared model.
        """
        model_name = f"{project_id}_{raw_model_name}"
        model_info = self.service_node.call.litellm_api_call(
            "model_group_info",
            model_name,
        )
        #
        if model_info:
            return model_name, False
        #
        if public_project_id != project_id:
            model_name = f"{public_project_id}_{raw_model_name}"
            model_info = self.service_node.call.litellm_api_call(
                "model_group_info",
                model_name,
            )
            #
            if model_info:
                return model_name, True
        #
        return raw_model_name, False

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
        # Platform run id: correlates this LLM call with the predict/eval run that caused it.
        # Removed from the outbound set unconditionally — it is an internal identifier that the
        # model provider has no use for, and `remove` is a no-op when the header is absent.
        # Read into proxy_auth first: everything downstream of this point (the log below, the
        # usage hooks once their bodies land) must take the id from there, not from the headers.
        #
        platform_run_id = extract_run_id(proxy_target["headers"])
        proxy_target["headers"].remove(ELITEA_RUN_ID_HEADER)
        proxy_auth[PLATFORM_RUN_ID_AUTH_KEY] = platform_run_id
        #
        # "user" = runtime authenticated with the caller's session cookie (#6486)
        # Skip LiteLLM's native admin endpoints — they need their original credential, not a project virtual key.
        if proxy_auth["type"] in ("token", "user") and self._is_llm_endpoint(proxy_target_endpoint):
            user_name = proxy_auth["user"]["name"]
            user_id = proxy_auth["user"]["id"]
            #
            project_id = None
            #
            # Prefer explicit project_id from request headers so a request that
            # originates inside a team project is billed to that team rather
            # than to the caller's personal project. Accept X-Project-Id first
            # (semantic name), fall back to OpenAI-Organization for SDK builds
            # that don't yet send X-Project-Id. Both are gated by a membership
            # check so a token holder can never spend on a project they don't
            # belong to.
            #
            header_project_id = proxy_target["headers"].get("X-Project-Id") \
                or proxy_target["headers"].get("OpenAI-Organization")
            #
            if header_project_id is not None:
                try:
                    candidate_project_id = int(header_project_id)
                except (TypeError, ValueError):
                    candidate_project_id = None
                #
                if candidate_project_id is not None:
                    try:
                        user_in_project = context.rpc_manager.timeout(
                            30
                        ).admin_check_user_in_project(
                            candidate_project_id, user_id,
                        )
                    except:  # pylint: disable=W0702
                        log.exception("Failed to check project membership")
                        user_in_project = False
                    #
                    if user_in_project:
                        project_id = candidate_project_id
            #
            if project_id is None:
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
            proxy_auth["project_id"] = project_id
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
            # Verification point for run correlation (#6569): logged here, at the single relay
            # every project LLM call passes through, so a run's LLM usage is traceable from the
            # gateway alone. A missing id is a warning, not a debug detail — it means this
            # call's usage cannot be attributed to any run.
            #
            request_json = proxy_target.get("json")
            request_data = proxy_target.get("data")
            raw_model = None
            #
            raw_model = model_of(request_json) or model_of(request_data)
            #
            if platform_run_id:
                log.info(
                    "LLM call: run_id=%s project_id=%s endpoint=%s model=%s",
                    platform_run_id, project_id, proxy_target_endpoint, raw_model,
                )
            else:
                log.warning(
                    "LLM call without platform run id: project_id=%s endpoint=%s model=%s",
                    project_id, proxy_target_endpoint, raw_model,
                )
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
            # Same idea as additional_drop_params above, but scoped to specific model
            # name prefixes instead of every request. Matched against the raw client-sent
            # model name, since _map_model_name() below hasn't run yet.
            model_drop_params = additional_litellm_params.get("model_drop_params", {})
            #
            if isinstance(proxy_target["json"], dict) and "model" in proxy_target["json"]:
                request_model_name = proxy_target["json"]["model"]
                for model_prefix, drop_params in model_drop_params.items():
                    if not request_model_name.startswith(model_prefix):
                        continue
                    for drop_param in drop_params:
                        if drop_param in proxy_target["json"]:
                            log.debug("Dropping param for model %s: %s", request_model_name, drop_param)
                            proxy_target["json"].pop(drop_param, None)
            #
            # The two facts metering cannot work out for itself: the name the caller asked
            # for, and the project the model resolved in. Collected here, handed over below.
            metered_model_name = None
            metered_project_id = None
            #
            if isinstance(proxy_target["json"], dict) and "model" in proxy_target["json"]:
                raw_model_name = proxy_target["json"]["model"]
                model_name, is_shared = self._map_model_name(
                    raw_model_name, project_id, public_project_id,
                )
                #
                if model_name != raw_model_name:
                    log.debug("Mapped model name (JSON): %s -> %s", raw_model_name, model_name)
                    proxy_target["json"]["model"] = model_name
                #
                if is_shared:
                    self.apply_budget_tag(
                        proxy_target, project_id,
                        form_data=False, endpoint=proxy_target_endpoint,
                        user_id=user_id,
                    )
                #
                metered_model_name = raw_model_name
                metered_project_id = public_project_id if is_shared else project_id
            #
            # Also handle model mapping for form data (multipart requests like image edits)
            #
            raw_model_name = model_of(proxy_target.get("data"))
            #
            if raw_model_name:
                model_name, is_shared = self._map_model_name(
                    raw_model_name, project_id, public_project_id,
                )
                #
                if model_name != raw_model_name:
                    log.debug("Mapped model name (form data): %s -> %s", raw_model_name, model_name)
                    #
                    # to_dict, not dict(): a multi-dict copies as lists of values
                    if hasattr(proxy_target["data"], "to_dict"):
                        proxy_target["data"] = proxy_target["data"].to_dict()
                    proxy_target["data"]["model"] = model_name
                #
                if is_shared:
                    self.apply_budget_tag(
                        proxy_target, project_id,
                        form_data=True, endpoint=proxy_target_endpoint,
                        user_id=user_id,
                    )
                #
                metered_model_name = raw_model_name
                metered_project_id = public_project_id if is_shared else project_id
            #
            denial = prepare_llm_call(
                proxy_target, proxy_auth, metered_model_name, metered_project_id,
            )
            #
            # Returned before any stream is opened, so a refusal leaks nothing
            if denial is not None:
                return denial
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
        # "user" = runtime authenticated with the caller's session cookie (#6486)
        if proxy_auth["type"] in ("token", "user"):
            for header_key in list(dict(response["headers"])):
                if header_key.lower().startswith("x-litellm-") or \
                        header_key.lower().startswith("llm_provider-"):
                    response["headers"].remove(header_key)
