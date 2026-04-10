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
    credential_values = {}
    #
    aws_access_key = vault_client.unsecret(integration_data["settings"]["aws_access_key_id"])
    #
    if aws_access_key != "-":
        credential_values["aws_access_key_id"] = aws_access_key
    #
    aws_secret_key = vault_client.unsecret(integration_data["settings"]["aws_secret_access_key"])
    #
    if aws_secret_key != "-":
        credential_values["aws_secret_access_key"] = aws_secret_key
    #
    aws_region_name = integration_data["settings"]["region_name"]
    #
    if aws_region_name != "-":
        credential_values["aws_region_name"] = aws_region_name
    #
    if standalone:
        result = {
            "credential_name": f"{target_project}_{integration_uid}",
            "credential_values": credential_values,
            "credential_info": {
                "custom_llm_provider": "Bedrock",
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
            "custom_llm_provider": "bedrock",
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
                "custom_llm_provider": "bedrock",
                **credential_values,
                "model": model_name,
            },
            "model_info": {
                "centry_integration_uid": integration_uid,
            },
        })
    #
    return result
