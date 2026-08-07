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
import re
import time
import datetime

import flask  # pylint: disable=E0401

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import context  # pylint: disable=E0401


BUDGET_TAG_PREFIX = "elitea_proj_"

# off     — no tagging, no writes, no blocking; requests are byte-for-byte pre-feature
# observe — tag and track spend, but never push a limit, so nothing is ever blocked
# enforce — track and block once a limit is exceeded
MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"

BUDGET_MODES = (MODE_OFF, MODE_OBSERVE, MODE_ENFORCE)

# Deliberately period-neutral: the message must read correctly whatever the budget
# period is, so it says "resets" rather than naming a month.
BUDGET_ERROR_MESSAGE = (
    "The budget for shared models has been reached. Requests are unavailable "
    "until the budget resets or an administrator raises the limit."
)

# Which budget tripped. The UI maps these to its own wording and usage links.
SCOPE_PROJECT = "project"
SCOPE_MEMBER = "member"

BUDGET_ERROR_CODES = {
    SCOPE_PROJECT: "project_budget_exceeded",
    SCOPE_MEMBER: "member_budget_exceeded",
}

# Cap on buffering an error body before rewriting it; error payloads are tiny
MAX_ERROR_BODY_BYTES = 64 * 1024

# Percent-of-limit at which the Usage page warns, when nothing is configured
DEFAULT_WARNING_PCT = 80

WARNING_PCT_KEYS = {
    "project": "project_pct",
    "personal_project": "personal_project_pct",
    "user": "user_pct",
}

# How long a limit is trusted before re-reading it on the request path
BUDGET_SYNC_TTL = 60.0

# How long the personal-project id list is cached (it changes only on project creation)
PERSONAL_PROJECTS_TTL = 300.0

# How long a computed warning state is served without re-reading spend. The banner is
# requested on every chat, agent, pipeline and skill page load, and a spend read pages the
# whole month's activity -- without this the interactive path would pay for it every time.
BUDGET_WARNING_TTL = 60.0

# LiteLLM ignores max_budget=null on /tag/update, so "unlimited" is expressed as a
# ceiling nothing can reach. Keeps the tag (and its spend history) while not blocking.
UNLIMITED_BUDGET = 1_000_000_000.0

# Distinguishes "caller did not pass the project row" from "the project has no row"
UNSET = object()

# Cached in place of a limit: this tag has no budget and does not exist in LiteLLM, so
# there is nothing to re-check until an admin sets one (which invalidates the entry).
ABSENT = object()


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


def user_id_from_tag(tag_name):
    """Member id encoded in a per-user budget tag, or None for a project tag."""
    match = re.search(rf"{BUDGET_TAG_PREFIX}\d+_user_(\d+)_", str(tag_name))
    #
    return int(match.group(1)) if match else None


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


def budget_error_scope(body):
    """Which budget tripped, read from the tag name LiteLLM names in its error.

    LiteLLM reports the first tag that is over budget, so exactly one scope applies
    even when a request carries both a project and a per-user tag. Falls back to
    project scope when the tag is missing or unrecognised: a slightly generic
    message is better than telling the user the wrong budget blocked them.
    """
    try:
        text = body.decode("utf-8", errors="ignore")
    except AttributeError:
        text = str(body)
    #
    match = re.search(rf"{BUDGET_TAG_PREFIX}(\d+)_user_(\d+)_", text)
    #
    return SCOPE_MEMBER if match else SCOPE_PROJECT


