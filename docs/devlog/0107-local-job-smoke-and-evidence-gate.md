# 0107 — Prove the proposed load Job can start, without calling it an EKS run

Date: 2026-09-21. **Local preparation only:** no AWS resources were created.
The EKS pilot from 2026-09-20 remains torn down.

## Why

The [in-cluster proposal](0106-in-cluster-load-proposal.md) avoided the failed
local traffic tunnel, but a rendered Job and `k6 inspect` had not proved that
Kubernetes could mount the script, resolve the internal Service, run the pinned
image as a non-root user, and collect logs. Equally, a k6 summary could be
mistaken for a completed hour even after an interrupted run.

## What and how

An isolated `kind-kubefit-smoke` cluster (Kubernetes 1.34) received the existing
two-replica demo and the **unchanged** proposed Job. Only its ConfigMap source
was replaced by a clearly marked [15-second smoke script](../../tests/fixtures/k6/eks_job_smoke.js).
The smoke runs at 5 requests/second and reports `smoke_only: true`; it cannot
be used as one-hour observation evidence. The isolated cluster was deleted
after the check, leaving unrelated local Docker containers untouched.

```text
15-second fixture → ConfigMap → unchanged Job → internal demo Service
                           │              │
                    non-root mount    Pod status + logs
                           └──── local smoke only ────┘

future EKS Job + Pod + immutable ConfigMap JSON + k6 log → verifier
                                        │
                            load profile complete? (only)
                                        │
                       separate Prometheus + UID readiness check
```

The [Job evidence verifier](../../safety/eks_observation_job.py) requires one
owned, succeeded, no-retry Pod with the pinned image and exact command/target,
an immutable ConfigMap with the preregistered script SHA-256, no container restart,
zero exit status, 3590–3900 seconds of container runtime, and a single
60-minute profile summary with approximately 100,500 requests, zero dropped
iterations, and under 1% request errors. Its PASS does **not** bypass KubeFit
readiness or imply cloud cost savings.

## Evidence and limits

- The API server accepted the Job with server-side dry-run on Kubernetes 1.34.
- The local Job completed: `76` requests, `0` dropped iterations, `0` request
  errors; the Pod succeeded with `0` restarts and used the pinned image digest.
- Twenty-four verifier unit cases covered the valid evidence shape and partial, failed,
  restarted, wrong-image, wrong-owner, short-duration, wrong-profile, dropped,
  and high-error evidence.
- An attempted `kind load docker-image` of the multi-architecture digest could
  not import the arm64-only local image into an amd64 kind node. The Job instead
  pulled the pinned image from the registry and succeeded. This is a local
  image-loading limitation, not an EKS validation result.

The smoke did **not** test an hour of load, EKS scheduling or node headroom,
Prometheus continuity, KubeFit readiness, a recommendation, or an AWS bill.
The next paid attempt still needs explicit cost and deadline approval; after
the Job completes, collect its JSON and logs before TTL expiry, run the
verifier, and perform the independent readiness checks in the
[runbook](../../deploy/eks-pilot/monitoring-runbook.md).
