# EKS validation decision gate

**Status: planning only. No cluster is authorized for creation by this document.**
The [disposable Terraform draft](../deploy/eks-pilot/README.md) is disabled by
default and has no deployment authorization. It covers VPC and EKS only; the
Prometheus/workload installation and teardown need a separate reviewed runbook.
On 2026-09-19, the selected AWS account returned an empty EKS cluster list in
`ap-northeast-2`. That observation says nothing about other Regions or accounts.
The [2026-09-20 read-only preflight](eks-pilot-preflight-2026-09-20.md) refreshed
the planned Region's version, instance offering, quotas, and public rate inputs;
none of those checks authorizes provisioning or guarantees capacity tomorrow.

## Which claim are we trying to prove?

| Route | Environment | Evidence it can support | Evidence it cannot support |
|---|---|---|---|
| Existing-cluster pilot | An authorized, already monitored EKS workload | Read-only compatibility and a representative recommendation, if the observation window is sufficient | Bill savings or safe production rollout without further validation |
| Disposable compatibility pilot | One temporary EKS cluster, synthetic workload, local Prometheus | EKS connectivity, current-Pod UID matching, and a controlled short-window analysis | Real-user traffic, multi-day production recommendation, or actual AWS savings |

The disposable route is useful **before** the final project review, not as the first
operation on submission day. Run it once after the local path is stable, fix any
integration defects, then repeat only the necessary final checks. Do not keep EKS
running between tests.

## GO/NO-GO checklist before provisioning

All items must be explicit. An unknown price or cleanup owner means **NO-GO**.

- [ ] The operator has chosen the route, Region, account, cluster name, and a
  non-production workload. No existing cluster shares the planned name.
- [ ] A hard **wall-clock stop time** includes provisioning, observation, and cleanup.
  Someone is assigned to perform and verify cleanup even if the experiment fails.
  The Terraform draft rejects a deadline outside the next four hours at plan
  time and a stale deadline at apply time; it cannot terminate an ongoing
  operation or perform cleanup automatically.
- [ ] The architecture is written down: node type/count, storage, network, load
  balancers, Prometheus choice, and any NAT gateway or public IPv4 use.
- [ ] An AWS Pricing Calculator estimate or equivalent rate sheet is saved with its
  date. Estimate the **whole environment**, not only the EKS control plane.
- [ ] The maximum duration multiplied by the summed hourly rates, plus a buffer for
  one-time/variable charges, is below the operator's approved experiment budget.
- [ ] Creation uses one reviewed infrastructure plan with a known state location;
  no secrets or Terraform state are committed to Git.
- [ ] The rollback/teardown instructions have been rehearsed on disposable resources
  or reviewed against the exact planned architecture.
- [ ] The evidence claim is registered in advance: compatibility, not production
  savings. The temporary pilot uses the `demo` observation profile only with a
  controlled load and labels the result non-production.

