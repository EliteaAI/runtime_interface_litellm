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

""" LLM model connection test: request params, failure categories, message scrubbing """

import re
import time
from urllib.parse import urlsplit

TEST_DEADLINE_SECONDS = 30
CREDENTIAL_LOOKUP_SERVICE_SECONDS = 5
CREDENTIAL_LOOKUP_HTTP_SECONDS = 4
GATEWAY_HTTP_MARGIN_SECONDS = 2
PROVIDER_MARGIN_SECONDS = 3

REASONING_PARAMS = ("reasoning_effort", "thinking", "reasoning")
MAX_MESSAGE_LENGTH = 300

AUTHENTICATION_FAILED = "Authentication failed"
MODEL_NOT_FOUND = "Model not found"
WRONG_PROTOCOL_OR_ROUTE = "Wrong API protocol or route"
TIMED_OUT = "Timed out"
RATE_LIMITED = "Rate limited"
CONNECTION_FAILED = "Connection failed"

TIMED_OUT_MESSAGE = "the provider did not answer in time"
UNREGISTERED_CREDENTIAL_MESSAGE = (
    "the selected AI credentials are not registered in the LLM gateway; re-save the credentials and try again"
)
UNMAPPABLE_SETTINGS_MESSAGE = "the model settings could not be turned into a request"

_STACK_TRACE_MARKER = "\nstack trace:"
_LITELLM_PREFIX = re.compile(r"^(?:litellm\.\w+:\s*)+(?:\w+(?:Error|Exception):\s*)*", re.IGNORECASE)
_PROVIDER_PREFIX = re.compile(r"^(?:\w+Exception\s*(?:-\s*|\w+Error\s*-\s*)?)", re.IGNORECASE)

_OBJECT_REPR = re.compile(r"<[^<>]*\bobject at 0x[0-9a-fA-F]+>")
_LIBRARY_HINT = re.compile(r"\s*Handle with `[^`]*`\.?", re.IGNORECASE)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
_BEARER = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_KEY_ASSIGNMENT = re.compile(r"\b(api[-_ ]?key|authorization|token)\b(\s*[=:]\s*)\S+", re.IGNORECASE)
_SECRET_LIKE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-.]{3,}|\b[A-Za-z0-9]{32,}\b")

_TIMEOUT_SIGNS = ("litellm.timeout", "apitimeouterror", "timed out", "readtimeout", "timeout exceeded")
_RATE_LIMIT_SIGNS = ("ratelimiterror", "rate limit", "status code: 429", "error code: 429")
_AUTHENTICATION_SIGNS = (
    "authenticationerror", "permissiondeniederror", "status code: 401", "status code: 403",
    "error code: 401", "error code: 403", "invalid api key", "incorrect api key", "unauthorized", "forbidden",
)
_MODEL_NOT_FOUND_SIGNS = (
    "deploymentnotfound", "deployment for this resource does not exist", "model_not_found",
    "does not exist", "not allowed to access model", "invalid model", "unknown model", "no such model",
    "model not found",
)
_ROUTE_SIGNS = (
    "notfounderror", "resource not found", "status code: 404", "error code: 404",
    "status code: 405", "error code: 405", "method not allowed", "unsupported route",
)


def run_connection_test(mapped_model, gateway, secrets=()):
    if not mapped_model:
        return failure(CONNECTION_FAILED, UNMAPPABLE_SETTINGS_MESSAGE)
    #
    litellm_params = mapped_model.get("litellm_params") or {}
    started = time.monotonic()
    try:
        is_registered = is_credential_registered(gateway, litellm_params.get("litellm_credential_name"))
    except Exception as error:  # pylint: disable=W0718
        return failure(*classify_failure(str(error), secrets))
    #
    if not is_registered:
        return failure(CONNECTION_FAILED, UNREGISTERED_CREDENTIAL_MESSAGE)
    #
    service_timeout = TEST_DEADLINE_SECONDS - (time.monotonic() - started)
    gateway_http_timeout = service_timeout - GATEWAY_HTTP_MARGIN_SECONDS
    try:
        result = gateway(timeout=service_timeout).litellm_api_call(
            "health_test_connection",
            litellm_params=build_test_params(litellm_params, gateway_http_timeout - PROVIDER_MARGIN_SECONDS),
            mode="chat",
            timeout=gateway_http_timeout,
        )
    except Exception as error:  # pylint: disable=W0718
        return failure(*classify_failure(str(error), secrets))
    #
    if (result or {}).get("status") == "success":
        return {"latency_ms": round((time.monotonic() - started) * 1000)}
    return failure(*classify_failure(((result or {}).get("result") or {}).get("error"), secrets))


