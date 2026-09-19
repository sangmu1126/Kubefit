# 0099 — Disabled EKS infrastructure draft

## Why

The Seoul cost estimate named a plausible EKS experiment but left the network,
node, endpoint, and Terraform state boundaries implicit. A reviewer could not
compare the estimate with actual planned resources before any AWS spend.

## How and what

```text
default enable_experiment=false ──> 0 AWS resources
approved account + /32 + budget + owner + deadline
                         └──────> review-only non-default plan
                                  └──────> separate approval before apply
```

Added a pinned VPC/EKS Terraform draft with two private managed nodes, one
NAT gateway, and a single-IP API allowlist. The gate checks the selected AWS
account and required operator inputs; it does **not** enforce a spending cap,
stop the clock, verify capacity, or clean up. Local state and operator input
files are ignored; the provider lock file is tracked. The runbook makes the
remaining Helm, workload, and teardown work explicit.

## Verification and limits

- `terraform init -backend=false -input=false`: succeeded; downloaded pinned
  modules and providers only.
- `terraform fmt -check`: passed.
- `terraform validate -no-color`: passed.
- `terraform plan -input=false -refresh=false -no-color -lock=false` with default
  variables: **No changes**.
- The first sandboxed validate could not launch provider processes; the same
  read-only validation succeeded when provider execution was allowed.
- No `apply`, EKS resource creation, monitoring deployment, or real workload
  validation was performed. The candidate remains subject to the decision gate.

## Next question

If a short EKS pilot receives explicit cost and teardown approval, what is the
smallest reproducible Prometheus/workload deployment that yields UID-consistent
metrics without retaining a chargeable volume?
