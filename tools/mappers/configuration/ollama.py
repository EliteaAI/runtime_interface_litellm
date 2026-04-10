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
    if standalone:
        target_project = configuration_info["project_id"]
        target_uuid = configuration_info["uuid"]
        #
        result = {
            "credential_name": f"{target_project}_{target_uuid}",
            "credential_values": credential_values,
            "credential_info": {
                "custom_llm_provider": "Ollama",
            },
        }
    else:
        result = credential_values
    #
    return result


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
    return {
        "model_name": f"{configuration_project}_{model_name}",
        "litellm_params": {
            "custom_llm_provider": "ollama",
            **credential_values,
            "model": model_name,
        },
        "model_info": {
            "centry_configuration_uuid": configuration_uuid,
        },
    }
