# Disposable EKS compatibility pilot (not deployed)

This directory is a **reviewable infrastructure draft**, not an instruction to
launch a cluster. `enable_experiment` defaults to `false`; a default plan creates
**zero resources**. No Terraform `apply` has been run for this pilot.

The planned topology is one EKS 1.34 cluster in Seoul, two private-subnet
`m6i.large` managed nodes in two AZs, one NAT gateway with one public IPv4,
and a public Kubernetes API endpoint restricted to one operator IPv4 `/32`.
There is no public app LoadBalancer. The Terraform identity receives cluster
administrator access to install and remove the disposable monitoring stack;
this is **not** KubeFit's runtime permission model. The pilot omits optional
control-plane log delivery, customer-managed KMS, and IRSA. Do not copy this
minimal topology into production. Kubernetes 1.34 support and instance
availability must be rechecked immediately before any launch.

## Review path (read-only)

From this directory:

```sh
terraform init -backend=false
terraform fmt -check
terraform validate
terraform plan -input=false -refresh=false
```

The last command must say `No changes`. `terraform init` downloads public
modules/providers, not AWS infrastructure. The committed lock file pins the
provider versions; `.terraform/`, `*.tfvars`, and local state are ignored.

For one fail-closed local check of the default Terraform plan and rendered
monitoring topology, run this from the repository root after `terraform init`
and `helm repo add prometheus-community ...`:

```sh
.venv/bin/python -m safety.eks_pilot_preflight
```

The [preflight](../../safety/eks_pilot_preflight.py) refuses Terraform variable
overrides, auto-loaded tfvars, a non-default workspace, or an existing local
state. It requires a zero-change default plan and rejects a rendered PVC,
Ingress, external Service, missing cAdvisor/kube-state-metrics monitor, or
non-ephemeral Prometheus storage. It performs no AWS or Kubernetes writes. A
PASS is **not** permission to enable the experiment, and it cannot verify AWS
inventory, live scrape targets, capacity, rates, or teardown.

Before a non-default plan, complete the [decision gate](../../docs/eks-validation-plan.md):
explicit charge approval, exact account and Region, current rate sheet,
approved total budget and wall-clock deadline, assigned cleanup owner, current
public `/32` address, cluster-name collision check, and a review of **every**
planned resource. The four-hour estimate in that document is a dated lower
bound, not a budget limit. The Terraform budget and stop-time fields are
recorded approvals, **not automatic cost enforcement or teardown**. Never put
credentials in `tfvars` or commit a plan/state file.

The two modules and provider versions are pinned in [`main.tf`](main.tf). A
future approved operator can supply the required inputs via an ignored local
`*.tfvars` file and inspect `terraform plan -var-file=...`; simply setting
`enable_experiment=true` without a matching account, a valid `/32`, a named
owner, a positive budget, and a valid UTC `stop_at_utc` is blocked. The stop
time must be in the future and within four hours **when the plan is made**;
an apply-time check rejects a saved plan if that deadline has passed. Terraform
does not stop a running create operation or automatically destroy resources at
that time. The operator must reserve teardown time inside the same window and
stop manually. The account check does not verify that the chosen name is
unused, that funding exists, or that the deadline is operationally enforced.
Do not run `apply` from this README without separate approval and the complete
runbook.

## Teardown boundary

If an experiment is ever approved, keep its state until teardown is verified.
Remove any experiment-created Kubernetes LoadBalancer/Ingress resources before
cluster deletion. Review a destroy plan **for this state only**, then verify
the exact experiment-owned EKS cluster, node group, EC2 instances, volumes,
NAT gateway, Elastic IP, and any manually created monitoring resources are
gone. Do not interpret a successful destroy exit code as proof of zero charges.

No Helm release, Prometheus PVC, workload, or load generator is provisioned by
this Terraform draft. Those must be separately planned and accounted for; the
existing local Prometheus values use the kind `standard` StorageClass and are
**not** EKS-ready. An ephemeral Prometheus deployment would lose metrics on
restart, so a restarted observation window must start over. The
[monitoring runbook](monitoring-runbook.md) and [EKS-only values](prometheus-values.yaml)
provide a reviewable, unexecuted observation path after separate approval.
