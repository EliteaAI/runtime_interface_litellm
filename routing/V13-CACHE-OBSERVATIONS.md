# Restart-safe cache observations (EL-6722 candidate)

The existing process cache loses accumulated cache observations on restoration.
The candidate carries advisory evidence inside the existing signed session token.
No table, billing store, routing classifier call or new provider dependency is added.

`Session.checkpoint()` writes version 2; version 1 remains readable and cold.
At most 64 records and 64,000 serialized bytes are retained. Malformed or oversized
optional evidence is dropped without discarding pending task state. Records contain
prefix hashes, model/effort/tool/cap identity, counters, epoch and timestamps, not
prompt text. Replaying the same identity/prefix/epoch/start-time receipt cannot
increase the observed hit count. Branches restore their signed ancestor; edits and
inventory/access/context revision changes still invalidate derived state.

The matching SDK stamps `request_started_at` immediately before invoke, async
invoke, stream, async stream and structured invocation. The Gateway requires finite,
ordered start/completion timestamps; old receipts without a start remain usable
for task intent but cannot assert warmth. The default evidence window is 300
seconds (five minutes), measured from request start, with expiry at the boundary.
Both receipt admission and economic quoting use the same constant. The separate
180-second in-process session eviction timer does not expire signed cache evidence.
This is an advisory default, not proof of a future cache hit or universal provider
retention. One-hour caching requires an explicitly verified contract. Restoring or replaying a receipt
never refreshes its age. Missing SDK cache counters remain absent; an explicit
zero remains an observed miss. Native normalized input totals include the cache
read/write buckets; hidden reasoning is not serialized into routing observations.

Existing expected-cost ranking can use this restored evidence under its existing
minimum-observation rule. Cold upper bounds and quality eligibility are unchanged.
This does not establish the historical hit frequency as a future probability.
Provider retention modes, cache-depth uncertainty, matched warm/cold trials and
improved cost forecasts remain separate calibration work.

The cache-state revision participates in the signed policy digest. Drain active
Auto invocations before deploying a policy revision; do not switch an in-flight
run. The candidate has source tests and synthetic reproduction, not live provider
cache-retention or browser acceptance. Judges are evaluation infrastructure only.

Focused checks (standalone plugin root):

```sh
python -m pytest --rootdir=tests --confcutdir=tests tests/test_cache_checkpoint.py -q
python -m pytest --rootdir=tests --confcutdir=tests tests/test_auto_routing_service.py -q
python -m pytest --rootdir=tests --confcutdir=tests tests/test_v12_algorithm.py -q
python -m pytest --rootdir=tests --confcutdir=tests tests/test_routing_manifest.py -q
```
