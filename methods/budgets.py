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

import json
import time
import datetime

import flask  # pylint: disable=E0401

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import context  # pylint: disable=E0401


BUDGET_TAG_PREFIX = "elitea_proj_"

BUDGET_ERROR_MESSAGE = (
    "The monthly budget for shared models has been reached. "
    "Please contact your platform administrator to raise the limit."
)

# Cap on buffering an error body before rewriting it; error payloads are tiny
MAX_ERROR_BODY_BYTES = 64 * 1024

# How long a limit is trusted before re-reading it on the request path
BUDGET_SYNC_TTL = 60.0

# How long the personal-project id list is cached (it changes only on project creation)
PERSONAL_PROJECTS_TTL = 300.0

# LiteLLM ignores max_budget=null on /tag/update, so "unlimited" is expressed as a
# ceiling nothing can reach. Keeps the tag (and its spend history) while not blocking.
UNLIMITED_BUDGET = 1_000_000_000.0


def make_budget_tag(project_id, now=None):
    """Build the per-project monthly budget tag name."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    #
    return f"{BUDGET_TAG_PREFIX}{project_id}_{now:%Y%m}"


def make_user_budget_tag(project_id, user_id, now=None):
    """Build the per-user-within-project monthly budget tag name."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    #
    return f"{BUDGET_TAG_PREFIX}{project_id}_user_{user_id}_{now:%Y%m}"


def is_anthropic_endpoint(endpoint):
    """True for Anthropic-native endpoints, which read tags from litellm_metadata."""
    return bool(endpoint) and endpoint.startswith("/v1/messages")


