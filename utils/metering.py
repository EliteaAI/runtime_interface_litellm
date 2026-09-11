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

""" The two metering call sites, forwarded to the usage plugin

No policy lives here on purpose: whether a call is metered, how a provider is looked up and what
a streamed body must carry are all the usage plugin's decisions. This file only reaches it.
"""

from pylon.core.tools import log  # pylint: disable=E0611,E0401


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


def prepare_llm_call(proxy_target, proxy_auth, raw_model_name=None, model_project_id=None):
    """Hand over the two facts only this plugin knows: the raw name and where it resolved."""
    hooks = usage_hooks()
    #
    if hooks is None:
        return
    #
    try:
        hooks.prepare_llm_call(proxy_target, proxy_auth, raw_model_name, model_project_id)
    except:  # pylint: disable=W0702
        log.exception("Failed to prepare LLM call metering")


def meter_llm_call(proxy_target, proxy_auth, response, iterator):
    """The iterator to serve; identity unless the usage plugin wants this call metered."""
    hooks = usage_hooks()
    #
    if hooks is None:
        return iterator
    #
    try:
        return hooks.meter_llm_call(proxy_target, proxy_auth, response, iterator)
    except:  # pylint: disable=W0702
        # A metering failure must never cost the user their response
        log.exception("Failed to meter LLM call")
        return iterator
