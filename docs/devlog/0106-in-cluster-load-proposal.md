# 0106 — Decouple EKS load generation from the local API tunnel

Date: 2026-09-20. **Local preparation only:** no EKS cluster exists and the
proposed Job was not deployed. A future paid attempt needs a new plan, budget,
deadline, and explicit approval.

## Why

The [second EKS probe](0105-second-eks-pilot.md) proved two Ready nodes and
current-Pod metrics, but the one-hour k6 run failed when its local Service
port-forward and the Prometheus port-forward lost the EKS API stream together.
The application and monitoring Pods had zero restarts. Restarting the tunnel
would not have restored the preregistered uninterrupted load profile.

## What and how

We prepared one bounded Kubernetes [Job](../../deploy/eks-pilot/observation-job.yaml)
in `kubefit-demo`. It uses the **same** committed
`benchmarks/k6/observation_profile.js` via a ConfigMap made from that file,
and sends traffic to the internal demo Service. No load balancer, ingress,
PVC, extra node, long-lived service account token, or new k6 script is needed.
The Job has no retry, a 65-minute active deadline, a one-hour post-finish
retention for log collection, a pinned k6 2.1.0 image digest, non-root
execution, and explicit 250m/256Mi requests with 1 CPU/1 GiB limits.

```text
local script ──ConfigMap──> EKS Job ──ClusterIP──> demo Pods
                               │
                         Job status/logs
Prometheus ──short local tunnel──> UID-verified readiness
```

A local API tunnel failure should not stop an already running Job. However,
moving load into the cluster changes node co-tenancy and may affect latency
or scheduling. The fixed two-node topology is **not yet proven** to have enough
headroom for this Job during its 100 req/s spike. A Job failure, dropped
iteration, or nonzero k6 threshold result still invalidates the run. The
Prometheus tunnel may be reconnected for read-only analysis only after
confirming unchanged Prometheus and workload identities; a Prometheus Pod
restart would lose `emptyDir` history.

## Evidence and next decision

- The image registry reported the pinned multi-architecture digest, including
  an amd64 manifest for the EKS `m6i.large` nodes.
- Local Docker ran that digest with UID/GID 12345 and privileges dropped, then
  `k6 inspect` loaded the source script with its original scenarios and
  thresholds. This generated **no traffic**.
- The ConfigMap command rendered the committed script locally. A static test
  checks the Job's internal target, pinned image, bounds, and security fields.
- Kubernetes API schema validation, EKS scheduling, actual Job runtime,
  co-location effects, and the full 60-minute result remain unverified.

On a future approved run, first check capacity and object collisions, then
create only the reviewed ConfigMap and Job. Monitor Job conditions via short
reads, capture logs before TTL cleanup, and reject a partial/failed profile.
Do not call the old interrupted run a successful baseline.
