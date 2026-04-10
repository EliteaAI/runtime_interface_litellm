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

import boto3  # pylint: disable=E0401
from botocore.exceptions import ClientError, BotoCoreError  # pylint: disable=E0401
from pydantic import BaseModel, SecretStr, ConfigDict  # pylint: disable=E0401


# Connection check timeout in seconds
CONNECTION_CHECK_TIMEOUT = 30


class AmazonBedrockCredential(BaseModel):
    """ Model """
    model_config = ConfigDict(
        json_schema_extra={
            "metadata": {
                "label": "Amazon Bedrock",
                "section": "ai_credentials",
                # "icon_url": "amazon-bedrock.svg",
                "type": "amazon_bedrock",
            }
        }
    )

    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[SecretStr] = None
    aws_region_name: Optional[str] = None

    @staticmethod
    def _extract_aws_error_message(error) -> str:
        """Extract error message from AWS Bedrock error"""
        try:
            if hasattr(error, 'response') and 'Error' in error.response:
                error_msg = error.response['Error'].get('Message', '')
                if error_msg:
                    return f" - {error_msg}"
        except Exception:  # pylint: disable=W0718
            pass
        return ""

    @staticmethod
    def check_connection(data: dict) -> dict:
        """ Test Amazon Bedrock connection using ListFoundationModels API """
        try:
            aws_access_key_id = data.get('aws_access_key_id')
            aws_secret_access_key = data.get('aws_secret_access_key')
            aws_region_name = data.get('aws_region_name')

            # Validate required fields
            if not aws_access_key_id:
                return {"success": False, "message": "aws_access_key_id is required"}
            if not aws_secret_access_key:
                return {"success": False, "message": "aws_secret_access_key is required"}
            if not aws_region_name:
                return {"success": False, "message": "aws_region_name is required"}

            # Create Bedrock client
            config = boto3.session.Config(
                connect_timeout=CONNECTION_CHECK_TIMEOUT,
                read_timeout=CONNECTION_CHECK_TIMEOUT
            )
            
            bedrock_client = boto3.client(
                'bedrock',
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                region_name=aws_region_name,
                config=config
            )

            # Make request to ListFoundationModels (lightweight check)
            bedrock_client.list_foundation_models()

            return {
                "success": True,
                "message": "Connection to Amazon Bedrock successful"
            }

        except ClientError as error:
            error_code = error.response.get('Error', {}).get('Code', '')
            error_details = AmazonBedrockCredential._extract_aws_error_message(error)
            
            if error_code == 'UnrecognizedClientException':
                return {
                    "success": False,
                    "message": f"Authentication failed: Invalid aws_access_key_id or aws_secret_access_key{error_details}"
                }
            elif error_code == 'InvalidSignatureException':
                return {
                    "success": False,
                    "message": f"Authentication failed: Invalid aws_secret_access_key{error_details}"
                }
            elif error_code == 'AccessDeniedException':
                return {
                    "success": False,
                    "message": f"Access denied - check IAM permissions{error_details}"
                }
            elif error_code == 'InvalidClientTokenId':
                return {
                    "success": False,
                    "message": f"Invalid aws_access_key_id{error_details}"
                }
            else:
                return {
                    "success": False,
                    "message": f"AWS error ({error_code}){error_details}"
                }

        except BotoCoreError as error:
            error_msg = str(error)
            if 'Could not connect' in error_msg or 'EndpointConnectionError' in error_msg:
                return {
                    "success": False,
                    "message": f"Connection error - check aws_region_name '{aws_region_name}': {error_msg}"
                }
            return {
                "success": False,
                "message": f"Connection error: {error_msg}"
            }

        except Exception as error:  # pylint: disable=W0718
            return {"success": False, "message": f"Error: {str(error)}"}
