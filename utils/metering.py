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

""" Access to the usage plugin's metering hooks, plus the provider lookup cache """

import time

from pylon.core.tools import log  # pylint: disable=E0611,E0401

from tools import context  # pylint: disable=E0401

MODE_OFF = "off"

# Where prepare_request parks the credential family and the raw model name on proxy_auth, the
# dict this plugin already uses to carry state to the response side. LiteLLM rewrites the
# outbound model name; the costs catalog is keyed by the raw one.
PLATFORM_PROVIDER_AUTH_KEY = "platform_provider"
PLATFORM_RAW_MODEL_AUTH_KEY = "platform_raw_model"

PROVIDER_CACHE_TTL = 60.0
PROVIDER_CACHE_LIMIT = 4096

_provider_cache = {}


def usage_hooks():
    """The usage plugin's hook surface, or None when it is not loaded.

    Resolved on every call rather than at import: lazy lookup is what keeps this plugin
    free of an init_after dependency on usage.
    """
    try:
        import tools  # pylint: disable=C0415
        #
        return tools.usage_hooks
    except (ImportError, AttributeError):
        return None


def usage_mode(hooks):
    """Metering mode, or "off" whenever it cannot be established."""
    if hooks is None:
        return MODE_OFF
    #
    try:
        return hooks.usage_get_mode()
    except:  # pylint: disable=W0702
        log.exception("Failed to read usage mode")
        return MODE_OFF


def resolve_provider(project_id, raw_model_name):
    """Credential family behind a model, cached briefly — never an RPC hop per LLM call."""
    key = (project_id, raw_model_name)
    cached = _provider_cache.get(key)
    now = time.monotonic()
    #
    if cached is not None and cached[1] > now:
        return cached[0]
    #
    try:
        provider = context.rpc_manager.timeout(10).configurations_get_model_provider(
            project_id=project_id, model_name=raw_model_name,
        )
    except:  # pylint: disable=W0702
        # Metering degrades to sniffing the body; it must never fail the call
        log.exception("Failed to resolve provider for model %s", raw_model_name)
        return None
    #
    if len(_provider_cache) >= PROVIDER_CACHE_LIMIT:
        _provider_cache.clear()
    #
    _provider_cache[key] = (provider, now + PROVIDER_CACHE_TTL)
    return provider
