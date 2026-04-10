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

import threading

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

import arbiter  # pylint: disable=E0401

from tools import context, this, auth, worker_client  # pylint: disable=E0401


class Method:  # pylint: disable=E1101,R0903,W0201
    """
        Method Resource

        self is pointing to current Module instance

        web.method decorator takes zero or one argument: method name
        Note: web.method decorator must be the last decorator (at top)
    """

    @web.init()
    def init(self):
        """ Init """
        self.runtime_cache = {}
        #
        # 'Public' route
        #
        url_prefix = self.descriptor.config.get("url_prefix", None)
        #
        if url_prefix is None:
            url_prefix = this.module_name
        #
        url_prefix = url_prefix.strip("/")
        self.public_rule = f"{context.url_prefix}/{url_prefix}/.*"
        #
        auth.add_public_rule({
            "uri": self.public_rule,
        })
        #
        # Stream/Service nodes
        #
        self.stream_node = arbiter.StreamNode(  # pylint: disable=I1101
            worker_client.event_node,
            id_prefix="litellm:",
        )
        self.service_node = arbiter.ServiceNode(  # pylint: disable=I1101
            worker_client.event_node,
            id_prefix="litellm:",
            default_timeout=30,
        )
        #
        self.task_node = arbiter.TaskNode(  # pylint: disable=I1101
            worker_client.event_node,
            pool="litellm",
            task_limit=0,
            ident_prefix="litellm:",
            multiprocessing_context="threading",
        )
        #
        self.stream_node.start()
        self.service_node.start()
        self.task_node.start()
        #
        # Admin
        #
        self.litellm_mode = self.descriptor.config.get("litellm_mode", "built-in")
        #
        try:
            if self.litellm_mode == "built-in":
                this.for_module("admin").module.register_admin_task(
                    "sync_llm_entities", self.sync_llm_entities
                )
                this.for_module("admin").module.register_admin_task(
                    "delete_llm_venv", self.delete_llm_venv
                )
                this.for_module("admin").module.register_admin_task(
                    "delete_llm_entities", self.delete_llm_entities
                )
            #
            this.for_module("admin").module.register_admin_task(
                "import_llm_models", self.import_llm_models
            )
            this.for_module("admin").module.register_admin_task(
                "seed_llm_keys", self.seed_llm_keys
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to register admin tasks")
        #
        # Register configurations
        #
        self.configurations_lock = threading.Lock()
        self.configurations_blocklist = set()
        #
        # pylint: disable=C0415
        try:
            from ..models.pd.configuration.open_ai import OpenAICredential
            from ..models.pd.configuration.azure_open_ai import AzureOpenAICredential
            from ..models.pd.configuration.ai_dial import AIDIALCredential
            from ..models.pd.configuration.amazon_bedrock import AmazonBedrockCredential
            from ..models.pd.configuration.vertex_ai import VertexAICredential
            from ..models.pd.configuration.ollama import OllamaCredential
            #
            for name, model in [
                    ("open_ai", OpenAICredential),
                    ("azure_open_ai", AzureOpenAICredential),
                    ("ai_dial", AIDIALCredential),
                    ("amazon_bedrock", AmazonBedrockCredential),
                    ("vertex_ai", VertexAICredential),
                    ("ollama", OllamaCredential),
            ]:
                context.rpc_manager.timeout(5).configurations_register(
                    type_name=name,
                    section="ai_credentials",
                    model=model,
                )
        except:  # pylint: disable=W0702
            log.exception("Failed to register configurations")
        #
        # Register LLM interface
        #
        try:
            worker_client.register_llm_interface(
                ai_check_settings_callback=self.ai_check_settings,
                ai_get_models_callback=self.ai_get_models,
                ai_count_tokens_callback=self.ai_count_tokens,
                #
                llm_invoke_callback=self.llm_invoke,
                llm_stream_callback=self.llm_stream,
                #
                chat_model_invoke_callback=self.chat_model_invoke,
                chat_model_stream_callback=self.chat_model_stream,
                #
                embed_documents_callback=self.embed_documents,
                embed_query_callback=self.embed_query,
                #
                indexer_config_callback=self.indexer_config,
                #
                supported_integrations=[
                    "ai_dial",
                    "amazon_bedrock",
                    "ollama",
                    "open_ai",
                    "open_ai_azure",
                    "vertex_ai",
                ],
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to register LLM interface")

    @web.deinit()
    def deinit(self):
        """ De-init """
        try:
            if self.litellm_mode == "built-in":
                this.for_module("admin").module.unregister_admin_task(
                    "delete_llm_entities", self.delete_llm_entities
                )
                this.for_module("admin").module.unregister_admin_task(
                    "delete_llm_venv", self.delete_llm_venv
                )
                this.for_module("admin").module.unregister_admin_task(
                    "sync_llm_entities", self.sync_llm_entities
                )
            #
            this.for_module("admin").module.unregister_admin_task(
                "import_llm_models", self.import_llm_models
            )
            this.for_module("admin").module.unregister_admin_task(
                "seed_llm_keys", self.seed_llm_keys
            )
        except:  # pylint: disable=W0702
            log.exception("Failed to unregister admin tasks")
        #
        self.task_node.stop()
        self.service_node.stop()
        self.stream_node.stop()
        #
        auth.remove_public_rule({
            "uri": self.public_rule,
        })
