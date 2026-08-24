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

""" Token usage capture for raw LLM-proxy traffic (e.g. Claude Code over /v1/messages) """

import json
import os

from pylon.core.tools import log  # pylint: disable=E0611,E0401

AUDIT_TRACER_NAME = "audit-trail"

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    with open(os.path.join(_PLUGIN_DIR, "metadata.json"), "r") as _f:
        _PLUGIN_VERSION = json.load(_f).get("version", "0.0.0")
except Exception:  # pylint: disable=W0703
    _PLUGIN_VERSION = "0.0.0"

_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens")
_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens")


def _first_int(usage, keys):
    for key in keys:
        value = usage.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _merge_usage(usage, target):
    """Merge one usage dict into `target` (input/cache: first value wins, output: latest wins).

    Anthropic's streamed `message_start` reports a placeholder output_tokens
    that only the final `message_delta` corrects, so output must always take
    the latest observed value rather than the first.
    """
    if not isinstance(usage, dict):
        return
    if target.get("input_tokens") is None:
        target["input_tokens"] = _first_int(usage, _INPUT_TOKEN_KEYS)
    output_tokens = _first_int(usage, _OUTPUT_TOKEN_KEYS)
    if output_tokens is not None:
        target["output_tokens"] = output_tokens
    if not target.get("cache_read_tokens"):
        target["cache_read_tokens"] = _first_int(usage, ("cache_read_input_tokens",)) or 0
    if not target.get("cache_creation_tokens"):
        target["cache_creation_tokens"] = _first_int(usage, ("cache_creation_input_tokens",)) or 0


def extract_usage(buffer, is_sse):
    """Best-effort (model, input_tokens, output_tokens, cache_*) from a raw proxied
    LLM response body — either a single JSON object or an SSE stream, covering both
    the Anthropic Messages API shape and OpenAI-compatible chat completions.
    """
    result = {
        "model": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    if not buffer:
        return result
    #
    try:
        text = bytes(buffer).decode("utf-8", errors="replace")
    except Exception:  # pylint: disable=W0703
        return result
    #
    if not is_sse:
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return result
        if isinstance(payload, dict):
            result["model"] = payload.get("model")
            _merge_usage(payload.get("usage"), result)
        return result
    #
    for block in text.split("\n\n"):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            message = payload.get("message")
            if isinstance(message, dict):
                result["model"] = result["model"] or message.get("model")
                _merge_usage(message.get("usage"), result)
            result["model"] = result["model"] or payload.get("model")
            _merge_usage(payload.get("usage"), result)
    #
    return result


def record_llm_proxy_usage(buffer, is_sse, *, user_id, user_email, project_id,
                            duration_ms, is_error, start_time_ns, end_time_ns):
    """Emit an audit-trail span for a raw LLM-proxy call.

    Flows through the tracing plugin's AuditSpanProcessor exactly like every
    other LLM generation (predict flow, LangChain callback, Langfuse) - no
    direct audit_events write, and no dependency on pylon_predicts.
    """
    usage = extract_usage(buffer, is_sse)
    if usage["input_tokens"] is None and usage["output_tokens"] is None:
        return
    #
    try:
        from opentelemetry import trace  # pylint: disable=C0415,E0401

        attrs = {
            "audit.observation.type": "generation",
            "audit.model.name": usage["model"] or "unknown_model",
            "audit.duration_ms": duration_ms,
            "audit.is_error": bool(is_error),
        }
        if usage["input_tokens"] is not None:
            attrs["audit.input_tokens"] = usage["input_tokens"]
        if usage["output_tokens"] is not None:
            attrs["audit.output_tokens"] = usage["output_tokens"]
        if usage["cache_read_tokens"]:
            attrs["audit.cache_read_tokens"] = usage["cache_read_tokens"]
        if usage["cache_creation_tokens"]:
            attrs["audit.cache_creation_tokens"] = usage["cache_creation_tokens"]
        if user_id is not None:
            attrs["user.id"] = int(user_id)
        if user_email:
            attrs["user.email"] = str(user_email)
        if project_id is not None:
            attrs["project.id"] = int(project_id)
        #
        tracer = trace.get_tracer(AUDIT_TRACER_NAME, _PLUGIN_VERSION)
        span = tracer.start_span(
            name=usage["model"] or "unknown_model",
            attributes=attrs,
            start_time=start_time_ns,
        )
        span.end(end_time=end_time_ns)
    except Exception:  # pylint: disable=W0703
        log.debug("usage_audit: failed to emit LLM proxy audit span", exc_info=True)
