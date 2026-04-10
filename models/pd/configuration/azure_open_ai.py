#!/usr/bin/python3
# coding=utf-8
# pylint: disable=R0903

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

""" Models """

from typing import Optional

from pydantic import BaseModel, SecretStr, ConfigDict  # pylint: disable=E0401

from ....utils.utils import check_azure_openai_connection  # pylint: disable=E0401


class AzureOpenAICredential(BaseModel):
    """ Model """
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Azure OpenAI",
                "section": "ai_credentials",
                "type": "azure_open_ai",
            }
        }
    )

    api_base: str
    api_key: Optional[SecretStr] = None
    api_version: Optional[str] = None

    @staticmethod
    def check_connection(data: dict) -> dict:
        """ Test Azure OpenAI connection directly """
        return check_azure_openai_connection(data)
