"""Signed, caller-owned V7 state carried by existing assistant checkpoints.

The bounded process cache accelerates the exact current checkpoint. A restart,
branch, edit, expiry or eviction restores signed durable state with cold cache
estimates, as required by V7. It is not a second persistence or billing store.
"""
import copy
import json
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from .v7.state import Session, message_digest, observe_cache
from .v7.retrieval import digest

_LOCK = threading.RLock()
_SESSIONS = OrderedDict()
MAX_SESSIONS = 32
MAX_CHECKPOINT_BYTES = 600_000


@contextmanager
def session_for(scope_key, token, checkpoint):
    fingerprint = digest(token)
    with _LOCK:
        entry = _SESSIONS.get(scope_key)
        if entry is None or entry['fingerprint'] != fingerprint or time.monotonic()-entry['used'] > 180:
            session = Session.restore(checkpoint) if checkpoint else Session(scope_key)
            entry = {'fingerprint': fingerprint, 'session': session, 'lock': threading.RLock(), 'used': time.monotonic()}
            _SESSIONS[scope_key] = entry
        _SESSIONS.move_to_end(scope_key)
        while len(_SESSIONS) > MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
    with entry['lock']:
        # Another branch may have consumed this checkpoint while we waited.
        # Restore that branch's signed input, never borrow its successor state.
        if entry['fingerprint'] != fingerprint:
            entry = {'fingerprint': fingerprint, 'session': Session.restore(checkpoint) if checkpoint else Session(scope_key),
                     'lock': entry['lock'], 'used': time.monotonic()}
            with _LOCK:
                _SESSIONS[scope_key] = entry
        try:
            yield entry
        except Exception:
            entry['fingerprint'] = None  # Failed mutations are never reused.
            raise
        finally:
            entry['used'] = time.monotonic()


def publish(entry, token):
    entry['fingerprint'] = digest(token)


def apply_observation(session, state, observation, messages, gateway, catalog, tools, policy_revision):
    """Only a receipt for the exact signed preceding input can update V7 state.

    Provider usage from the SDK is advisory. It cannot grant access, change
    qualification, or reduce the conservative economic upper bound.
    """
    if not state or not observation:
        return
    count = state['input_count']
    if count >= len(messages) or digest(messages[:count]) != state['input_digest']:
        return  # Edited/compacted history is invalidated by Session.prepare.
    response_index = observation.get('message_index', count)
    if type(response_index) is not int or not count <= response_index < len(messages)-1 or any(m.get('role') == 'user' for m in messages[count:response_index]):
        return
    response = messages[response_index]
    if response.get('role') != 'assistant' or observation.get('message_digest') != message_digest(response):
        return
    decision = state['decision']
    result = {'message': response, 'finish_reason': observation.get('finish_reason'),
              'usage': observation.get('usage') or {}, 'returned_model': observation.get('returned_model')}
    session.record_pending(messages[:count], decision, result, policy_revision, answer_index=response_index)
    session.finish_pending(decision.get('pending_intent_id'), result)
    vid = decision['selection']['variant']
    completed_at = observation.get('completed_at')
    if type(completed_at) in (int, float) and 0 <= time.time() - completed_at <= 180:
        # Rebuilding/Continue/restart must not refresh old provider evidence.
        # Missing, future or expired times can inform intent, never warm cache.
        observe_cache(session, gateway, vid, catalog['variants'][vid], messages[:response_index], tools,
                      decision['budget']['completion_cap'], result, now=completed_at)


def state_value(session, decision, messages, *, scope_id, project_id, user_id, gate_revision, policy_revision):
    compact_decision = {key: copy.deepcopy(decision[key]) for key in
                        ('action', 'selection', 'descriptor', 'budget', 'pending_intent_id') if key in decision}
    compact_decision['selection'] = {key: decision['selection'].get(key) for key in ('variant', 'model', 'effort')}
    value = {'kind': 'auto_session_v1', 'scope_id': scope_id, 'project_id': project_id, 'user_id': user_id,
             'gate_revision': gate_revision, 'policy_revision': policy_revision,
             'checkpoint': session.checkpoint(), 'input_count': len(messages), 'input_digest': digest(messages),
             'decision': compact_decision}
    if len(json.dumps(value).encode()) > MAX_CHECKPOINT_BYTES:
        raise ValueError('Routing checkpoint exceeds its supported bound')
    return value
