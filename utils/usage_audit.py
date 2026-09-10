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

""" Double-count suppression for LLM traffic already audited upstream """

#
# Requests made by Elitea's own runtime (agent/chat predict) travel through this
# same proxy, but their token usage is already captured on the runtime side as
# generation spans (AuditLangChainCallback / Langfuse). Such requests carry this
# header so the proxy skips its own audit and analytics are not double-counted.
# External clients (Claude Code and friends with a personal token) never send it
# and remain audited here.
#
INTERNAL_AUDIT_HEADER = "X-Elitea-Audited"

_TRUTHY_HEADER_VALUES = ("1", "true", "yes", "on")


def is_audited_elsewhere(headers):
    """True when the caller states this LLM call is already audited upstream."""
    try:
        value = headers.get(INTERNAL_AUDIT_HEADER)
    except Exception:  # pylint: disable=W0703
        return False
    #
    if value is None:
        return False
    #
    return str(value).strip().lower() in _TRUTHY_HEADER_VALUES
