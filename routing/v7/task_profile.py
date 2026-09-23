"""Bounded task properties from the existing classifier call.

These are classifier assertions, not calibrated model capabilities. They can
raise a conservative demand floor; they never lower it or grant eligibility.
"""
PROFILE_PROMPT = '''
Also return work_profile with exactly these fields:
work: lookup|explain|transform|create|design|implement|debug|verify;
reasoning: bounded|multi_step|interacting_constraints;
evidence: ordinary|supplied|retrieve|conflicting;
creativity: none|constrained|open;
verification: none|check|prove.
Describe the CURRENT deliverable using its required sources and active instructions.
An explanation and an implementation of the same architecture differ. Crash/race
correctness proofs have interacting constraints; technical names alone do not.
Open creativity alone is not deep reasoning. Effort is separate from these properties.
Ground demand in the requested work: simple means a bounded lookup, filter, rewrite,
calculation or local explanation with explicit rules; standard means dependent steps,
integration or evidence reconciliation with bounded uncertainty; deep means interacting
invariants, race/crash recovery, nonlocal changes, proof or substantial unresolved uncertainty.
Reading a source or running a test does not by itself raise the reasoning demand. Do not
infer deep work from code, architecture, security, detailed wording or answer length alone.
Conversely, a short request to implement durable recovery can be deep. Unknown file
contents are uncertainty, not proof that a repair is easy. Classify a continuation's new
deliverable again at the next user task boundary; do not inherit the previous stage's demand.
'''

ENUMS = {
    'work': {'lookup', 'explain', 'transform', 'create', 'design', 'implement', 'debug', 'verify'},
    'reasoning': {'bounded', 'multi_step', 'interacting_constraints'},
    'evidence': {'ordinary', 'supplied', 'retrieve', 'conflicting'},
    'creativity': {'none', 'constrained', 'open'},
    'verification': {'none', 'check', 'prove'},
}


def apply_profile(descriptor, value):
    if value is None:
        return descriptor  # Legacy descriptors retain their existing policy.
    if not isinstance(value, dict) or set(value) != set(ENUMS):
        raise ValueError('WORK_PROFILE_FIELDS')
    if any(not isinstance(value[k], str) or value[k] not in allowed for k, allowed in ENUMS.items()):
        raise ValueError('WORK_PROFILE_ENUM')
    floor = ('deep' if value['reasoning'] == 'interacting_constraints' or value['verification'] == 'prove'
             else 'standard' if value['reasoning'] == 'multi_step' or value['evidence'] == 'conflicting'
             else 'simple')
    order = {'simple': 0, 'standard': 1, 'deep': 2}
    result = {**descriptor, 'work_profile': dict(value), 'profile_demand_floor': floor}
    result['demand'] = max(descriptor['demand'], floor, key=order.get)
    return result
