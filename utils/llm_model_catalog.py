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
""" LLM model catalog: the gateway's loaded LiteLLM catalog in the LLM model form's vocabulary """

import threading
import time

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611

CATALOG_HTTP_SECONDS = 8
CATALOG_SERVICE_SECONDS = 10
CATALOG_TTL_SECONDS = 600
FAILED_FETCH_TTL_SECONDS = 60

FORM_MODES = frozenset({"chat", "responses"})

LITELLM_PREFIX_BY_CREDENTIAL = {
    "open_ai": "openai",
    "azure_open_ai": "azure",
    "amazon_bedrock": "bedrock",
    "vertex_ai": "vertex_ai",
    "ollama": "ollama",
    "ai_dial:azure": "azure",
    "ai_dial:openai": "openai",
    "ai_dial:anthropic": "anthropic",
}

EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
EFFORT_FLAGS = {
    "none": "supports_none_reasoning_effort",
    "minimal": "supports_minimal_reasoning_effort",
    "low": "supports_low_reasoning_effort",
    "xhigh": "supports_xhigh_reasoning_effort",
    "max": "supports_max_reasoning_effort",
}
DECLARED_EFFORTS_KEY = "reasoning_effort_levels"
OPT_OUT_EFFORTS = ("minimal", "low")
OPT_IN_EFFORTS = ("xhigh", "max")
UNCONDITIONAL_EFFORTS = ("medium", "high")
GPT_REASONING_SERIES_MARKERS = ("gpt-5", "gpt-6")
EFFORTS_CHAT_ANTHROPIC_REJECTS = ("none", "minimal")
THINKING_TYPES_WITHOUT_OFF_PATH = ("adaptive", "always_on")
TOKEN_BUDGET_THINKING_TYPE = "enabled"
TOKEN_BUDGET_EFFORTS = ("low", "medium", "high")
EFFORTS_ABOVE_TOKEN_BUDGET = ("xhigh", "max")
CLAUDE_ONLY_PROVIDERS = ("anthropic", "vertex_ai-anthropic_models")
PREFERRED_DEFAULT_EFFORT = "medium"

_fill_lock = threading.Lock()
_cached_models = None
_cache_expires_at = 0.0


def llm_model_catalog(gateway):
    return {"prefixes": dict(LITELLM_PREFIX_BY_CREDENTIAL), "models": cached_form_models(gateway)}


def cached_form_models(gateway):
    global _cached_models, _cache_expires_at  # pylint: disable=W0603
    if _cached_models is not None and time.monotonic() < _cache_expires_at:
        return _cached_models
    with _fill_lock:
        if _cached_models is not None and time.monotonic() < _cache_expires_at:
            return _cached_models
        try:
            gateway_catalog = gateway(timeout=CATALOG_SERVICE_SECONDS).litellm_api_call(
                "model_cost_map", timeout=CATALOG_HTTP_SECONDS,
            )
            models = form_models(gateway_catalog)
        except Exception as error:  # pylint: disable=W0718
            log.warning("LLM model catalog is unavailable: %s", error)
            models = {}
        ttl = CATALOG_TTL_SECONDS if models else FAILED_FETCH_TTL_SECONDS
        _cached_models, _cache_expires_at = models or _cached_models or {}, time.monotonic() + ttl
        return _cached_models


def form_models(catalog):
    return {
        key: form_entry(key, entry, catalog)
        for key, entry in catalog.items()
        if isinstance(entry, dict) and entry.get("mode") in FORM_MODES
    }


