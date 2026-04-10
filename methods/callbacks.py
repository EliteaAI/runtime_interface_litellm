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

""" Method """

import json
import importlib

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import VaultClient  # pylint: disable=E0401


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    #
    # AI
    #

    @web.method()
    def ai_check_settings(  # pylint: disable=R0912,R0913,R0914
            self, integration_name, settings,
        ):
        """ Check integration settings/test connection """
        integration = {
            "mode": None,
            "project_id": None,
            "integration_name": integration_name,
            "integration_data": {
                "uid": "00000000-0000-0000-0000-000000000000",
                "settings": settings,
            },
        }
        integration_test = self.integration_to_test(integration)
        #
        if integration_test is None:
            return "Failed to test"
        #
        result = self.service_node.call.litellm_api_call(
            "health_test_connection",
            **integration_test,
        )
        #
        if result["status"] == "success":
            return True
        #
        try:
            return result["result"]["error"]
        except Exception as error:  # pylint: disable=W0718
            log.error("Error during settings check: %s", error)
            return str(error)

    @web.method()
    def ai_get_models(  # pylint: disable=R0913
            self, integration_name, settings,
        ):
        """ Get model list """
        _ = integration_name, settings
        #
        return []

    @web.method()
    def ai_count_tokens(  # pylint: disable=R0913
            self, integration_name, settings, data,
        ):
        """ Count input/output/data tokens """
        _ = integration_name
        #
        try:
            project_id = settings.integration.project_id
        except AttributeError:
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        model_name = f'{project_id}_{settings.merged_settings["model_name"]}'
        #
        api_args = {
            "model": model_name,
        }
        #
        if isinstance(data, list):
            data = json.loads(json.dumps(data))
            api_args["messages"] = data
        else:
            api_args["prompt"] = data
        #
        result = self.service_node.call.litellm_api_call(
            "utils_token_counter",
            **api_args,
        )
        #
        return result["total_tokens"]

    #
    # LLM
    #

    @web.method()
    def llm_invoke(  # pylint: disable=R0913
            self, integration_name, settings, text,
        ):
        """ Call model """
        _ = integration_name
        #
        try:
            project_id = settings.integration.project_id
        except AttributeError:
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        vault_client = VaultClient(project_id)
        vault_secrets = vault_client.get_all_secrets()
        #
        if "project_llm_key" not in vault_secrets:
            raise RuntimeError("No LLM key found")
        #
        project_llm_key = vault_secrets.get("project_llm_key")
        #
        model_name = f'{project_id}_{settings.merged_settings["model_name"]}'
        #
        model_parameters = {}
        #
        for param in ["max_tokens", "temperature", "top_p"]:
            if param in settings.merged_settings:
                model_parameters[param] = settings.merged_settings[param]
        #
        result = self.service_node.call.litellm_openai_invoke(
            target_class="langchain_openai.llms.base.OpenAI",
            target_args=None,
            target_kwargs={
                "api_key": project_llm_key,
                "model": model_name,
                **model_parameters,
            },
            client_attr=None,
            method_name="invoke",
            method_args=None,
            method_kwargs={
                "input": text,
            },
            langchain_input=False,
            pydantic_cleanup=False,
        )
        #
        return result

    @web.method()
    def llm_stream(  # pylint: disable=R0913
            self, integration_name, settings, text,
        ):
        """ Stream model """
        _ = integration_name
        #
        try:
            project_id = settings.integration.project_id
        except AttributeError:
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        vault_client = VaultClient(project_id)
        vault_secrets = vault_client.get_all_secrets()
        #
        if "project_llm_key" not in vault_secrets:
            raise RuntimeError("No LLM key found")
        #
        project_llm_key = vault_secrets.get("project_llm_key")
        #
        model_name = f'{project_id}_{settings.merged_settings["model_name"]}'
        #
        model_parameters = {}
        #
        for param in ["max_tokens", "temperature", "top_p"]:
            if param in settings.merged_settings:
                model_parameters[param] = settings.merged_settings[param]
        #
        stream_id = self.stream_node.add_stream()
        #
        try:
            task_id = self.task_node.start_task(
                pool="litellm",
                name="litellm_openai_stream",
                kwargs={
                    "stream_id": stream_id,
                    "target_class": "langchain_openai.llms.base.OpenAI",
                    "target_args": None,
                    "target_kwargs": {
                        "streaming": True,
                        "api_key": project_llm_key,
                        "model": model_name,
                        **model_parameters,
                    },
                    "client_attr": None,
                    "method_name": "stream",
                    "method_args": None,
                    "method_kwargs": {
                        "input": text,
                    },
                    "langchain_input": False,
                    "pydantic_cleanup": False,
                },
            )
            #
            if task_id is None:
                raise RuntimeError("Failed to start LLM task")
        except:  # pylint: disable=W0702
            self.stream_node.remove_stream(stream_id)
            raise
        #
        consumer = self.stream_node.get_consumer(
            stream_id,
            timeout=self.descriptor.config.get("proxy_consumer_timeout", 600),
        )
        #
        yield from consumer

    #
    # ChatModel
    #

    @web.method()
    def chat_model_invoke(  # pylint: disable=R0913
            self, integration_name, settings, messages,
        ):
        """ Call model """
        _ = integration_name
        #
        try:
            project_id = settings.integration.project_id
        except AttributeError:
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        vault_client = VaultClient(project_id)
        vault_secrets = vault_client.get_all_secrets()
        #
        if "project_llm_key" not in vault_secrets:
            raise RuntimeError("No LLM key found")
        #
        project_llm_key = vault_secrets.get("project_llm_key")
        #
        model_name = f'{project_id}_{settings.merged_settings["model_name"]}'
        #
        model_parameters = {}
        #
        for param in ["max_tokens", "temperature", "top_p"]:
            if param in settings.merged_settings:
                model_parameters[param] = settings.merged_settings[param]
        #
        result = self.service_node.call.litellm_openai_invoke(
            target_class="langchain_openai.chat_models.base.ChatOpenAI",
            target_args=None,
            target_kwargs={
                "api_key": project_llm_key,
                "model": model_name,
                **model_parameters,
            },
            client_attr=None,
            method_name="invoke",
            method_args=None,
            method_kwargs={
                "input": json.loads(json.dumps(messages)),
            },
            langchain_input=True,
            pydantic_cleanup=True,
        )
        #
        return result

    @web.method()
    def chat_model_stream(  # pylint: disable=R0913
            self, integration_name, settings, messages,
        ):
        """ Stream model """
        _ = integration_name
        #
        try:
            project_id = settings.integration.project_id
        except AttributeError:
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        vault_client = VaultClient(project_id)
        vault_secrets = vault_client.get_all_secrets()
        #
        if "project_llm_key" not in vault_secrets:
            raise RuntimeError("No LLM key found")
        #
        project_llm_key = vault_secrets.get("project_llm_key")
        #
        model_name = f'{project_id}_{settings.merged_settings["model_name"]}'
        #
        model_parameters = {}
        #
        for param in ["max_tokens", "temperature", "top_p"]:
            if param in settings.merged_settings:
                model_parameters[param] = settings.merged_settings[param]
        #
        stream_id = self.stream_node.add_stream()
        #
        try:
            task_id = self.task_node.start_task(
                pool="litellm",
                name="litellm_openai_stream",
                kwargs={
                    "stream_id": stream_id,
                    "target_class": "langchain_openai.chat_models.base.ChatOpenAI",
                    "target_args": None,
                    "target_kwargs": {
                        "streaming": True,
                        "api_key": project_llm_key,
                        "model": model_name,
                        **model_parameters,
                    },
                    "client_attr": None,
                    "method_name": "stream",
                    "method_args": None,
                    "method_kwargs": {
                        "input": json.loads(json.dumps(messages)),
                    },
                    "langchain_input": True,
                    "pydantic_cleanup": True,
                },
            )
            #
            if task_id is None:
                raise RuntimeError("Failed to start LLM task")
        except:  # pylint: disable=W0702
            self.stream_node.remove_stream(stream_id)
            raise
        #
        consumer = self.stream_node.get_consumer(
            stream_id,
            timeout=self.descriptor.config.get("proxy_consumer_timeout", 600),
        )
        #
        yield from consumer

    #
    # Embed
    #

    @web.method()
    def embed_documents(  # pylint: disable=R0913
            self, integration_name, settings, texts,
        ):
        """ Make embeddings """
        _ = integration_name
        #
        if "project_id" in settings:
            project_id = settings["project_id"]
        elif "project_id" in settings["integration_data"]:
            project_id = settings["integration_data"]["project_id"]
        elif "project_id" in settings["integration_data"]["settings"]:
            project_id = settings["integration_data"]["settings"]["project_id"]
        else:
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        vault_client = VaultClient(project_id)
        vault_secrets = vault_client.get_all_secrets()
        #
        if "project_llm_key" not in vault_secrets:
            raise RuntimeError("No LLM key found")
        #
        project_llm_key = vault_secrets.get("project_llm_key")
        #
        model_name = f'{project_id}_{settings["model_name"]}'
        #
        result = self.service_node.call.litellm_openai_invoke(
            target_class="langchain_openai.embeddings.base.OpenAIEmbeddings",
            target_args=None,
            target_kwargs={
                "api_key": project_llm_key,
                "model": model_name,
            },
            client_attr=None,
            method_name="embed_documents",
            method_args=None,
            method_kwargs={
                "texts": texts,
            },
            langchain_input=False,
            pydantic_cleanup=False,
        )
        #
        return result

    @web.method()
    def embed_query(  # pylint: disable=R0913
            self, integration_name, settings, text,
        ):
        """ Make embedding """
        _ = integration_name
        #
        if "project_id" in settings:
            project_id = settings["project_id"]
        elif "project_id" in settings["integration_data"]:
            project_id = settings["integration_data"]["project_id"]
        elif "project_id" in settings["integration_data"]["settings"]:
            project_id = settings["integration_data"]["settings"]["project_id"]
        else:
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        vault_client = VaultClient(project_id)
        vault_secrets = vault_client.get_all_secrets()
        #
        if "project_llm_key" not in vault_secrets:
            raise RuntimeError("No LLM key found")
        #
        project_llm_key = vault_secrets.get("project_llm_key")
        #
        model_name = f'{project_id}_{settings["model_name"]}'
        #
        result = self.service_node.call.litellm_openai_invoke(
            target_class="langchain_openai.embeddings.base.OpenAIEmbeddings",
            target_args=None,
            target_kwargs={
                "api_key": project_llm_key,
                "model": model_name,
            },
            client_attr=None,
            method_name="embed_query",
            method_args=None,
            method_kwargs={
                "text": text,
            },
            langchain_input=False,
            pydantic_cleanup=False,
        )
        #
        return result

    #
    # Indexer
    #

    @web.method()
    def indexer_config(  # pylint: disable=R0912,R0913,R0914
            self, integration_name, settings, model,
        ):
        """ Make indexer config """
        try:
            mapper = importlib.import_module(
                f"plugins.runtime_interface_litellm.tools.mappers.integration.{integration_name}"
            )
            #
            model_capabilities = mapper.to_capabilities(
                settings,
                model,
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to map")
            raise
        #
        try:
            project_id = settings["project_id"]
        except (AttributeError, KeyError):
            project_id = None
        #
        if project_id is None:
            project_id = self.get_public_project_id()
        #
        project_token = self.get_system_user_token(project_id)
        base_url = self.get_base_url()
        #
        if "embeddings" in model_capabilities:
            return {
                "embedding_model": "langchain_openai.embeddings.base.OpenAIEmbeddings",
                "embedding_model_params": {
                    "model": model,
                    #
                    "base_url": base_url,
                    "api_key": project_token,
                },
            }
        #
        model_parameters = {}
        #
        for param in ["max_tokens", "temperature", "top_p"]:
            if param in settings["settings"]:
                model_parameters[param] = settings["settings"][param]
        #
        if "chat_completion" not in model_capabilities:
            return {
                "ai_model": "langchain_openai.llms.base.OpenAI",
                "ai_model_params": {
                    "model": model,
                    #
                    **model_parameters,
                    #
                    "base_url": base_url,
                    "api_key": project_token,
                },
            }
        #
        return {
            "ai_model": "langchain_openai.chat_models.base.ChatOpenAI",
            "ai_model_params": {
                "model": model,
                #
                **model_parameters,
                #
                "base_url": base_url,
                "api_key": project_token,
            },
        }
