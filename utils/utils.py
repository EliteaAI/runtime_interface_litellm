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

""" Utility functions for runtime interface litellm """

import json

import requests  # pylint: disable=E0401


# Connection check timeout in seconds
CONNECTION_CHECK_TIMEOUT = 30


def extract_error_message(response) -> str:
    """Extract error message from API response"""
    try:
        error_json = response.json()
        error_msg = error_json.get('error', {}).get('message', '')
        if error_msg:
            return f" - {error_msg}"
    except Exception:  # pylint: disable=W0718
        pass
    return ""


def check_azure_openai_connection(data: dict) -> dict:
    """
    Test Azure OpenAI compatible API connection
    
    Args:
        data: Dictionary containing api_base, api_key, and api_version
        
    Returns:
        Dictionary with 'success' (bool) and 'message' (str)
    """
    try:
        api_base = data.get('api_base')
        api_key = data.get('api_key')
        api_version = data.get('api_version')

        # Validate required fields
        if not api_base:
            return {"success": False, "message": "api_base is required"}
        if not api_key:
            return {"success": False, "message": "api_key is required"}
        if not api_version:
            return {"success": False, "message": "api_version is required"}

        # Remove trailing slash if present
        api_base = api_base.rstrip('/')

        # Azure OpenAI models endpoint (lightweight check)
        models_url = f"{api_base}/openai/models?api-version={api_version}"

        # Prepare headers
        headers = {
            'Content-Type': 'application/json',
            'api-key': api_key
        }

        # Make request directly to Azure OpenAI
        response = requests.get(
            models_url,
            headers=headers,
            timeout=CONNECTION_CHECK_TIMEOUT
        )

        if response.status_code == 200:
            return {
                "success": True,
                "message": "Connection to Azure OpenAI successful"
            }
        elif response.status_code == 401 or response.status_code == 403:
            error_details = extract_error_message(response)
            return {
                "success": False,
                "message": f"Authentication failed: Invalid or expired api_key{error_details}"
            }
        elif response.status_code == 404:
            error_details = extract_error_message(response)
            return {
                "success": False,
                "message": f"Wrong api_base URL or wrong api_version{error_details}"
            }
        elif response.status_code == 400:
            error_details = extract_error_message(response)
            return {
                "success": False,
                "message": f"Bad request - likely invalid api_version '{api_version}'{error_details}"
            }
        else:
            error_details = extract_error_message(response)
            response_text = response.text[:200]
            if len(response.text) > 200:
                response_text += "..."
            return {
                "success": False,
                "message": f"Connection failed with status {response.status_code}: {response_text}{error_details}"
            }

    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timeout - check api_base URL"}
    except requests.exceptions.ConnectionError as error:
        return {"success": False, "message": f"Cannot connect to api_base '{data.get('api_base', '')}': {str(error)}"}
    except Exception as error:  # pylint: disable=W0718
        return {"success": False, "message": f"Error: {str(error)}"}


def check_openai_connection(data: dict) -> dict:
    """
    Test OpenAI API connection
    
    Args:
        data: Dictionary containing api_base and api_key
        
    Returns:
        Dictionary with 'success' (bool) and 'message' (str)
    """
    try:
        api_base = data.get('api_base')
        api_key = data.get('api_key')

        # Validate required fields
        if not api_base:
            return {"success": False, "message": "api_base is required"}
        if not api_key:
            return {"success": False, "message": "api_key is required"}

        # Remove trailing slash if present
        api_base = api_base.rstrip('/')

        # OpenAI models endpoint (lightweight check)
        models_url = f"{api_base}/models"

        # Prepare headers (OpenAI uses Bearer token)
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }

        # Make request to OpenAI API
        response = requests.get(
            models_url,
            headers=headers,
            timeout=CONNECTION_CHECK_TIMEOUT
        )

        if response.status_code == 200:
            return {
                "success": True,
                "message": "Connection to OpenAI successful"
            }
        elif response.status_code == 401 or response.status_code == 403:
            error_details = extract_error_message(response)
            return {
                "success": False,
                "message": f"Authentication failed: Invalid or expired api_key{error_details}"
            }
        elif response.status_code == 404:
            error_details = extract_error_message(response)
            return {
                "success": False,
                "message": f"Wrong api_base URL - endpoint not found{error_details}"
            }
        elif response.status_code == 400:
            error_details = extract_error_message(response)
            return {
                "success": False,
                "message": f"Bad request{error_details}"
            }
        else:
            error_details = extract_error_message(response)
            response_text = response.text[:200]
            if len(response.text) > 200:
                response_text += "..."
            return {
                "success": False,
                "message": f"Connection failed with status {response.status_code}: {response_text}{error_details}"
            }

    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timeout - check api_base URL"}
    except requests.exceptions.ConnectionError as error:
        return {"success": False, "message": f"Cannot connect to api_base '{data.get('api_base', '')}': {str(error)}"}
    except Exception as error:  # pylint: disable=W0718
        return {"success": False, "message": f"Error: {str(error)}"}


