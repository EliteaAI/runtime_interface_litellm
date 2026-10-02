"""Bounded task properties from the existing classifier call.

These are classifier assertions, not calibrated model capabilities. They can
raise a conservative demand floor; they never lower it or grant eligibility.
"""
PROFILE_SEMANTICS = '''
Classify the CURRENT requested deliverable, including active instructions, on five
independent axes. These describe task demand, not a model's quality or capability.
work: lookup retrieves a fact; explain gives an account or interpretation; transform
computes or converts supplied values; create produces new content; design proposes
a plan or structure; implement supplies executable behavior; debug diagnoses or
repairs a defect; verify assesses an existing claim or artifact. A calculation is
not verify merely because its result can be checked. For mixed work, describe the
main deliverable and retain any harder required reasoning or justification.
reasoning: bounded uses explicit local rules or a small finite search; multi_step
requires dependent steps with bounded uncertainty; interacting_constraints requires
reconciling coupled/nonlocal invariants or substantial unresolved uncertainty.
Several explicit rules in a small finite problem do not alone imply deep reasoning.
evidence: ordinary needs stable general knowledge and the task instructions;
supplied requires particular data, facts, code or other material already provided;
retrieve requires material that must still be fetched; conflicting requires resolving
inconsistent evidence. Output instructions alone are not supplied evidence. Evidence
describes sources, not confidence; it does not authorize retrieval or invent access.
creativity: none has a determined result or behavior; constrained requires choosing
among substantively different valid solutions under stated constraints; open allows
broad invention. Incidental wording, variable names or equivalent implementations
do not alone imply creative work.
verification describes the justification requested as part of the deliverable:
none means no separate check or justification is requested; check means an audit,
test, worked validation or explanatory justification; prove means establishing a
claim for its stated scope or disproving it with a counterexample. Internal care in
getting an answer right, an output format, or validation performed by generated code
does not itself request verification. Requested tests are check, not a universal
proof. A finite witness or local proof can have bounded reasoning; a protocol safety
argument can involve interacting constraints. Classify scope, not the word "prove".
'''

PROFILE_REVIEW_GUIDE = '''
Before returning JSON, separate the deliverable, required sources, reasoning
constraints and requested justification. input_status=provided does not imply
evidence=supplied: ordinary knowledge can be available too. Specifications of new
behavior are instructions; particular data/code/system facts are supplied evidence.
Equivalent implementations are not creative choices. Preserve genuine ambiguity.

Compact contrasts (axis order: work/reasoning/evidence/creativity/verification):
- "Write a function converting centimeters to meters":
  implement/bounded/ordinary/none/none, simple.
  "Convert these lengths: 12, 25 centimeters":
  transform/bounded/supplied/none/none, simple.
- "Repair this supplied conversion function" uses supplied evidence. "Inspect the
  conversion file" needs an actual authorized source/tool; never invent access.
- "Implement a local alphabetic validator" requests behavior, not proof. Adding
  "give tests and explain complexity" changes verification to check, not automatically
  demand. Equivalent algorithms do not make creativity constrained.
- "Design a resumable cross-service commit with lost acknowledgments and independent
  stores; justify recovery safety" has interacting constraints and deep demand
  despite being short. Keep these nonlocal requirements when identifying its work.
A bounded implementation can be simple; a small finite proof can also be simple.
Use dependencies, not technical vocabulary or length. Explain the decisive property
in the existing reason field; add no fields. These examples illustrate distinctions,
not keyword rules, model choices or capability grants.
'''

