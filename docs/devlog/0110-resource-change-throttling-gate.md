# 0110 — Fail closed on post-change CPU throttling

Date: 2026-09-21. Status: validated in disposable local kind. No AWS resources
were created. The experiment followed the [before/after finding](0109-local-recommendation-before-after.md).

## Why

The resource-change benchmark previously returned a performance Pair PASS
without reading Prometheus CPU throttling. A candidate with a 20m CPU limit
passed two short latency trials while a separate query showed throttled CFS
periods. A PASS-only downstream gate could mistake that narrow performance
result for broader safety evidence.

## What and how

New `benchmark-change` executions detect changes to container resource fields.
For each base and candidate run they capture current Deployment-owned Pod UIDs,
measure the fixed k6 profile, confirm the same Pod identities and runtime
counters afterward, verify those UIDs in Prometheus, and query throttled-period
P95 in the aligned load window. The query must cover **every** measured Pod
with at least 80% of the expected five-second query points (and at least
three samples). The observation and Pod UIDs are stored inside
the content-addressed performance artifact and replayed on load.

```text
resource-change bundle
    ↓
base/candidate rollout → current Pod UIDs → fixed k6 interval
    ↓                                  ↓
stable UID/runtime check       aligned Prometheus throttling P95
    └──────────────────────┬───────────┘
                           ↓
        latency/error FAIL → FAIL
        throttling missing or above threshold → REVIEW_REQUIRED
        all required checks pass → PASS
                           ↓
                  opposite-order Pair
```

The review thresholds reuse the existing resource benchmark policy: candidate
CPU throttling P95 above **5%**, or an increase above **1 percentage point**
over the base. Missing Prometheus data, incomplete Pod coverage, changed Pod
identity, or changed restart/OOM counters also prevent a safety PASS. A
latency/error failure remains `FAIL` even if throttling also needs review.
`review_required` artifacts and Pairs are persisted, return CLI exit code 2,
and cannot satisfy downstream PASS-only PodKill prerequisites. Image-only or
replica-only changes retain their existing performance-only behavior.

### Compatibility boundary

Old immutable Pair artifacts are still readable and retain their historical
*performance-only* PASS. They are **not** silently upgraded into throttling
evidence. A legacy resource-change Pair lacking the new policy cannot satisfy
PodKill prerequisites even if its historical performance status was PASS.
Newly executed resource-change benchmarks require the new signal.
The artifact stores the derived P95 and Pod UID binding, not raw Prometheus
range-response bytes; it verifies internal replay and source identity but
cannot independently reconstruct the metric from an offline Prometheus dump.

## Verification

Unit and artifact tests cover high throttling, missing evidence, low
throttling, incomplete Pod series, Pod UID replacement, replayable
`review_required` results, and Pair propagation. The pre-change local Pair
still loads as PASS under its old policy.

A fresh local kind 1.36.1 run then used the same synthetic nginx resource
change. The collector recorded base throttling P95 **0%** and candidate
throttling P95 **10.204%** across the current UID-bound Pods. The candidate
also exceeded the existing latency regression policy (steady P95 +15.78%,
steady P99 +82.18%, spike P95 +27.04%, spike P99 +1871.20%), so the actual
saved run was **FAIL**, not merely `review_required`. Its throttling checks
were warnings, and the base manifest was restored. This live run proves the
new collection and stronger failure path; the unit tests establish the
`review_required` branch when performance otherwise passes. It does not
prove a safe CPU limit, statistical significance, production suitability,
or AWS cost savings.

The localhost API and Prometheus tunnels were stopped, and the exact
`kind-kubefit` cluster was deleted. `kind get clusters` returned none.

## Next question

If the tool is used on a representative application, should its current 5%
and 1-percentage-point thresholds be calibrated against that application's
latency and throughput SLO? This synthetic nginx run cannot answer that.