| Cost component | Rate source to record | Included? |
|---|---|---|
| EKS cluster support tier | [EKS pricing](https://aws.amazon.com/eks/pricing/) | Required |
| Worker compute and count | EC2/Fargate price for selected Region and type | Required |
| EBS and snapshots | Selected volume/storage pricing | If used |
| Load balancers and data transfer | Selected ELB/network pricing | If used |
| NAT gateway and public IPv4 | Selected VPC pricing | If used |
| Prometheus/AMP, logs, image registry | Selected service pricing | If used |

Estimate: `maximum hours × total hourly rate + variable charges + buffer`.
This is a **spending plan**, not a hard AWS spending cap. AWS Budgets is useful as
an additional alert, but its billing data is refreshed at least daily, so it cannot
stop a short experiment in real time. Use the wall-clock stop and human cleanup as
the primary control. [AWS Budgets update frequency](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-best-practices.html)

### Dated candidate for a short Seoul compatibility pilot

This is a **candidate for review, not approval to provision**. The local demo has
two Pods requesting 1 vCPU and 2 GiB each. Two `m6i.large` workers (2 vCPU, 8 GiB
each) are a plausible starting point for those Pods plus a small Prometheus stack,
but actual allocatable capacity and scheduling must still be checked. The proposed
network uses private workers and one NAT gateway; it does not create an external
LoadBalancer. A temporary Prometheus data directory would avoid an EBS CSI/PVC
dependency, but a Prometheus restart would invalidate the observation window.

On 2026-09-19 the AWS Price List Query API returned the following **public
on-demand** Seoul rates. The EKS and IPv4 rates come from the linked AWS pricing
pages. Discounts, credits, taxes, and data transfer are not included.

| Candidate component | Rate | Four-hour subtotal |
|---|---:|---:|
| One standard-support EKS cluster | $0.100/hour | $0.400 |
| Two `m6i.large` Linux workers | 2 × $0.118/hour | $0.944 |
| One NAT gateway | $0.059/hour | $0.236 |
| One NAT public IPv4 address | $0.005/hour | $0.020 |
| Two 20 GB gp3 node root volumes | 40 GB × $0.0912/GB-month ÷ 730 | ~$0.020 |
| **Static subtotal** | **~$0.405/hour** | **~$1.620** |

The estimate is a **lower bound**, not a cap: NAT processing is $0.059/GB in the
queried Seoul price list, and cross-AZ/internet transfer, logs, extra volumes,
image storage, and setup mistakes can add charges. NAT gateway partial hours are
billed as full hours. Re-query rates and review the exact infrastructure plan before
any create command. Sources: [EKS pricing](https://aws.amazon.com/eks/pricing/),
[VPC/NAT/IPv4 pricing](https://aws.amazon.com/vpc/pricing/),
[EBS pricing](https://aws.amazon.com/ebs/pricing/), and
[AWS Price List Query API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-price-list-query-api.html).

The **four-hour window is only a proposed maximum**: provisioning and teardown use
part of it, leaving roughly one controlled observation hour. If readiness remains
ineligible, stop rather than extending the window automatically. A person must
approve the maximum duration, overall budget, network layout, and cleanup owner
before implementing or applying EKS infrastructure.

## Experiment and stop conditions

1. Before any create command, record the approved plan, state location, start time,
   stop time, resource tags, and expected inventory. Run a plan/preview and inspect
   **every** resource it would create. Any unexpected chargeable resource is NO-GO.
2. If approved later, create only the named disposable environment. Put KubeFit's
   CLI outside EKS. Do not install a controller or grant Deployment write access.
3. Confirm Prometheus scrapes kube-state-metrics and kubelet/cAdvisor, then run
   `readiness` with `--verify-pod-uid-source`. A UID mismatch, missing metric,
   unexpected context, or incomplete coverage ends the experiment without a PR.
4. If using the one-hour `demo` profile, generate deliberate traffic and keep stable
   replicas long enough to satisfy its sample and coverage gates. Do not reinterpret
   an ineligible result as a production recommendation.
5. Stop by the registered time regardless of PASS/FAIL. Preserve only redacted
   analysis, timestamps, test outputs, and the actual AWS charges when available.

## Teardown verification

Deletion is complete only after inventory checks, not merely when a destroy command
returns. Before deleting the cluster, identify Kubernetes `LoadBalancer` Services and
Ingress resources; AWS warns they can leave load balancers if not removed first.
After teardown, verify the planned cluster and managed node groups are gone and
inspect the experiment's tags/state for remaining EC2 instances, volumes, load
balancers, NAT gateways, Elastic IPs, and monitoring resources. AMP scrapers and
workspaces, if used, have their own lifecycle. Do not run broad deletion commands
against an account or VPC without resolving the exact experiment-owned IDs.
[AWS EKS deletion guide](https://docs.aws.amazon.com/eks/latest/userguide/delete-cluster.html)

If any item remains or ownership is unclear, stop automation and resolve it
manually. Record the final inventory and later compare the bill with the estimate.
Even a clean teardown does not prove that KubeFit reduced an AWS invoice; that
requires a separate controlled before/after cost study.