PROFILE_PROMPT = '''
Also return work_profile with exactly these fields:
work: lookup|explain|transform|create|design|implement|debug|verify;
reasoning: bounded|multi_step|interacting_constraints;
evidence: ordinary|supplied|retrieve|conflicting;
creativity: none|constrained|open;
verification: none|check|prove.
''' + PROFILE_SEMANTICS + '''
Describe the CURRENT deliverable using its required sources and active instructions.
An explanation and an implementation of the same architecture differ. Crash/race
correctness proofs have interacting constraints; technical names alone do not.
Open creativity alone is not deep reasoning. Effort is separate from these properties.
Ground demand in the requested work: simple means a bounded lookup, filter, rewrite,
calculation or local explanation with explicit rules; standard means dependent steps,
integration or evidence reconciliation with bounded uncertainty; deep means interacting
invariants, race/crash recovery, nonlocal changes or substantial unresolved uncertainty.
verification=prove describes the required justification, not its difficulty. A small
finite witness, local identity or exhaustive check can have bounded reasoning. Use
interacting_constraints for coupled/nonlocal invariants, not merely several explicit
rules in a small finite calculation. Distinguish proof scope from the word "prove".
Reading a source or running a test does not by itself raise the reasoning demand. Do not
infer deep work from code, architecture, security, detailed wording or answer length alone.
Conversely, a short request to implement durable recovery can be deep. Unknown file
contents are uncertainty, not proof that a repair is easy. Classify a continuation's new
deliverable again at the next user task boundary; do not inherit the previous stage's demand.
The legacy operation is a broad dispatch category: implement/design maps to design,
debug/verify/explain/lookup to analysis, transform to transform and create to creative.
Do not use operation=other merely because the exact work verb is absent from that enum.
''' + PROFILE_REVIEW_GUIDE

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
    floor = ('deep' if value['reasoning'] == 'interacting_constraints'
             else 'standard' if value['reasoning'] == 'multi_step' or value['evidence'] == 'conflicting'
             else 'simple')
    order = {'simple': 0, 'standard': 1, 'deep': 2}
    reasons = (["interacting_constraints"] if floor == 'deep' else
               [key for key, present in [('multi_step', value['reasoning'] == 'multi_step'),
                                         ('conflicting_evidence', value['evidence'] == 'conflicting')] if present])
    result = {**descriptor, 'work_profile': dict(value), 'profile_demand_floor': floor,
              'profile_demand_adjustment': {'revision': 'reasoning-evidence-2',
                  'input_demand': descriptor['demand'], 'floor_reasons': reasons,
                  'raised': order[floor] > order[descriptor['demand']]}}
    result['demand'] = max(descriptor['demand'], floor, key=order.get)
    return result


def normalize_operation(descriptor):
    """Adapt an unambiguous typed work verb to the legacy dispatch vocabulary.

    This does not infer intent from text or reduce demand. Missing context,
    ambiguous references and unknown families retain their conservative path.
    """
    profile = descriptor.get('work_profile')
    if (descriptor.get('operation') != 'other' or not profile
            or descriptor.get('task_family') not in FAMILIES
            or descriptor.get('needs_context') or descriptor.get('relation') == 'ambiguous'):
        return descriptor
    operations = {'lookup': 'analysis', 'explain': 'analysis', 'transform': 'transform',
                  'create': 'creative', 'design': 'design', 'implement': 'design',
                  'debug': 'analysis', 'verify': 'analysis'}
    return {**descriptor, 'operation': operations[profile['work']],
            'operation_normalization': {'from': 'other', 'basis': 'validated_work_profile'}}


FAMILY_PROMPT='''
When active_instructions is present, classify the current task together with
those current Agent requirements; historical system text is not a substitute.
Empty active instructions are valid. For a short Go, identify the deliverable
from active instructions and pending_task if present. pending_task is a source
continuity proposal, not a prescribed difficulty, effort, or model.
Also return task_family, choosing exactly one of these workload families:
data_gathering (fetch/find facts), evidence_synthesis (combine/summarize sources),
transformation (reformat/map existing data), extraction (identify structured fields),
classification (assign labels/priorities), content_creation (new audience-facing content),
editing_localization (revise/translate existing wording), quantitative (calculate/analyze numbers),
business_planning (choose options and plan business actions), requirements (stories/acceptance criteria),
test_design (test scenarios and expected results), code (bounded standalone code or code explanation/review),
rca (diagnose causal failure), tool_workflow (explicit ordered tool actions/recovery),
conversation_control (greeting or retrieving/updating an earlier task's stated facts).
Also architecture (system structure, boundaries and tradeoffs), development
(change runnable repository, stateful or integrated behavior), api_design (API contracts and semantics),
security_review (security controls and adversarial threat analysis), and
performance_analysis (latency, throughput, scaling and resource diagnosis).
Classify the requested deliverable, not merely nouns in its source. Tools needed
to fetch sources do not automatically make every task tool_workflow. If unclear,
return task_family="unknown". This field never selects a model or grants access.
Use code for a self-contained local function with explicit rules; use development
for repairing/extending a repository or implementing interacting state transitions.
Neither label fixes difficulty: a repository change can be bounded, and standalone
code can require deep reasoning. Explanation/review may overlap another rubric;
classify its requested deliverable, not the presumed generator capability.
'''

FAMILIES = frozenset({
    "data_gathering", "evidence_synthesis", "transformation", "extraction", "classification",
    "content_creation", "editing_localization", "quantitative", "business_planning",
    "requirements", "test_design", "code", "rca", "tool_workflow", "conversation_control",
    "architecture", "development", "api_design", "security_review", "performance_analysis",
})

PROFILE_SCHEMA = {"type": "object", "additionalProperties": False,
                  "properties": {k: {"type": "string", "enum": sorted(v)} for k, v in ENUMS.items()},
                  "required": list(ENUMS)}