def credential_secrets(credentials):
    api_base = str(credentials.get("api_base") or "")
    candidates = [str(credentials.get("api_key") or ""), api_base, urlsplit(api_base).hostname or ""]
    return sorted({secret for secret in candidates if len(secret) > 3}, key=len, reverse=True)


def is_credential_registered(gateway, credential_name):
    if not credential_name:
        return False
    return gateway(timeout=CREDENTIAL_LOOKUP_SERVICE_SECONDS).litellm_api_call(
        "credential_exists", credential_name=credential_name, timeout=CREDENTIAL_LOOKUP_HTTP_SECONDS,
    )


def build_test_params(litellm_params, provider_timeout):
    params = {
        key: value for key, value in litellm_params.items() if key not in REASONING_PARAMS
    }
    params["model"] = provider_qualified_model(params.get("model", ""), params.get("custom_llm_provider"))
    params["timeout"] = provider_timeout
    params["max_retries"] = 0
    return params


def provider_qualified_model(model, custom_llm_provider):
    if not custom_llm_provider or model.startswith(f"{custom_llm_provider}/"):
        return model
    return f"{custom_llm_provider}/{model}"


def classify_failure(error_text, secrets=()):
    text = str(error_text or "")
    headline = text.split(_STACK_TRACE_MARKER, 1)[0]
    lowered_headline = headline.lower()
    #
    if _contains_any(f"{lowered_headline}\n{_raised_exception_line(text).lower()}", _TIMEOUT_SIGNS):
        return TIMED_OUT, TIMED_OUT_MESSAGE
    #
    message = scrub(provider_message(headline), secrets)
    #
    if _contains_any(lowered_headline, _RATE_LIMIT_SIGNS):
        return RATE_LIMITED, message
    if _contains_any(lowered_headline, _AUTHENTICATION_SIGNS):
        return AUTHENTICATION_FAILED, message
    if _contains_any(lowered_headline, _MODEL_NOT_FOUND_SIGNS):
        return MODEL_NOT_FOUND, message
    if _contains_any(lowered_headline, _ROUTE_SIGNS):
        if "model" in provider_message(headline).lower():
            return MODEL_NOT_FOUND, message
        return WRONG_PROTOCOL_OR_ROUTE, message
    return CONNECTION_FAILED, message


def provider_message(headline):
    message = headline.strip()
    while True:
        stripped = _PROVIDER_PREFIX.sub("", _LITELLM_PREFIX.sub("", message)).strip()
        if stripped == message:
            return message
        message = stripped


def scrub(text, secrets=()):
    result = str(text or "")
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[redacted]")
    result = _LIBRARY_HINT.sub("", result)
    result = _OBJECT_REPR.sub("", result)
    result = _URL.sub("[url]", result)
    result = _IPV4.sub("[address]", result)
    result = _BEARER.sub("Bearer [redacted]", result)
    result = _KEY_ASSIGNMENT.sub(r"\1\2[redacted]", result)
    result = _SECRET_LIKE.sub("[redacted]", result)
    result = re.sub(r"\.{2,}(?=\s|$)", ".", " ".join(result.split()))
    if len(result) > MAX_MESSAGE_LENGTH:
        result = result[:MAX_MESSAGE_LENGTH - 1].rstrip() + "…"
    return result


def failure(category, message=""):
    return {
        "success": False,
        "message": f"{category}: {message}" if message else category,
    }


def _raised_exception_line(text):
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _contains_any(text, signs):
    return any(sign in text for sign in signs)