def check_ollama_connection(data: dict) -> dict:
    """
    Test Ollama API connection
    
    Args:
        data: Dictionary containing api_base
        
    Returns:
        Dictionary with 'success' (bool) and 'message' (str)
    """
    try:
        api_base = data.get('api_base')

        # Validate required fields
        if not api_base:
            return {"success": False, "message": "api_base is required"}

        # Remove trailing slash if present
        api_base = api_base.rstrip('/')

        # Ollama API tags endpoint (lists available models - lightweight check)
        tags_url = f"{api_base}/api/tags"

        # Make request to Ollama API (no authentication needed typically)
        response = requests.get(
            tags_url,
            timeout=CONNECTION_CHECK_TIMEOUT
        )

        if response.status_code == 200:
            return {
                "success": True,
                "message": "Connection to Ollama successful"
            }
        elif response.status_code == 404:
            return {
                "success": False,
                "message": "Wrong api_base URL - Ollama endpoint not found. Make sure Ollama is running and accessible."
            }
        elif response.status_code == 401 or response.status_code == 403:
            return {
                "success": False,
                "message": "Authentication required - check Ollama configuration"
            }
        else:
            response_text = response.text[:200]
            if len(response.text) > 200:
                response_text += "..."
            return {
                "success": False,
                "message": f"Connection failed with status {response.status_code}: {response_text}"
            }

    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timeout - check if Ollama is running at the specified api_base URL"}
    except requests.exceptions.ConnectionError as error:
        return {"success": False, "message": f"Cannot connect to Ollama at '{data.get('api_base', '')}': {str(error)}. Make sure Ollama is running."}
    except Exception as error:  # pylint: disable=W0718
        return {"success": False, "message": f"Error: {str(error)}"}


def check_vertex_ai_connection(data: dict) -> dict:
    """
    Test Vertex AI API connection using google-cloud-aiplatform library
    
    Args:
        data: Dictionary containing vertex_project, vertex_location, and vertex_credentials (JSON string)
        
    Returns:
        Dictionary with 'success' (bool) and 'message' (str)
    """
    try:
        vertex_project = data.get('vertex_project')
        vertex_location = data.get('vertex_location')
        vertex_credentials = data.get('vertex_credentials')

        # Validate required fields
        if not vertex_project:
            return {"success": False, "message": "vertex_project is required"}
        if not vertex_location:
            return {"success": False, "message": "vertex_location is required"}
        if not vertex_credentials:
            return {"success": False, "message": "vertex_credentials is required"}

        # Try using google-cloud-aiplatform library
        try:
            from google.cloud import aiplatform
            from google.oauth2 import service_account
        except ImportError:
            return {
                "success": False,
                "message": "google-cloud-aiplatform library not available. Install with: pip install google-cloud-aiplatform"
            }
        
        # Parse credentials JSON
        try:
            credentials_dict = json.loads(vertex_credentials)
        except json.JSONDecodeError as e:
            return {"success": False, "message": f"Invalid JSON in vertex_credentials: {str(e)}"}
        
        # Create credentials object
        try:
            credentials = service_account.Credentials.from_service_account_info(
                credentials_dict,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
        except Exception as e:  # pylint: disable=W0718
            return {"success": False, "message": f"Invalid service account credentials: {str(e)}"}
        
        # Initialize Vertex AI client
        try:
            aiplatform.init(
                project=vertex_project,
                location=vertex_location,
                credentials=credentials
            )
        except Exception as e:  # pylint: disable=W0718
            return {"success": False, "message": f"Failed to initialize Vertex AI client: {str(e)}"}
        
        # Try to list models (lightweight operation)
        try:
            # This will test authentication and permissions
            _ = aiplatform.Model.list(
                filter=None,
                order_by=None,
                project=vertex_project,
                location=vertex_location,
                credentials=credentials
            )
            # If we get here, connection works
            return {
                "success": True,
                "message": "Connection to Vertex AI successful"
            }
        except Exception as e:  # pylint: disable=W0718
            error_msg = str(e)
            
            # Check if this is a billing error
            if 'billing' in error_msg.lower():
                return {
                    "success": True,
                    "message": "Vertex AI credentials validated successfully (billing not enabled, but authentication works)"
                }
            
            # Check for authentication errors
            if '401' in error_msg or 'unauthenticated' in error_msg.lower():
                return {
                    "success": False,
                    "message": f"Authentication failed: Invalid vertex_credentials - {error_msg}"
                }
            
            # Check for permission errors
            if '403' in error_msg or 'permission' in error_msg.lower():
                return {
                    "success": False,
                    "message": f"Permission denied: Check IAM permissions for the service account - {error_msg}"
                }
            
            # Check for not found errors
            if '404' in error_msg or 'not found' in error_msg.lower():
                return {
                    "success": False,
                    "message": (
                        f"Project or location not found: Check vertex_project '{vertex_project}' "
                        f"and vertex_location '{vertex_location}' - {error_msg}"
                    )
                }
            
            # Generic error
            return {
                "success": False,
                "message": f"Vertex AI API error: {error_msg}"
            }

    except Exception as error:  # pylint: disable=W0718
        return {"success": False, "message": f"Error: {str(error)}"}
