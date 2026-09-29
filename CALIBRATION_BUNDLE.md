# Installed calibration bundles

The default routing path is unchanged. A deployment can opt an exact project into
an installed, reviewed snapshot using module configuration:

```yaml
auto_routing_calibration_profiles:
  "7":
    revision: "<installed manifest revision>"
    profile: "<installed profile name>"
```

This is operator configuration, not an HTTP request field or model instruction.
It does not enable Auto: both existing environment and project gates still apply.
An invalid binding fails closed instead of falling back to the legacy catalog.

The release must include `routing/calibration/manifest.json` with an exact
revision and a `profiles` map. Each profile names a sibling JSON snapshot and its
SHA-256. Snapshots must declare classifier and baseline variant IDs, a measured
request scope, and required prospective forecast validation. The application
compiles these snapshots through the uniform evidence compiler; installing a
snapshot cannot bypass capability, adverse-evidence, native-contract or forecast
checks. Missing or changed files make the profile unavailable.

Preflight and native relay validate the same installed profile. The selected
snapshot digest also participates in the gate revision, revoking earlier pins
when the operator changes the profile or bundle. Cached compilers do not skip
file-integrity checks. Existing pins must drain before a rollout.

An isolated text profile cannot be reused for repository tools or conversation
history. A bounded conversation profile enforces its declared user-turn ceiling
and rejects prior tool history. Unsupported requests remain unsupported; a
forecast, teacher label or configured baseline is not a capability grant.

Bundle installation, activation, benchmark evidence and deployed browser
acceptance are separate operations. This code change performs none of them.
