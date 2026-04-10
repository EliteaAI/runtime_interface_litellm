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

from tools import this  # pylint: disable=E0401


def to_capabilities(
        integration_settings,
        model_name,
):
    """ Mapper """
    model_info = None
    #
    for item in integration_settings["settings"]["models"]:
        if item["name"] == model_name:
            model_info = item
            break
    #
    if model_info is None:
        raise RuntimeError(f"No model info found: {model_name}")
    #
    result = []
    #
    for key, value in model_info["capabilities"].items():
        if value:
            result.append(key.lower())
    #
    return result


def to_credential(  # pylint: disable=R0913
        _integration,
        _integration_name,
        integration_data,
        integration_uid,
        _project_id,
        target_project,
        vault_client,
        standalone=True
):
    """ Mapper """
    if integration_data["settings"]["api_base"] == this.module.get_base_url():
        return None
    #
    api_base = integration_data["settings"]["api_base"]
    #
    credential_values = {
        "api_base": api_base,
    }
    #
    api_key = vault_client.unsecret(integration_data["settings"]["api_token"])
    #
    if api_key != "-":
        credential_values["api_key"] = api_key
    #
    api_version = integration_data["settings"]["api_version"]
    #
    if api_version != "-":
        credential_values["api_version"] = api_version
    #
    if standalone:
        result = {
            "credential_name": f"{target_project}_{integration_uid}",
            "credential_values": credential_values,
            "credential_info": {
                "custom_llm_provider": "Azure",
            },
        }
    else:
        result = credential_values
    #
    return result


def to_test(  # pylint: disable=R0913
        integration,
        integration_name,
        integration_data,
        integration_uid,
        project_id,
        target_project,
        vault_client,
):
    """ Mapper """
    if integration_data["settings"]["api_base"] == this.module.get_base_url():
        return None
    #
    credential_values = to_credential(
        integration,
        integration_name,
        integration_data,
        integration_uid,
        project_id,
        target_project,
        vault_client,
        standalone=False,
    )
    #
    model_values = {}
    #
    if integration_data["settings"]["models"]:
        model_data = integration_data["settings"]["models"][0]  # FIXME: use other model?
        model_name = model_data["name"]
        #
        model_values["model"] = model_name
    #
    return {
        "litellm_params": {
            "custom_llm_provider": "azure",
            **credential_values,
            **model_values,
        },
    }


def to_models(  # pylint: disable=R0913
        integration,
        integration_name,
        integration_data,
        integration_uid,
        project_id,
        target_project,
        vault_client,
        credential_name=None,
):
    """ Mapper """
    result = []
    #
    if integration_data["settings"]["api_base"] == this.module.get_base_url():
        return result
    #
    if credential_name is None:
        credential_values = to_credential(
            integration,
            integration_name,
            integration_data,
            integration_uid,
            project_id,
            target_project,
            vault_client,
            standalone=False,
        )
    else:
        credential_values = {
            "litellm_credential_name": credential_name,
        }
    #
    for model_data in integration_data["settings"]["models"]:
        model_name = model_data["name"]
        #
        result.append({
            "model_name": f"{target_project}_{model_name}",
            "litellm_params": {
                "custom_llm_provider": "azure",
                **credential_values,
                "model": model_name,
            },
            "model_info": {
                "centry_integration_uid": integration_uid,
            },
        })
    #
    return result