def budget_error_target(body):
    """Which project, and which member if any, the blocking tag belongs to.

    The tag LiteLLM names in its error carries both ids, so no request context is needed
    to work out who to notify. Returns (project_id, user_id), either possibly None.
    """
    try:
        text = body.decode("utf-8", errors="ignore")
    except AttributeError:
        text = str(body)
    #
    member = re.search(rf"{BUDGET_TAG_PREFIX}(\d+)_user_(\d+)_", text)
    #
    if member:
        return int(member.group(1)), int(member.group(2))
    #
    project = re.search(rf"{BUDGET_TAG_PREFIX}(\d+)_", text)
    #
    return (int(project.group(1)) if project else None), None


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.method()
    def budgets_mode(self):
        """Current cost-budgets mode: off, observe or enforce.

        Legacy boolean `enabled` is still honoured so an existing config keeps working.
        """
        config = self.descriptor.config.get("cost_budgets", {})
        #
        mode = config.get("mode", None)
        #
        if mode is None:
            return MODE_ENFORCE if config.get("enabled", False) else MODE_OFF
        #
        mode = str(mode).strip().lower()
        #
        if mode not in BUDGET_MODES:
            log.warning("Unknown cost_budgets.mode %r, treating as off", mode)
            return MODE_OFF
        #
        return mode

    @web.method()
    def budgets_enabled(self):
        """True when budgets do anything at all (tracking or enforcing)."""
        return self.budgets_mode() != MODE_OFF

    @web.method()
    def budgets_enforcing(self):
        """True only when limits should actually block calls."""
        return self.budgets_mode() == MODE_ENFORCE

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
    def ensure_budget_tag(self, project_id, tag_name, limit_getter, check_threshold=True):
        """Create/refresh a LiteLLM tag budget.

        Provisioning lazily on first shared call means enforcement self-heals if the
        tag is ever deleted on the LiteLLM side, without needing a scheduled job.
        Re-checked only every BUDGET_SYNC_TTL seconds so the hot path stays cheap;
        an explicit budget change pushes immediately via the RPC instead of waiting.

        check_threshold=False is for bulk callers: a spend read per project is fine for
        one tag on a request, but not for a loop over every project at once.
        """
        # Observe mode needs no LiteLLM write at all: an untracked tag still accrues
        # spend in the daily aggregate the dashboard reads, and pushing no ceiling
        # means nothing can be blocked.
        if not self.budgets_enforcing():
            return
        #
        cache = self.runtime_cache.setdefault("budget_tags", {})
        #
        now = time.monotonic()
        checked_at, cached_limit = cache.get(tag_name, (0.0, None))
        #
        if now - checked_at < BUDGET_SYNC_TTL:
            return
        #
        limit = limit_getter(project_id)
        #
        # Already confirmed absent from LiteLLM and still unbudgeted: nothing to check.
        if limit is None and cached_limit is ABSENT:
            cache[tag_name] = (now, ABSENT)
            return
        #
        # None means unlimited: lift any previously-set ceiling instead of leaving the
        # old value enforcing, but don't create a tag that never had a budget.
        max_budget = UNLIMITED_BUDGET if limit is None else limit
        #
        try:
            if limit is None:
                result = self.service_node.call.litellm_api_call(
                    "tag_update_if_exists",
                    tag_name,
                    max_budget=max_budget,
                )
                #
                cache[tag_name] = (now, ABSENT if result is None else limit)
                return
            #
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
        #
        # Rides this same throttled tick rather than running per request, so the warning
        # costs at most one spend read per tag per BUDGET_SYNC_TTL. Also reached when an
        # admin saves a budget, which is deliberate: a limit set below current spend is
        # worth flagging straight away rather than at the next call.
        if check_threshold and limit is not None:
            self.check_budget_threshold(tag_name, project_id, limit)

    @web.method()
    def check_budget_threshold(self, tag_name, project_id, limit):
        """Notify once when a tag's spend reaches its configured warning threshold.

        Spend comes from the counter LiteLLM maintains on the tag itself, which is what its
        own enforcement reads, rather than the daily activity report.

        Only ever notifies: a failure here must leave the call untouched, so everything is
        swallowed. Deduplication lives in elitea_core, which compares against the threshold
        already alerted for this period.
        """
        try:
            user_id = user_id_from_tag(tag_name)
            #
            scope = "user" if user_id is not None else (
                "personal_project" if self.is_personal_project(project_id) else "project"
            )
            #
            threshold = self.get_warning_threshold(scope)
            #
            spend = self.read_tag_alert_spend(tag_name)
            #
            if spend is None or limit <= 0:
                return
            #
            pct = spend / limit * 100
            #
            if pct < threshold or pct >= 100:
                # At or over the limit the block itself raises the alert, so a warning
                # here would only duplicate it
                return
            #
            claimed = context.rpc_manager.timeout(10).elitea_core_claim_budget_alert(
                project_id=project_id,
                period=f"{datetime.datetime.now(datetime.timezone.utc):%Y%m}",
                pct=int(threshold),
                user_id=user_id,
            )
            #
            if not claimed:
                return
            #
            context.rpc_manager.timeout(15).elitea_core_notify_budget_event(
                project_id=project_id,
                kind="threshold",
                pct=int(round(pct)),
                user_id=user_id,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to check budget threshold for tag %s", tag_name)

    @web.method()
    def read_tag_alert_spend(self, tag_name):
        """Current-period spend for one tag, or None if it could not be read.

        Uses the same daily aggregate the usage pages read, so a warning agrees with what
        an admin sees. The running counter on the tag row itself would be cheaper, but
        LiteLLM never returns it: /tag/info builds its response from an explicit field
        list that omits spend, and enforcement reads that column directly over Prisma.

        Cost is acceptable only because callers are throttled to once per tag per
        BUDGET_SYNC_TTL — never call this per request.
        """
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            #
            result = self.read_tag_spend(tag_name, now) or {}
            #
            if not result.get("available"):
                return None
            #
            return float(result.get("spend", 0) or 0)
        except:  # pylint: disable=W0702
            log.exception("Failed to read spend for tag %s", tag_name)
            return None

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
    def get_user_budget_limit(self, project_id, user_id, project_budget=UNSET):
        """Effective monthly per-user limit within a project, or None if unlimited.

        Resolves in three tiers: the member's own row, then the project's member default,
        then the platform default. Callers looping over many members should read the
        project row once and pass it as project_budget rather than paying an RPC each.

        A row with enabled=false exempts the member from the *platform* default only. A
        limit an admin set on this project still applies, so "set a limit for everyone
        here" cannot be silently undone by a member row nobody meant to opt out.

        A personal project has one member, its owner, so its project budget already IS that
        member's budget. A second limit there can only duplicate or silently override it --
        and being invisible on a page that shows only the project scope, it blocked users
        who could see budget remaining. Resolved before any row is read.
        """
        if self.is_personal_project(project_id):
            return None
        #
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
        exempt = budget is not None and not budget.get("enabled", True)
        #
        if budget is not None and not exempt and budget.get("monthly_limit") is not None:
            return budget["monthly_limit"]
        #
        return self.get_member_default_limit(project_id, project_budget, exempt=exempt)

    @web.method()
    def get_member_default_limit(self, project_id, project_budget=UNSET, exempt=False):
        """The project's own member default, or the platform default when it has none.

        The project's `enabled` flag is not consulted: it marks the project's *own* limit
        exempt, while a member default is a separately-set value in its own right.
        """
        if project_budget is UNSET:
            try:
                project_budget = context.rpc_manager.timeout(5).elitea_core_get_project_budget(
                    project_id=project_id,
                )
            except:  # pylint: disable=W0702
                log.exception("Failed to get budget for project %s", project_id)
                project_budget = None
        #
        if project_budget and project_budget.get("member_default_limit") is not None:
            return project_budget["member_default_limit"]
        #
        return None if exempt else self.get_default_limit("user", project_id)

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
    def get_warning_threshold(self, scope):
        """Percent-of-limit at which the Usage page warns for a budget scope.

        Falls back to the default for an unknown scope or an out-of-range value, so a
        bad config degrades to the previous behaviour rather than silencing warnings.
        """
        thresholds = self.descriptor.config.get("cost_budgets", {}).get(
            "warning_thresholds", {},
        )
        #
        try:
            value = int(thresholds.get(WARNING_PCT_KEYS.get(scope, ""), DEFAULT_WARNING_PCT))
        except (TypeError, ValueError):
            return DEFAULT_WARNING_PCT
        #
        return value if 1 <= value <= 100 else DEFAULT_WARNING_PCT

    @web.method()
    def get_budget_warning_state(self, project_id, user_id):
        """Whether to warn this user that a budget is nearing its limit, and which one.

        Cached for BUDGET_WARNING_TTL because the caller is a page load: every chat, agent,
        pipeline and skill view asks on open, and resolving this reads spend, which pages a
        month of activity. One read per scope per minute is shared by every member of the
        project.

        The member budget takes priority over the project budget and only one scope is ever
        returned, so the UI has no precedence rule to get wrong.
        """
        cache = self.runtime_cache.setdefault("budget_warning", {})
        key = (int(project_id), int(user_id))
        #
        cached_at, payload = cache.get(key, (0.0, None))
        #
        if payload is not None and time.monotonic() - cached_at < BUDGET_WARNING_TTL:
            return payload
        #
        payload = self._resolve_budget_warning(project_id, user_id)
        cache[key] = (time.monotonic(), payload)
        #
        return payload

    @web.method()
    def _resolve_budget_warning(self, project_id, user_id):
        """Compute the warning state, ignoring the cache. See get_budget_warning_state."""
        no_warning = {
            "scope": None, "percent_used": None, "warning_pct": None, "should_warn": False,
        }
        #
        # Observe mode tracks spend but never blocks, so warning that requests are about to
        # become unavailable would not be true
        if not self.budgets_enforcing():
            return no_warning
        #
        try:
            # Member first: it is the one that stops this user specifically. Each scope is
            # resolved lazily, so a member warning costs no project lookup or spend read.
            scopes = (
                (
                    SCOPE_MEMBER,
                    lambda: self.get_user_budget_limit(project_id, user_id),
                    lambda: make_user_budget_tag(project_id, user_id),
                ),
                (
                    SCOPE_PROJECT,
                    lambda: self.get_project_budget_limit(project_id),
                    lambda: make_budget_tag(project_id),
                ),
            )
            #
            for scope, get_limit, get_tag in scopes:
                limit = get_limit()
                #
                if limit is None or limit <= 0:
                    continue
                #
                state = self._warning_for_scope(scope, limit, get_tag(), project_id)
                #
                if state is not None:
                    return state
        except:  # pylint: disable=W0702
            log.exception(
                "Failed to resolve budget warning for project %s user %s", project_id, user_id,
            )
        #
        return no_warning

    @web.method()
    def _warning_for_scope(self, scope, limit, tag_name, project_id):
        """Warning state for one scope, or None when that scope has nothing to warn about.

        The caller has already established the limit is finite and above zero.
        """
        spend = self.read_tag_alert_spend(tag_name)
        #
        # Unreadable spend yields no warning rather than a fabricated percentage
        if spend is None:
            return None
        #
        pct = spend / limit * 100
        #
        # Same scope keys check_budget_threshold uses, so the banner and the notification
        # fire at the same percentage rather than at two different ones
        threshold = self.get_warning_threshold(
            "user" if scope == SCOPE_MEMBER else (
                "personal_project" if self.is_personal_project(project_id) else "project"
            )
        )
        #
        # At or over the limit the block itself is the message, so this banner stays out of
        # the way -- and a stale reading cannot contradict a rejection the user just saw
        if pct < threshold or pct >= 100:
            return None
        #
        return {
            "scope": scope,
            "percent_used": round(pct),
            "warning_pct": threshold,
            "should_warn": True,
        }

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
            self.notify_budget_limit_reached(body)
            #
            payload = json.dumps({
                "error": {
                    "message": BUDGET_ERROR_MESSAGE,
                    "type": "budget_exceeded",
                    "code": BUDGET_ERROR_CODES[budget_error_scope(body)],
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
    def notify_budget_limit_reached(self, body):
        """Notify once that a budget is exhausted, driven by the block itself.

        The rejection is the event, so nothing has to poll for it. Claiming at 100 also
        means a later threshold warning cannot fire for a budget already known to be full.
        """
        try:
            project_id, user_id = budget_error_target(body)
            #
            if project_id is None:
                return
            #
            claimed = context.rpc_manager.timeout(10).elitea_core_claim_budget_alert(
                project_id=project_id,
                period=f"{datetime.datetime.now(datetime.timezone.utc):%Y%m}",
                pct=100,
                user_id=user_id,
            )
            #
            if not claimed:
                return
            #
            context.rpc_manager.timeout(15).elitea_core_notify_budget_event(
                project_id=project_id, kind="limit", user_id=user_id,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to send budget limit notification")

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
    def restore_budget_ceilings(self):
        """Re-push stored limits for every project that has one.

        Called when enforcement is switched on so limits apply straight away rather
        than on each project's next shared call.
        """
        try:
            budgets = context.rpc_manager.timeout(10).elitea_core_list_project_budgets() or {}
        except:  # pylint: disable=W0702
            log.exception("Failed to list project budgets while restoring ceilings")
            return 0
        #
        restored = 0
        #
        for project_id in budgets:
            try:
                tag_name = make_budget_tag(int(project_id))
                self.invalidate_budget_tag(tag_name)
                # No threshold check here: this runs for every budgeted project on a
                # config change, and a spend read each would be a sweep over the whole
                # environment. Each project is checked on its own next call instead.
                self.ensure_budget_tag(
                    int(project_id), tag_name, self.get_project_budget_limit,
                    check_threshold=False,
                )
                restored += 1
            except:  # pylint: disable=W0702
                log.exception("Failed to restore budget ceiling for project %s", project_id)
        #
        if restored:
            log.info("Restored %s project budget ceiling(s) on entering enforce mode", restored)
        #
        return restored

    @web.method()
    def release_budget_ceilings(self):
        """Lift every Elitea budget ceiling currently set in LiteLLM.

        Called when enforcement is turned off so a previously-pushed limit stops
        blocking calls. Tags and their accrued spend are left intact.
        """
        try:
            tags = self.service_node.call.litellm_api_call("tag_list") or []
        except:  # pylint: disable=W0702
            log.exception("Failed to list tags while releasing budget ceilings")
            return 0
        #
        released = 0
        #
        for tag in tags:
            tag_name = (tag or {}).get("name") or ""
            #
            if not tag_name.startswith(BUDGET_TAG_PREFIX):
                continue
            #
            budget = (tag or {}).get("litellm_budget_table") or {}
            max_budget = budget.get("max_budget")
            #
            if max_budget is None or max_budget >= UNLIMITED_BUDGET:
                continue
            #
            try:
                self.service_node.call.litellm_api_call(
                    "tag_update_if_exists",
                    tag_name,
                    max_budget=UNLIMITED_BUDGET,
                )
                released += 1
            except:  # pylint: disable=W0702
                log.exception("Failed to release budget ceiling for %s", tag_name)
        #
        if released:
            log.info("Released %s budget ceiling(s) after leaving enforce mode", released)
        #
        return released

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
