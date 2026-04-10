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

from pydantic import BaseModel, ConfigDict  # pylint: disable=E0401

from ....utils.utils import check_ollama_connection


class OllamaCredential(BaseModel):
    """ Model """
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Ollama",
                "section": "ai_credentials",
                # "icon_url": "ollama.svg",
                "type": "ollama",
            }
        }
    )

    api_base: str

    @staticmethod
    def check_connection(data: dict) -> dict:
        """ Test Ollama API connection """
        return check_ollama_connection(data)
