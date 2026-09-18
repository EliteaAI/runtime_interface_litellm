# After V11: implementation and evaluation gates

V11 is complete as a paired calibration study. It is not a held-out Auto benchmark
and does not, by itself, authorize changing production effort eligibility.
Judges, control fixtures and any adjudicator execute only in the evaluation harness.
The product consumes a versioned measured profile; it never waits for a quality judge.

## 1. Calibrate the graders before resolving their disagreements

**Owner:** evaluation harness. **Inputs:** the unchanged V11 packets, 161 paired
disagreements, the separate judge-controls-r1 diagnostic and original receipts.

The current AND rule is conservative, but one overstrict judge can create a false
rejection and two permissive judges can jointly miss an error. Averaging their
scores or choosing the majority of two does not fix this. Keep the dimensions
correctness/grounding, relevance and completeness separate. Keep cosmetic format
and word count separate unless the actual consumer requires a machine-readable
contract. Never convert a provider refusal into a capability score.

1. Build a versioned control corpus from independently checked correct answers and
   one-error mutations. Cover omission, unsupported claims, wrong state transitions,
   concurrency, creative constraints, stale context and code. Include subtle cases,
   short/long equivalents and prose/JSON equivalents. An initial 12-case authored
   diagnostic is useful for finding a broken grader; it cannot calibrate population
   accuracy or explain the 161 disagreements by itself.
2. Freeze expected labels and independent evidence before dispatch. Split controls
   into calibration and untouched evaluation sets. Hide producer, price, original
   verdicts, expected label and pair identity. Report false acceptance, false rejection,
   invalid output and uncertainty by family, rather than only aggregate agreement.
3. Use exact or executable oracles where applicable. A verified material error cannot
   be overruled by a high prose score. A passing oracle proves only its tested contract.
4. Qualify an adjudicator on those controls. First give it the original task, necessary
   evidence and final artifact without votes. Then provide anonymized, shuffled judge
   findings for a separate conflict review. Require cited answer/source spans, decisive
   checks and `pass | fail | unresolved`; permit abstention. The second stage must not
   silently replace its independent first assessment.
5. Adjudicate only disagreements or sampled audit cases after qualification. Preserve
   both original votes and all costs. Human review owns unresolved subjective cases.

**Acceptance:** known material-error controls fail, known-correct controls pass, and
invalid envelopes remain unknown; equivalent style changes do not systematically
change verdicts. Publish performance on untouched controls before assigning weights.
Do not derive judge weights from agreement with the other judge.

**Overhead:** 161 single-call adjudications would add 8.7% to the 1,856 initial
subjective judge calls; the two-stage design would add up to 17.3%. Token cost depends
on the conflict packet and is not proportional to call count. Product overhead is zero.

## 2. Repair fixture and assessment contracts prospectively

**Owner:** dataset and assessment modules. Preserve the V11 strict score.

- Specify whether `source` is a string or array in new fixtures. The 19 V11 source-type
  disagreements have a separate, narrowly defined semantic sensitivity view.
- Keep raw JSON compliance, recoverable structure and semantic correctness as separate
  fields. Never silently promote a recovered object to native structured-output proof.
- For multiple generated implementations, extract unchanged candidates, hash them and
  require every candidate to pass the same isolated oracle. A prose wrapper must not
  hide a contradictory implementation. Never repair code before grading the original.
- Correct the judge envelope failure prospectively: test provider-supported strict
  tool/schema output and reject nested or missing required fields. The one repeated
  invalid Terra judgment remains unknown. Do not keep retrying valid negative votes.

**Acceptance:** fixtures distinguish wrong values from unspecified representation;
malformed judge output cannot become a pass; fixture revisions cannot overwrite old
answers, labels, request hashes or accounting.

## 3. Promote model-and-effort contracts through the actual product path

**Owners:** runtime_interface_litellm catalog/service/relay and SDK Auto model adapter.
The V11 JSON is measured observations, not a configured model inventory.

Current V9 measured-contract consumers are default-only. Adding effort rows to a JSON
cannot enable them: catalog construction clears effort, service filters explicit
measured efforts, and relay validation rejects custom thinking/reasoning parameters.

Implement one contract end to end:

`resolved model identity + provider deployment + transport + requested effort +
output allowance semantics + capabilities + profile revision + evidence revision`.

1. Keep shared models plus project models; project configuration wins name collisions.
   Resolve deployment aliases to verified identities without discarding other shared
   models. Missing evidence means unqualified, not absent from the manual picker.
2. Materialize only supported model/effort candidates. Use measured native Anthropic
   adaptive effort on native-capable deployments; do not infer it for the local
   compatible path with thinking disabled. A compatible adapter may qualify separately;
   its protocol alone is not a reason to blacklist the provider.
