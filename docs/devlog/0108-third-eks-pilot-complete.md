# 0108 — Complete the disposable EKS observation and verify teardown

Date: 2026-09-21. The operator separately approved this paid pilot with a
USD 5 planning ceiling and a 22:20 KST stop time. This entry records observed
results, not authority for another launch. Private plan, kubeconfig, raw Job
objects, logs, and analysis remain under ignored `.kubefit/eks-pilot/`.

## Why

The second EKS probe verified nodes and metrics but a local traffic
port-forward disconnected before the fixed hour finished. The unresolved
question was whether internal load delivery could complete the preregistered
profile while the same workload Pods and Prometheus history stayed intact.

## How and what happened

```text
approved saved plan (50 creates)
  → EKS 1.34 + two private m6i.large nodes + three add-ons
  → ephemeral Prometheus + two synthetic nginx Pods
  → internal k6 Job, fixed 60-minute script
  → Job evidence verifier PASS + unchanged Pod identities
  → UID-verified readiness eligible + read-only analysis
  → scoped destroy plan (50 deletes) + AWS inventory empty
```

The exact saved creation plan passed the structural auditor; Terraform
applied 50 resources. Both workers became Ready. Helm chart 88.5.0 installed
without a PVC or LoadBalancer; both demo Pods scheduled on separate nodes.
Current-Pod CPU, memory, throttling, `kube_pod_info`, and `kube_pod_owner`
series each returned two results. An initial readiness call correctly stayed
`collecting` because the observation hour had not elapsed.

The immutable ConfigMap used the committed script SHA-256
`1e2e6ae0cc7e7f434cf981b8a95970d7e51b519d651dbb4c862dbd1a00d2a816`.
The unchanged Job ran inside the cluster against the internal demo Service:
10 minutes at 5 requests/s, 35 minutes at 25 requests/s, 5 minutes at
100 requests/s, then 10 minutes at 25 requests/s. It ran from about
10:03:51 to 11:03:51 UTC. Its Job completed with one Succeeded Pod, zero
restarts, zero exit status, 100,503 requests, zero dropped iterations, and
zero request errors. The fail-closed verifier returned
`verification: load_profile_complete` and `container_duration_seconds: 3600.0`.

Saved before/after workload and Prometheus Pod identities and restart counts
matched. At 11:05:54 UTC, UID-verified readiness reported `eligible`, with
122 usage and throttling samples and 100% observation coverage. Read-only
analysis recommended 10m CPU/32Mi memory requests and 20m CPU/48Mi limits
for the synthetic nginx container, compared with current 1000m/2048Mi
requests and 2000m/4096Mi limits. Patch eligibility was `eligible`; **no
patch was applied or PR opened**.

The request-cost model projected $64.605/month current versus $0.767184/month
recommended, or 98.8% lower *modeled request allocation*. This uses a
50/50 allocation of a $0.118/hour `m6i.large` node rate, 730 hours, and two
replicas. It is **not actual AWS bill savings**: no nodes were scaled down as
a result of a request change, EKS/NAT/storage/data charges were excluded, and no before/after
performance or user-traffic comparison was performed. The large percentage
primarily reflects intentionally overprovisioned synthetic nginx.

## Cleanup and operational finding

After retaining private evidence, the Job, ConfigMap, demo manifests, and
Helm release were removed. The scoped Terraform plan showed only 50 deletes
from the experiment modules and approval gate; applying that saved plan
completed with 50 destroyed. The managed node group took about 15m44s to
delete. A final read-only inventory found the named EKS cluster absent, no
experiment EC2 instances or EBS volumes, no experiment VPC, a deleted NAT
gateway, and no EIP, Auto Scaling group, or load balancer. This finished well
before the registered stop time. Billing data was not yet available, so actual
charges remain unknown; the USD 5 field was an approval ceiling, not an
automatic spending limit.

Helm v4.2.4 rejected `helm list --all`; plain `helm list --namespace
monitoring` provided the needed collision check. The runbook now uses that
form. The practical decision is to retain the internal Job path for any
separately approved repeat, while leaving long local traffic port-forwards
as failed historical evidence. A future claim about AWS savings would require
an actual changed deployment, a valid before/after performance comparison,
capacity reduction, and observed billing—not this compatibility pilot.
