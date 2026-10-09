# V13 qualification evidence

The product catalog now retains measured family cells that did not qualify, as
well as admitted cells. The selector uses the same exact family/demand checks
and exposes the assessment of each inventory-admitted calibrated variant in
`trace.selection.family_qualification.assessments`.

Statuses distinguish `measured_failure`, `provider_refusal` (only when recorded
separately), `unresolved_measurement`, `insufficient_samples`, `unmeasured_family`,
`unobserved_demand`, `not_qualified`, `unresolved_task`, and
`provisional_qualified`. Counts, the original Wilson interval, observed demands
and source revision remain attached. Missing evidence is not a failed answer;
an all-pass cell with three samples is not a production quality guarantee.

A preset with no admitted family cells remains disabled with its evidence
available, rather than failing catalog compilation on an empty demand set.
Project/shared authorization and request-contract filtering still run first.
No extra classifier or judge calls are added. Signed checkpoint selections stay
compact and pins still preserve model/effort for an autonomous run.

This changes the catalog/policy fingerprint; install at a planned boundary for
Auto runs. It does not activate the Stage V13 candidate JSON, deploy code, alter
original judgments, add provider quotas, or demonstrate improved quality/cost.
The calibration harness can now distinguish data gaps from measured failures
using the same selector that serves the application.

Validate with the focused qualification tests plus the original V12 all-cell
admission, effort/pin, service, context and economic regression tests. Difficulty
labels still require an independent audit before reporting classifier accuracy.
