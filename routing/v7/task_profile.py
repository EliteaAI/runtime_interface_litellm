"""Bounded task properties from the existing classifier call.

These are classifier assertions, not calibrated model capabilities. They can
raise a conservative demand floor; they never lower it or grant eligibility.
"""
PROFILE_SEMANTICS_VERSION = 'current-deliverable-3.2'
PROFILE_SEMANTICS = '''
Classify the CURRENT requested deliverable, including active instructions, on five
independent axes. These describe task demand, not a model's quality or capability.
work: lookup retrieves a fact; explain gives an account or interpretation; transform
computes or converts supplied material, including paraphrasing it; create produces
new content; design proposes a plan or structure; implement supplies executable
behavior; debug diagnoses or repairs a defect; verify assesses an existing claim or
artifact, including proving or refuting a stated claim. Choose the primary artifact:
a requested calculation with a justification remains transform; a new design with a
proof remains design; a proof of a given claim is verify. Retain the hardest REQUIRED
reasoning even when it supports, rather than names, the main artifact.
reasoning: bounded uses explicit local rules or a small finite search; multi_step
requires dependent steps with bounded uncertainty; interacting_constraints requires
reconciling nonlocal invariants or global tradeoffs that cannot be settled by checking
steps locally, or substantial unresolved uncertainty. Routine workflows with roles, branches, approvals, retries
or exceptions are multi_step when their interactions can be resolved locally.
Several requirements being related is not enough for interacting_constraints.
State the nonlocal invariant or unresolved dependency in reason when choosing it.
A fixed chain of arithmetic, tests or enumerated cases can still be bounded.
evidence: ordinary needs stable general knowledge and instructions specifying the
new artifact; supplied requires examining particular input values, quoted text,
claims, existing code or facts about an existing situation. Desired behavior and
assumptions for a NEW design are instructions, even when detailed. Facts describing
an EXISTING system to diagnose or adapt are supplied. A mathematical expression or
finite data explicitly given for verification is supplied. retrieve needs material
not yet fetched; conflicting must reconcile incompatible evidence about the SAME
claim. Merely restating, identifying or explaining an unresolved contradiction without
resolving its sources uses supplied evidence. Explaining different definitions is not
by itself resolving conflicting facts. Raw task difficulty and a downstream conservative
policy floor are separate; do not copy a policy floor into the difficulty judgment.
Evidence describes sources, not confidence; it never authorizes or invents access.
creativity: none has a determined result or behavior; constrained requires choosing
among substantively different valid solutions; open allows broad invention. Wording,
variable names and equivalent implementations alone are not creative choices.
verification describes justification REQUESTED in the deliverable: none means no
separate validation or justification; check means tests, worked validation, an audit,
an explained tradeoff, or tracing why/how a proposed result works in specified cases;
prove means establishing or refuting
a stated claim across its allowed domain or schedules. A safety requirement or a
request to state a guarantee does not itself ask for its proof. Planning future
checks is design, not performing those checks now. Proving every permitted failure
schedule is prove even without that verb; inspecting a few concrete failures is check.
"No proof required" excludes prove, not an explicitly requested check or explanation.
Plain description without a requested rationale or scenario trace remains none.
A local proof can be bounded; a coupled protocol proof can be deep. Classify the
scope, not the word "prove", and do not invent additional obligations.
'''

PROFILE_REVIEW_GUIDE = '''
Separate the primary artifact, source material, dependencies and requested
justification before returning JSON. input_status=provided does not imply supplied
evidence: ordinary knowledge may be available too. Preserve genuine uncertainty.
Compact contrasts (work/reasoning/evidence/creativity/verification):
- "Write a function converting centimeters to meters":
  implement/bounded/ordinary/none/none, simple.
  "Convert these lengths: 12, 25 centimeters; show the calculation":
  transform/bounded/supplied/none/check, simple.
- "Restate this definition in plain language" transforms supplied text.
  "Explain why the definition excludes this example" explains supplied material.
- "Prove that each listed number is positive" verifies supplied data; bounded and
  simple. "Design an allocation protocol and justify safety for every allowed crash
  and delayed message" is design with interacting constraints and prove, deep.
- A roles-and-exceptions workflow is multi_step/standard; explaining how a named
  exception proceeds is check, while merely listing roles and steps is none.
  A guarantee across independently failing participants can be interacting_constraints/deep.
Use these distinctions across topics, not as keyword rules. Difficulty comes from
required dependencies, not technical vocabulary, stakes or answer length. Explain
the decisive property in the existing reason field; add no fields or model choices.
Before returning JSON, compare reasoning with demand. interacting_constraints and
simple/standard, or multi_step and simple, indicate that at least one assertion
needs reconsideration. Correct the assertion from the requested work; do not merely
raise demand to hide a mislabeled routine workflow. Evidence conflict alone need
not make a bounded reconciliation difficult; the policy floor remains separate.
'''

PROFILE_PROMPT = '''
Also return work_profile with exactly these fields:
work: lookup|explain|transform|create|design|implement|debug|verify;
reasoning: bounded|multi_step|interacting_constraints;
evidence: ordinary|supplied|retrieve|conflicting;
creativity: none|constrained|open;
verification: none|check|prove.
''' + PROFILE_SEMANTICS + '''
Ground demand in the CURRENT requested work: simple is a bounded lookup, rewrite,
calculation, local implementation or explanation with explicit rules; standard is
dependent integration or evidence reconciliation with bounded uncertainty; deep is
coupled/nonlocal invariants or substantial unresolved uncertainty. Reading a source,
running a test, proof vocabulary, architecture and open creativity alone do not set
difficulty. Conversely, a short durable-recovery request can be deep. Missing file
contents are uncertainty, not evidence that a repair is easy. Reclassify the new
deliverable at each user task boundary; do not inherit the previous stage's demand.
The legacy operation is broad: implement/design maps to design,
debug/verify/explain/lookup to analysis, transform to transform and create to creative.
Do not use operation=other merely because the exact work verb is absent from its enum.
''' + PROFILE_REVIEW_GUIDE

ENUMS = {
    'work': {'lookup', 'explain', 'transform', 'create', 'design', 'implement', 'debug', 'verify'},
    'reasoning': {'bounded', 'multi_step', 'interacting_constraints'},
    'evidence': {'ordinary', 'supplied', 'retrieve', 'conflicting'},
    'creativity': {'none', 'constrained', 'open'},
    'verification': {'none', 'check', 'prove'},
}


def profile_consistency(demand, value):
    """Expose contradictory classifier assertions without inventing corrected labels.

    Only reasoning has an intrinsic minimum under the shared definitions. Conflict
    evidence can trigger a conservative floor even for bounded work, so it is not
    itself an inconsistent raw difficulty assertion.
    """
    minimum = {'bounded': 'simple', 'multi_step': 'standard',
               'interacting_constraints': 'deep'}[value['reasoning']]
    order = {'simple': 0, 'standard': 1, 'deep': 2}
    return {'revision': PROFILE_SEMANTICS_VERSION,
            'status': 'reasoning_exceeds_raw_demand' if order[minimum] > order[demand] else 'consistent',
            'raw_demand': demand, 'reasoning_minimum': minimum,
            'labels_corrected': False}


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
              'profile_consistency': profile_consistency(descriptor['demand'], value),
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
