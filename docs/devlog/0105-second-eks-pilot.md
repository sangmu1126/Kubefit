# 0105 — Second EKS pilot: add-on recovery and controlled observation

Date: 2026-09-20, `ap-northeast-2`. This is an explicitly approved,
disposable **compatibility** experiment with synthetic traffic, not evidence
of production savings. The approved ceiling is USD 5 with cleanup by
`2026-09-20T12:30:00Z` (21:30 KST). The budget field is not an AWS-enforced
cap; actual billed cost is not yet known.

## Why

The [first live probe](0104-first-eks-probe-and-teardown.md) stopped at two
`NotReady` workers while the EKS configuration omitted managed add-ons. This
run tests whether the corrected add-on ordering produces a working two-node
cluster, and whether Prometheus plus KubeFit can read current Pod metrics on
EKS. It does not test AWS invoice savings, real-user traffic, or a production
rollout.

## How and observed facts so far

```text
Approved create-only plan: 50 add, 0 change, 0 destroy
  → EKS 1.34 + two on-demand m6i.large nodes
  → vpc-cni/kube-proxy before compute, CoreDNS after compute
  → local Prometheus + two synthetic nginx Pods
  → UID-verified KubeFit readiness and 60-minute fixed k6 profile
  → explicit teardown and AWS inventory check
```

The saved creation plan passed the repository's structural auditor. The EKS
control plane and the two pre-compute add-ons were created. Terraform's node
group creation then ended with a `ResourceInUseException` (HTTP 409) saying
the group already existed. A read-only EKS query showed that exact group was
`ACTIVE`, with two desired nodes and no health issues; Terraform state lacked
only its resource entry. We imported the **exact existing node group** into
the local state instead of retrying creation or creating a replacement. The
next refreshed plan contained only CoreDNS (`1 add, 0 change, 0 destroy`),
which was applied. A subsequent refreshed Terraform plan returned `No changes`.
This is a manual state-reconciliation recovery, not evidence that an
unattended apply is reliable.

Both workers are `Ready`, one in `ap-northeast-2a` and one in `2b`. `aws-node`,
`kube-proxy`, and both CoreDNS Pods were running. The EKS-only monitoring Helm
render passed the internal-only/no-PVC check, and its release installed. Two
demo Pods rolled out, one on each worker, with zero restarts at the early
check. Prometheus reported `UP` for both kubelet/cAdvisor endpoints and
kube-state-metrics. It returned nonempty CPU, memory, throttling, and Pod
identity series for the current workload.

The first UID-verified KubeFit readiness result was `collecting`, correctly
blocking patch eligibility with only two usage and throttling samples. Around
20 minutes into the fixed k6 profile, both sample counts had risen to 42;
readiness remained `collecting`, and both workload Pods plus Prometheus still
had zero restarts. The profile is a controlled local-to-Service port-forward
load, **not** an ingress or end-user latency benchmark.

## Where the one-hour experiment stopped

At about `10:41:19Z`, both Kubernetes API port-forwards reported lost Pod
connections. The demo tunnel began returning `connection refused`; the
Prometheus tunnel also stopped responding. Immediately afterward, read-only
Kubernetes checks still found both workers `Ready` and all demo/monitoring Pods
`Running` with zero restarts. This supports a tunnel/API-stream interruption,
not a demonstrated workload crash; its root cause was not established.

We stopped k6 rather than silently reconnecting or changing the preregistered
load profile. It had run for **34m47s**, not 60 minutes. Its aborted summary
reported 25,741 requests, 51 dropped iterations, and an 11.89% request error
rate. The `duration_minutes: 60` field in the summary identifies the configured
profile, **not the completed runtime**. These numbers are invalid as a
before/after performance result. The fixed spike and recovery phases never ran.

A short new Prometheus tunnel allowed a final UID-verified readiness check:
76 usage and 76 throttling samples, 62.3% observation coverage, and
`insufficient_data` / patch `blocked`. KubeFit therefore did **not** produce
an actionable EKS recommendation, YAML patch, PR, or cost-savings claim. The
partial result does establish that metrics arrived and the insufficient-data
gate stayed closed.

## Teardown and evidence boundary

No LoadBalancer, Ingress, or PVC was present. We deleted only the demo
manifests and `monitoring` Helm release. The refreshed Terraform removal plan
was `0 add, 0 change, 50 destroy`; its managed entries were all pure deletions.
Apply completed with all 50 destroyed. This time the managed node group deleted
in about 2m16s without manual Auto Scaling adjustment.

Afterward, Terraform state had no resources. AWS read-only inventory found no
Seoul EKS clusters, no instances in the pilot VPC, no EBS volumes, no pilot
EIP/Auto Scaling group/VPC, and no load balancer in that VPC. The exact NAT
gateway reported `deleted`. These checks establish that the experiment-owned
infrastructure was removed; AWS billing data may lag, so actual charge and
whether it stayed under the approved USD 5 are **not yet verified**.

The claim supported now is narrower than full end-to-end validation:

```text
EKS nodes Ready + add-ons + Prometheus targets UP + current-Pod metrics
    ✓ observed
One uninterrupted 60-minute controlled load + eligible recommendation
    ✗ not observed; port-forward failed, readiness blocked
Actual AWS bill savings
    ✗ not measured
```

Before another paid attempt, move sustained load off a single local
`kubectl port-forward` path (for example, a separately reviewed in-cluster
generator) and preregister its own capacity/cost impact. Do not quietly
substitute a shorter or lower-rate test and call it the same profile.