def form_entry(key, entry, catalog):
    form = {}
    if isinstance(entry.get("litellm_provider"), str):
        form["provider"] = entry["litellm_provider"]
    thinking_type = form_thinking_type(entry)
    efforts = supported_reasoning_efforts(key, entry, catalog)
    if isinstance(entry.get("supports_reasoning"), bool):
        form["supports_reasoning"] = entry["supports_reasoning"]
    elif efforts == ():
        form["supports_reasoning"] = False
    if is_token_budget_claude(key, entry, thinking_type, efforts):
        thinking_type, efforts = TOKEN_BUDGET_THINKING_TYPE, TOKEN_BUDGET_EFFORTS
    if thinking_type:
        form["thinking_type"] = thinking_type
    if efforts and thinking_type in THINKING_TYPES_WITHOUT_OFF_PATH:
        efforts = tuple(effort for effort in efforts if effort not in EFFORTS_CHAT_ANTHROPIC_REJECTS)
    default_effort = form_default_effort(entry, efforts or ())
    if default_effort:
        form["supported_efforts"] = list(efforts)
        form["default_effort"] = default_effort
    #
    if isinstance(entry.get("supports_vision"), bool):
        form["supports_vision"] = entry["supports_vision"]
    #
    has_levels_without_off = not thinking_type and efforts and "none" not in efforts
    if thinking_type in THINKING_TYPES_WITHOUT_OFF_PATH or has_levels_without_off:
        form["reasoning_locked"] = True
    return form


def is_token_budget_claude(key, entry, thinking_type, efforts):
    """ A reasoning Claude entry with no adaptive or always-on flag and no level above high: Claude 4.5 and older """
    if entry.get("supports_reasoning") is not True or thinking_type:
        return False
    if any(effort in EFFORTS_ABOVE_TOKEN_BUDGET for effort in efforts or ()):
        return False
    provider = str(entry.get("litellm_provider") or "")
    return provider in CLAUDE_ONLY_PROVIDERS or (provider.startswith("bedrock") and "claude" in key)


def form_thinking_type(entry):
    if entry.get("thinking_always_on") is True:
        return "always_on"
    if entry.get("supports_adaptive_thinking") is True:
        return "adaptive"
    return None


def form_default_effort(entry, efforts):
    selectable = [effort for effort in efforts if effort != "none"]
    litellm_default = entry.get("default_reasoning_effort")
    if litellm_default in selectable:
        return litellm_default
    if PREFERRED_DEFAULT_EFFORT in selectable:
        return PREFERRED_DEFAULT_EFFORT
    return selectable[0] if selectable else None


def supported_reasoning_efforts(key, entry, catalog):
    """ Port of LiteLLM 1.108.3 resolve_supported_reasoning_efforts(entry, deployment_is_mapped=True) """
    supports_reasoning = entry.get("supports_reasoning")
    if supports_reasoning is False:
        return ()
    #
    bare_entry = bare_twin_entry(key, entry, catalog)
    flags = {
        effort: entry.get(flag) if entry.get(flag) is not None else bare_entry.get(flag)
        for effort, flag in EFFORT_FLAGS.items()
    }
    has_per_level_flag = any(value is not None for value in flags.values())
    if supports_reasoning is not True and not has_per_level_flag:
        return ()
    #
    declared = entry.get(DECLARED_EFFORTS_KEY)
    if declared is None:
        declared = bare_entry.get(DECLARED_EFFORTS_KEY)
    if isinstance(declared, (list, tuple)):
        return tuple(effort for effort in EFFORT_ORDER if effort in declared)
    #
    if not has_per_level_flag:
        return None
    #
    allowed = set(UNCONDITIONAL_EFFORTS)
    allowed.update(effort for effort in OPT_OUT_EFFORTS if flags[effort] is not False)
    allowed.update(effort for effort in OPT_IN_EFFORTS if flags[effort] is True)
    if accepts_none_effort(key, entry, flags["none"]):
        allowed.add("none")
    return tuple(effort for effort in EFFORT_ORDER if effort in allowed)


def bare_twin_entry(key, entry, catalog):
    provider = entry.get("litellm_provider")
    if not isinstance(provider, str) or not key.startswith(f"{provider}/"):
        return {}
    twin = catalog.get(key.removeprefix(f"{provider}/"))
    return twin if isinstance(twin, dict) else {}


def accepts_none_effort(key, entry, none_flag):
    if entry.get("litellm_provider") == "azure" and is_azure_gpt_reasoning_series(key):
        return none_flag is True
    return none_flag is not False


def is_azure_gpt_reasoning_series(key):
    is_reasoning_series = (
        any(marker in key for marker in GPT_REASONING_SERIES_MARKERS)
        and "gpt-5-chat" not in key.split("/")[-1]
    )
    return is_reasoning_series or "gpt5_series" in key
