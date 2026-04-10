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

""" Route """

import flask  # pylint: disable=E0401

from pylon.core.tools import log  # pylint: disable=E0611,E0401,W0611
from pylon.core.tools import web  # pylint: disable=E0611,E0401,W0611

from tools import this, auth  # pylint: disable=E0401


class Route:  # pylint: disable=E1101,R0903
    """ Route """

    @web.route(
        "/",
        defaults={"url": "/"},
        methods=["OPTIONS", "HEAD", "GET", "POST", "PUT", "PATCH", "DELETE"],
        endpoint="litellm_route_http",
    )
    @web.route(
        "/<path:url>",
        methods=["OPTIONS", "HEAD", "GET", "POST", "PUT", "PATCH", "DELETE"],
        endpoint="litellm_route_http__url",
    )
    def litellm_route_http(self, url):  # pylint: disable=R
        """ Handler """
        #
        # Target
        #
        proxy_target = {
            "url": url,
            "endpoint": f'/{url.lstrip("/")}',
            #
            "method": flask.request.method,
            "params": flask.request.args,
            "headers": flask.request.headers,
            #
            **self.preprocess_data(flask.request),
        }
        #
        # Auth
        #
        proxy_auth = {
            "type": flask.g.auth.type,
            "user": auth.current_user(),
        }
        #
        # Check auth
        #
        auth_check_response = self.check_access(proxy_target, proxy_auth)
        #
        if auth_check_response is not None:
            return auth_check_response
        #
        # Prepare request
        #
        prepare_request_response = self.prepare_request(proxy_target, proxy_auth)
        #
        if prepare_request_response is not None:
            return prepare_request_response
        #
        # Perform request
        #
        response_stream_id = self.stream_node.add_stream()
        #
        try:
            request_stream_id = self.service_node.call.litellm_request_start(response_stream_id)
        except:  # pylint: disable=W0702
            self.stream_node.remove_stream(response_stream_id)
            #
            log.exception("Proxy exception")
            return "Error", 500
        #
        emitter = None
        #
        try:
            emitter = self.stream_node.get_emitter(request_stream_id)
            consumer = self.stream_node.get_consumer(
                response_stream_id,
                timeout=this.descriptor.config.get("proxy_consumer_timeout", 600),
            )
            iterator = iter(consumer)
            #
            emitter.chunk(proxy_target)
            #
            response = next(iterator)
            #
            self.prepare_response(proxy_target, proxy_auth, response)
            #
            return flask.Response(
                flask.stream_with_context(iterator),
                status=response["status_code"],
                headers=response["headers"],
                direct_passthrough=True,
            )
        #
        except:  # pylint: disable=W0702
            log.exception("Proxy exception")
            return "Error", 500
        #
        finally:
            if emitter is not None:
                emitter.end()
