"""Internal worker attestation for source availability, bound to one invocation."""
import base64
import hashlib
import hmac
import json
import time
from .v7.availability import registered_options


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def context_revision(value):
    # Search query arguments describe the current task; they are not an access
    # revision. Including them would discard durable context on every turn.
    return digest({'instructions':value.get('active_instructions',{}).get('revision'),
        'sources':[{k:v for k,v in row.items() if k!='arguments'} for row in value.get('retrieval_options',[])]})


def validate_context(context, tools):
    if not isinstance(context, dict) or set(context)-{'active_instructions','retrieval_options'}:
        raise ValueError('Invalid runtime-context envelope')
    instructions = context.get('active_instructions') or {'text':'', 'revision':'', 'total_chars':0, 'truncated':False}
    if (not isinstance(instructions, dict) or set(instructions) != {'text','revision','total_chars','truncated'} or
            not isinstance(instructions['text'], str) or len(instructions['text']) > 8050 or
            type(instructions['total_chars']) is not int or instructions['total_chars'] < 0 or
            type(instructions['truncated']) is not bool or not isinstance(instructions['revision'], str)):
        raise ValueError('Invalid active-instruction projection')
    return {'active_instructions': instructions,
            'retrieval_options': registered_options(context.get('retrieval_options', []), tools)}


def issue_context(context, tools, *, key, project_id, user_id, scope_id, invocation_id, now=None):
    from .service import encode_pin
    value = validate_context(context, tools)
    claims = {'kind':'auto_runtime_context_v1','project_id':project_id,'user_id':user_id,
        'scope_id':scope_id,'invocation_id':invocation_id,'context_digest':digest(value),
        'tools_digest':digest(tools),'expires_at':int(time.time() if now is None else now)+900}
    return encode_pin(claims, key)


def read_context(request, *, key, project_id, user_id, now=None):
    value = validate_context(request.get('runtime_context') or {}, request.get('tools') or [])
    token = request.get('runtime_context_token')
    if not token:
        # Instructions describe intent, never authority. Source availability must
        # come from the worker that instantiated the actual tools.
        if value['retrieval_options']:
            raise ValueError('Source availability requires internal runtime attestation')
        return value
    try:
        if not isinstance(token,str) or len(token)>4000:
            raise ValueError()
        body, signature = token.rsplit('.',1)
        if not hmac.compare_digest(signature,hmac.new(key.encode(),body.encode(),hashlib.sha256).hexdigest()):
            raise ValueError()
        claims=json.loads(base64.urlsafe_b64decode(body+'='*((-len(body))%4)))
        for name, expected in {'kind':'auto_runtime_context_v1','project_id':project_id,'user_id':user_id,
                'scope_id':request['scope_id'],'invocation_id':request['invocation_id'],
                'context_digest':digest(value),'tools_digest':digest(request.get('tools') or [])}.items():
            if claims.get(name)!=expected:
                raise ValueError()
        if claims['expires_at'] <= (time.time() if now is None else now):
            raise ValueError()
        return value
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError('Invalid or expired runtime-context attestation') from exc