def is_budget_exceeded_body(body):
    """Detect LiteLLM's budget-exceeded error in a response body."""
    try:
        text = body.decode("utf-8", errors="ignore")
    except AttributeError:
        text = str(body)
    #
    lowered = text.lower()
    #
    return "budget" in lowered and (
        "budget_exceeded" in lowered or "budget has been exceeded" in lowered
    )


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def budgets_enabled(self):
        """ Check the cost-budgets feature flag. """
        return bool(self.descriptor.config.get("cost_budgets", {}).get("enabled", False))

    @web.method()
    def apply_budget_tag(  # pylint: disable=R0913
            self, proxy_target, project_id, form_data=False, endpoint=None, user_id=None,
    ):
        """Attach the monthly budget tags to an outgoing shared-model call.

        Attaches both a project tag and (when the user is known) a per-user tag.
        LiteLLM checks every tag independently and blocks if any one is over, so a
        user sub-limit stops one member draining the whole project budget.

        Tagging is what makes LiteLLM's per-tag budget apply, so a failure here must
        never break the call itself — worst case the call goes untagged.

        Note: LiteLLM skips budget checks for the master key, so enforcement only
        applies because proxied calls authenticate with a per-project key.
        """
        if not self.budgets_enabled():
            return
        #
        try:
            tag_names = [make_budget_tag(project_id)]
            self.ensure_budget_tag(project_id, tag_names[0], self.get_project_budget_limit)
            #
            if user_id is not None:
                user_tag = make_user_budget_tag(project_id, user_id)
                tag_names.append(user_tag)
                self.ensure_budget_tag(
                    project_id, user_tag,
                    lambda pid: self.get_user_budget_limit(pid, user_id),
                )
            #
            if form_data:
                # Multipart: LiteLLM only unpacks a metadata form field when it is a JSON string.
                proxy_target["data"]["metadata"] = json.dumps({"tags": tag_names})
                return
            #
            # Anthropic's own API owns the `metadata` field, so LiteLLM reads its own
            # tags from `litellm_metadata` on /v1/messages — `metadata` is dropped there.
            metadata_key = "litellm_metadata" if is_anthropic_endpoint(endpoint) else "metadata"
            #
            metadata = proxy_target["json"].setdefault(metadata_key, {})
            tags = [tag for tag in metadata.get("tags", []) if not tag.startswith(BUDGET_TAG_PREFIX)]
            tags.extend(tag_names)
            metadata["tags"] = tags
        except:  # pylint: disable=W0702
            log.exception("Failed to apply budget tags for project %s", project_id)

    @web.method()
    def ensure_budget_tag(self, project_id, tag_name, limit_getter):
        """Create/refresh a LiteLLM tag budget.

        Provisioning lazily on first shared call means enforcement self-heals if the
        tag is ever deleted on the LiteLLM side, without needing a scheduled job.
        Re-checked only every BUDGET_SYNC_TTL seconds so the hot path stays cheap;
        an explicit budget change pushes immediately via the RPC instead of waiting.
        """
        cache = self.runtime_cache.setdefault("budget_tags", {})
        #
        now = time.monotonic()
        checked_at, _ = cache.get(tag_name, (0.0, None))
        #
        if now - checked_at < BUDGET_SYNC_TTL:
            return
        #
        limit = limit_getter(project_id)
        #
        # None means unlimited: lift any previously-set ceiling instead of leaving the
        # old value enforcing, but don't create a tag that never had a budget.
        max_budget = UNLIMITED_BUDGET if limit is None else limit
        #
        try:
            if limit is None:
                self.service_node.call.litellm_api_call(
                    "tag_update_if_exists",
                    tag_name,
                    max_budget=max_budget,
                )
            else:
                self.service_node.call.litellm_api_call(
                    "tag_upsert",
                    tag_name,
                    max_budget=max_budget,
                )
        except:  # pylint: disable=W0702
            log.exception("Failed to sync budget tag %s", tag_name)
            return
        #
        cache[tag_name] = (now, limit)

    @web.method()
    def get_project_budget_limit(self, project_id):
        """Effective monthly limit (USD) for a project, or None if unlimited.

        Falls back to a configured default so a project nobody has explicitly
        budgeted is not silently unlimited. An explicit row with enabled=false
        means "deliberately exempt" and is honoured as unlimited.
        """
        try:
            budget = context.rpc_manager.timeout(5).elitea_core_get_project_budget(
                project_id=project_id,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to get budget for project %s", project_id)
            return None
        #
        if budget is not None:
            if not budget.get("enabled", True):
                return None
            #
            if budget.get("monthly_limit") is not None:
                return budget["monthly_limit"]
        #
        return self.get_default_limit("project", project_id)

    @web.method()
    def get_user_budget_limit(self, project_id, user_id):
        """Effective monthly per-user limit within a project, or None if unlimited."""
        try:
            budget = context.rpc_manager.timeout(5).elitea_core_get_user_budget(
                project_id=project_id, user_id=user_id,
            )
        except:  # pylint: disable=W0702
            log.exception(
                "Failed to get user budget for project %s user %s", project_id, user_id,
            )
            return None
        #
        if budget is not None:
            if not budget.get("enabled", True):
                return None
            #
            if budget.get("monthly_limit") is not None:
                return budget["monthly_limit"]
        #
        return self.get_default_limit("user", project_id)

    @web.method()
    def get_default_limit(self, scope, project_id):
        """Configured default limit for a scope, or None when defaults are off.

        Personal projects get their own default because they are auto-created per
        user and would otherwise all be unlimited.
        """
        defaults = self.descriptor.config.get("cost_budgets", {}).get("defaults", {})
        #
        if not defaults.get("enabled", False):
            return None
        #
        if scope == "user":
            return defaults.get("user_monthly_limit", None)
        #
        if self.is_personal_project(project_id):
            return defaults.get("personal_project_monthly_limit", None)
        #
        return defaults.get("project_monthly_limit", None)

    @web.method()
    def is_personal_project(self, project_id):
        """True when the project is a user's auto-created personal project."""
        cache = self.runtime_cache.get("personal_project_ids", None)
        #
        if cache is None or time.monotonic() - cache[0] > PERSONAL_PROJECTS_TTL:
            try:
                ids = set(context.rpc_manager.timeout(10).projects_get_personal_project_ids())
            except:  # pylint: disable=W0702
                log.exception("Failed to list personal projects")
                return False
            #
            cache = (time.monotonic(), ids)
            self.runtime_cache["personal_project_ids"] = cache
        #
        return int(project_id) in cache[1]

    @web.method()
    def make_budget_error_response(self, response, iterator):
        """Rewrite a LiteLLM budget-exceeded error into a friendly, stable payload.

        Returns None for anything else so normal responses stream untouched. Applies
        to the failing turn itself, since a turn can be long and must not wait.
        """
        if not self.budgets_enabled():
            return None
        #
        if response.get("status_code") not in (400, 429):
            return None
        #
        try:
            body = b""
            #
            for chunk in iterator:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                body += chunk
                #
                if len(body) > MAX_ERROR_BODY_BYTES:
                    break
            #
            if not is_budget_exceeded_body(body):
                return flask.Response(
                    body,
                    status=response["status_code"],
                    headers=self._body_headers(response, len(body)),
                )
            #
            payload = json.dumps({
                "error": {
                    "message": BUDGET_ERROR_MESSAGE,
                    "type": "budget_exceeded",
                    "code": "project_budget_exceeded",
                },
            }).encode("utf-8")
            #
            headers = self._body_headers(response, len(payload))
            headers["Content-Type"] = "application/json"
            #
            return flask.Response(payload, status=response["status_code"], headers=headers)
        except:  # pylint: disable=W0702
            log.exception("Failed to post-process potential budget error")
            return None

    @web.method()
    def _body_headers(self, response, length):
        """Rebuild response headers for a fully-buffered body."""
        headers = response["headers"]
        #
        headers.remove("Content-Length")
        headers.remove("Transfer-Encoding")
        headers["Content-Length"] = str(length)
        #
        return headers

    @web.method()
    def invalidate_budget_tag(self, tag_name):
        """Drop one cached tag so its limit is re-pushed on the next call."""
        cache = self.runtime_cache.get("budget_tags", None)
        #
        if cache:
            cache.pop(tag_name, None)

    @web.method()
    def invalidate_budget_tag_cache(self, project_id=None):
        """Drop cached tag state so the next shared call re-pushes the limit to LiteLLM."""
        cache = self.runtime_cache.get("budget_tags", None)
        #
        if not cache:
            return
        #
        if project_id is None:
            cache.clear()
            return
        #
        prefix = f"{BUDGET_TAG_PREFIX}{project_id}_"
        #
        for tag_name in [key for key in cache if key.startswith(prefix)]:
            cache.pop(tag_name, None)
