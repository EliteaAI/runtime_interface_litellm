""" Auto routing shadow decisions as tracing events (#6722 V5 D5).

Same OpenTelemetry path as `usage_audit` (tracing plugin span processors),
under its own tracer so audit-trail analytics never count it. Ids, scores
and costs only: no prompt text, no credentials.
"""
import json

from pylon.core.tools import log  # pylint: disable=E0611,E0401

TRACER_NAME = "auto-routing"
EVENT = "auto_routing_shadow_decision"
CANDIDATE_FIELDS = ("variant", "deployment", "canonical", "effort", "p_pass", "expected_cost_usd", "ineligible", "gates")


def shadow_record(v5_trace, *, project_id, user_id, invocation_id, executed):
    """The event payload: a projection of `trace.v5`, bounded by the catalog size."""
    return {
        "event": EVENT, "project_id": project_id, "user_id": user_id, "invocation_id": invocation_id,
        "selector_id": v5_trace.get("selector_id"), "selector_sha256": v5_trace.get("selector_sha256"),
        "policy_revision": v5_trace.get("policy_revision"), "tau": v5_trace.get("tau"),
        "reason": v5_trace.get("reason"), "shadow": v5_trace.get("shadow"),
        "v5_choice": v5_trace.get("chosen"), "legacy_choice": v5_trace.get("legacy_choice"),
        "executed": executed,
        "candidates": [{k: c.get(k) for k in CANDIDATE_FIELDS if k in c} for c in v5_trace.get("candidates", [])],
    }


def emit_shadow_decision(result, *, project_id, user_id):
    """Best effort; a tracing failure never affects routing."""
    try:
        trace_v5 = ((result or {}).get("trace") or {}).get("v5")
        if not isinstance(trace_v5, dict):
            return None
        record = shadow_record(trace_v5, project_id=project_id, user_id=user_id,
                               invocation_id=result.get("invocation_id"),
                               executed=(result.get("config") or {}).get("model_name"))
        from opentelemetry import trace  # pylint: disable=C0415,E0401
        span = trace.get_tracer(TRACER_NAME).start_span(name=EVENT, attributes={
            "auto.event": EVENT, "project.id": int(project_id), "user.id": int(user_id),
            "auto.reason": str(record["reason"]), "auto.shadow": bool(record["shadow"]),
            "auto.selector_id": str(record["selector_id"]), "auto.policy_revision": str(record["policy_revision"]),
            "auto.record": json.dumps(record, sort_keys=True, separators=(",", ":"), default=str),
        })
        span.end()
        return record
    except Exception:  # pylint: disable=W0703
        log.debug("routing_trace: failed to emit %s", EVENT, exc_info=True)
        return None