3. Remove default-only guards only together with explicit serialization/validation and
   round-trip tests. Verify the transmitted field and output allowance, never claim
   the provider executed an exact hidden thinking budget. Default means omitted effort.
4. Pin model and effort for the entire autonomous run, including tool loops and steering.
   Each fresh ordinary Auto subagent gets a separate decision; explicitly chosen models
   win. Keep pipeline selectors without Auto in this first release.
5. Snapshot the profile revision per run; publish immutable profiles and support rollback.
   No quality-grader calls, new training service or database table is required.

**Acceptance:** contract fixtures, provider request snapshots, concurrent child isolation,
manual override, steering, native replay integrity and local/dev A/B checks all pass.
Deploy behind existing platform/project flags. Do not change transport as an incidental
part of an evidence update.

## 4. Qualify effort selection on new tasks

**Owner:** policy/calibration harness; depends on phases 1–3.

V11 has only three or four tasks per rubric/preset and often one per demand. Do not
fit a fine-grained capability probability from a 1/1 cell. More thinking is not
monotonically better: inspect both new passes and lost passes against the same model's
fresh default. Treat quality, refusal availability, schema/tool reliability, latency
and total billable cost as separate observations.

Build prospective candidates using work type, dependent reasoning steps, interacting
constraints, ambiguity, evidence conflicts, creative freedom and execution needs.
Use the existing one classifier call; preserve lexical fast paths and bounded relevant
context. Cache a decision for the autonomous run, not for an unrelated future task.
The architecture-to-durability-implementation follow-up must be allowed to increase
demand at the next user turn, even if the previous model has a warm cache.

**Selection contract:** first filter authorization, transport and required capabilities;
then require a validated quality floor for the task stratum; finally compare expected
total task cost including thinking/output uncertainty, classifier, cache reads/writes,
tool loops and permitted retries. Use intervals/scenarios for unknown output length.
Do not fabricate cache hits or optimize solely on published output-token price.

**Acceptance:** freeze policy before a new 100-stable + 300-random task evaluation;
distribute random tasks across topic-changing conversations with real tool trajectories,
nested/parallel ordinary agents and management/engineering workloads. Use untouched
tasks rather than the 75 calibration tasks. Compare fixed and Auto policies on identical
tasks and publish regressions, not only aggregate success. No superiority claim from
V11's mixed fixed-preset percentage.

## 5. Investigate provider policy availability separately

**Owner:** provider integration/evaluation. V11 contains 27 direct Opus 5 refusals and
one dependent task blocked by a prior refusal, including an unexpected gardens aside.

Preserve native stop reasons, exact request/transport and deployment revision. Review
with the provider/platform team whether behavior is intentional policy, context effects
or deployment configuration. Run a separately labeled repeatability study on unchanged,
authorized benign prompts if needed. Do not weaken safeguards or rephrase prohibited
content to bypass them. A security-family blanket capability downgrade is unsupported.
Report operational task completion and content quality conditional on an answer
separately, while retaining refusal costs in end-to-end economics.

## 6. Measure selection coverage without provider quotas

**Owner:** routing observability and evaluation. V11 generates with Terra, Sol and three
Opus versions; it does not compare Luna, Haiku or Sonnet as generators.

Add an eligibility funnel per decision: inventory → allowed deployment → transport/
tools/context → measured support → quality threshold → cost ranking → selected.
Record why each candidate lost and the profile revision, without logging secrets.
If Anthropic selection approaches zero, distinguish missing evidence/transport guards
from quality failures, policy refusals or actual economic dominance. Add paired Sonnet,
Haiku and Luna comparisons on the same new tasks, native/compatible contracts and
representative warm/cold context lengths. Do not force traffic to a family to make a
chart look balanced. Keep explicit model selection available.

**Acceptance:** each exclusion has a reproducible reason; candidates with qualifying
quality and a better measured task cost can win irrespective of provider; manual
selection remains unaffected. Validate high-value engineering and creative workloads,
not only short transformations where an ultra-cheap model is naturally favored.

## 7. Release evidence and rollout

Keep the issue open and PRs in review until their own CI and live acceptance gates pass.
Publish static HTML, machine-readable observations, original/supplementary result
layers and SHA-256 manifests. No credentials or hidden reasoning are needed in reports.
Record unknown charges rather than treating failed attempts as free.

Start controlled dev rollout with flags and immutable profile revision, then collect
human quality feedback linked to the task and final artifact. Human acceptance is a
separate rollout metric; it has not been measured by the local calibration harness.

Research context: [MT-Bench judge study](https://arxiv.org/abs/2306.05685) describes
several judge biases; [Panel of LLM Evaluators](https://arxiv.org/abs/2404.18796)
studies diverse panels. Neither establishes the cause of this run's asymmetry or
validates an untested third judge for Elitea.
