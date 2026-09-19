# 0101 — Fail-closed EKS local preflight

## Why

The disposable EKS pilot had manual instructions to inspect a Terraform plan
and Helm render. A chart values edit could accidentally add a PVC or external
Service, and an operator could miss the change in a long manifest. The prior
``No changes`` check also needed a repeatable command.

## How and what

```text
clean local inputs
  ├─ Terraform workspace/fmt/validate/default plan ── must be 0 changes
  └─ Helm template 88.5.0 ── must be ephemeral, internal, and scrape-ready
                       any failure ──> no EKS operation
```

Added `python -m safety.eks_pilot_preflight`. It refuses auto-loaded Terraform
variables, non-default workspaces, and pre-existing local state; uses
`terraform plan -detailed-exitcode -refresh=false`; and parses the rendered
Helm YAML to reject PVCs, Ingress, externally exposed Services, or missing
Prometheus/cAdvisor/kube-state-metrics settings. The preflight deliberately
does not read or change the AWS account. It is a local consistency gate, not a
budget cap or live-cluster readiness proof.

## Verification and limits

- Twelve targeted tests passed, including deliberate PVC, LoadBalancer,
  missing-monitor, Terraform override, and existing-state mutations.
- Full Python suite: 555 passed (one existing Starlette deprecation warning).
- Ruff passed for the new code and tests.
- Local integrated preflight: `PASS — default plan has 0 changes; monitoring
  render is ephemeral and internal-only`.
- The first elevated local run timed out at the permission reviewer, not inside
  Terraform. One retry completed successfully.
- No AWS resource, Kubernetes workload, or Helm release was created. Live
  scrape health, node scheduling, and actual spend remain unverified.

## Next question

After explicit cost approval, does the *enabled* Terraform plan contain only
the reviewed EKS, VPC, worker, NAT, and root-volume resources, or are there
module-generated additions that change the estimate?
