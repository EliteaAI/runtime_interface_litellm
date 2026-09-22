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

""" Mappers """

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611


def to_credential(  # pylint: disable=R0913
        configuration_info,
        standalone=True
):
    """ Mapper """
    configuration_data = configuration_info["data"]
    #
    api_base = configuration_data["api_base"]
    #
    credential_values = {
        "api_base": api_base,
    }
    #
    api_key = configuration_data["api_key"]
    #
    if api_key is not None and api_key and api_key != "-":
        credential_values["api_key"] = api_key
    #
    api_version = configuration_data["api_version"]
    #
    if api_version is not None and api_version and api_version != "-":
        credential_values["api_version"] = api_version
    #
    if standalone:
        target_project = configuration_info["project_id"]
        target_uuid = configuration_info["uuid"]
        #
        result = {
            "credential_name": f"{target_project}_{target_uuid}",
            "credential_values": credential_values,
            "credential_info": {
                "custom_llm_provider": "Azure",
            },
        }
    else:
        result = credential_values
    #
    return result


def _protocol_params(api_protocol, model_name, credential_data):
    """ Helper: DIAL fronts three upstream protocols, each on its own route """
    api_base = str(credential_data.get("api_base") or "").rstrip("/")
    #
    if api_protocol == "anthropic" and api_base:
        return {
            "custom_llm_provider": "anthropic",
            "api_base": f"{api_base}/anthropic",
            "model": f"anthropic/{model_name}",
        }
    #
    if api_protocol == "openai" and api_base:
        # "responses/" is what makes litellm bridge to the Responses API per model
        return {
            "custom_llm_provider": "openai",
            "api_base": f"{api_base}/openai/v1",
            "model": f"openai/responses/{model_name}",
        }
    #
    return {
        "custom_llm_provider": "azure",
        "model": model_name,
    }


def to_model(  # pylint: disable=R0913
        expanded_configuration_info,
):
    """ Mapper """
    configuration_data = expanded_configuration_info["data"]
    #
    if "ai_credentials" not in configuration_data:
        return None
    #
    credential_data = configuration_data["ai_credentials"]
    #
    if "configuration_uuid" not in credential_data:
        return None
    #
    credential_project = credential_data["configuration_project_id"]
    credential_uuid = credential_data["configuration_uuid"]
    #
    credential_values = {
        "litellm_credential_name": f"{credential_project}_{credential_uuid}",
    }
    #
    model_name = configuration_data["name"]
    configuration_uuid = expanded_configuration_info["uuid"]
    configuration_project = expanded_configuration_info["project_id"]
    #
    protocol_params = _protocol_params(
        configuration_data.get("api_protocol") or "azure", model_name, credential_data,
    )
    #
    return {
        "model_name": f"{configuration_project}_{model_name}",
        "litellm_params": {
            **credential_values,
            **protocol_params,
        },
        "model_info": {
            "centry_configuration_uuid": configuration_uuid,
            "base_model": model_name,
        },
    }
