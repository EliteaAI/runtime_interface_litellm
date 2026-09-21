"""Worker-only RPC; no HTTP endpoint grants source-availability attestations."""
from pylon.core.tools import web
from tools import VaultClient
from ..routing.context import issue_context


class Method:
    @web.method()
    def sign_routing_context(self, project_id, user_id, context, tools, scope_id, invocation_id):
        key = VaultClient(project_id).get_secrets().get('project_llm_key')
        if not key:
            raise ValueError('Routing signing key unavailable')
        return issue_context(context, tools, key=key, project_id=project_id, user_id=user_id,
                             scope_id=scope_id, invocation_id=invocation_id)
