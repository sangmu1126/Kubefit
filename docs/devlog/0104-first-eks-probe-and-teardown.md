# 0104 — First live EKS probe: node readiness failed; teardown verified

Date: 2026-09-20, Seoul Region. This was a short, explicitly approved,
non-production compatibility probe, **not** a KubeFit cost-saving result.
The operator approved a USD 5 spending ceiling and a UTC cleanup deadline;
neither is an AWS-enforced spending cap. Actual billed cost is not yet known.

## Why

The local kind cluster cannot prove that the same Terraform, AWS network,
managed nodes, Prometheus, and KubeFit metric path work on EKS. We planned one
disposable cluster with two on-demand `m6i.large` workers. That size supplies a
non-burstable 2-vCPU/8-GiB baseline per node for a later controlled comparison;
it was **not** demonstrated to be the minimum or cheapest size for this brief
smoke test. T-family workers would introduce CPU-credit behavior to a longer
performance comparison. A cheaper smoke-only topology remains a separate
decision and has not been validated.

## What happened

```text
46-resource creation plan (0 change/delete), audited
        ↓
EKS control plane and two EC2 workers created
        ↓
Kubernetes nodes registered but stayed NotReady
        ↓
EKS add-ons = []; kube-system Pods = []
        ↓
No Prometheus, sample app, traffic, or KubeFit analysis was run
        ↓
46-resource destroy-only plan applied
        ↓
NAT/EIP deleted; managed node group deletion delayed
        ↓
Exact pilot Auto Scaling group scaled to 0
        ↓
Both worker instances terminated; EKS/VPC destroyed
        ↓
Terraform state empty; AWS inventory rechecked
```

The repository's EKS module configuration had no EKS managed add-ons. In this
run, `aws eks list-addons` returned an empty list, `kubectl get pods -A` returned
none, and both nodes remained `NotReady`. Missing VPC CNI is the most direct
explanation for node readiness failure; we did not collect kubelet logs, so
this is a supported diagnosis rather than proof that no other defect exists.
The live KubeFit ingestion path therefore remains **unverified**.

The first creation plan failed locally because module-wide `depends_on` deferred
EKS module data sources until apply, producing an invalid `count`. Removing
only the EKS module-wide dependency allowed a complete 46-resource plan; the
VPC module retained its approval-gate dependency. The revised EKS module
instead references the gate ID in a tag input, which keeps its data sources
plan-readable while making AWS writes wait for the gate. The auditor now
checks both references. The plan auditor also
needed to recognize the pinned module's one exact validation `null_resource`.
These were plan/configuration defects; no AWS resources existed at that stage.

Teardown itself exposed a reliability issue: the managed node group stayed in
`DELETING` while its exact Auto Scaling group still had min/max/desired size 2.
After confirming the group and two instance IDs belonged to this pilot, we
set only that group to 0/0/0. The instances moved to termination and eventually
became `terminated`. A later attempt to complete one termination lifecycle
hook returned “no active Lifecycle Action”; by then that instance had already
left the group, so no further hook override was made. Terraform then completed
the 46 deletions. This is a **manual recovery**, not proof that unattended
teardown succeeds.

## Evidence and limits

After destroy, read-only checks found: EKS cluster list empty; neither worker
pending/running/stopped; the pilot NAT in `deleted` state; no pilot EIP, VPC,
security group, EBS volume, or Auto Scaling group; Terraform state empty.
AWS billing data can lag, so these checks do not establish the exact charge.
There was no valid CPU/memory recommendation, before/after comparison, or
Prometheus target-health result from this probe.

## Correction before another paid attempt

The Terraform draft now declares managed `vpc-cni` and `kube-proxy` before
compute, plus `coredns` after compute, all with `preserve = false`. The
create-plan auditor requires these exact three add-ons and rejects their
preservation. Local Terraform validation, a default no-change plan, and a
read-only revised 50-resource creation plan passed. **That revised plan was not
applied**; actual node readiness and add-on ordering remain unverified.

Do not reuse the old saved plan or expired approval values for another run.
Recheck identity, public IP, stop time, prices, and the complete plan; obtain
fresh approval before any future `apply`. The default preflight intentionally
refuses a previously used local state directory even after its resource list is
empty; this run instead verified `terraform state list` was empty and the
disabled plan had zero changes.

References: [EKS node-group deletion](https://docs.aws.amazon.com/eks/latest/userguide/delete-managed-node-group.html),
[EC2 CPU credits](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-credits-baseline-concepts.html).
