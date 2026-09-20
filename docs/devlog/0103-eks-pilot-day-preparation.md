# 0103 — EKS pilot day preparation

## Why

The disposable EKS draft, monitoring values, and disabled preflight were ready,
but the experiment day still required many scattered decisions. An enabled
Terraform plan could contain additional module resources or a changed node/NAT
count that a reviewer might overlook. Teardown also needed an explicit path
that remained usable after the creation deadline expired.

## How and what

```text
today: read-only account/price/quota checks + local tests
        ↓
tomorrow: fresh operator decisions → saved create plan → structural audit
        ↓
        human review + separate spend approval → apply (only if authorized)
        ↓
        metric evidence or early stop → destroy-only plan → inventory check
```

Added a single pilot worksheet and a `terraform show -json` create-plan auditor.
The auditor requires an empty prior managed state, create-only actions, the
reviewed one-cluster/two-node/one-NAT/four-subnet topology, one IPv4 `/32`
endpoint allowlist, and no unknown managed resource type. It summarizes every
planned managed resource type without storing another JSON copy. It does **not**
approve the spend or replace reading the entire plan. Binary and JSON plan
artifacts are ignored by Git.

The worksheet separates read-only planning from chargeable creation, gives
exact workload/metric commands, and reserves a destroy-only path. Its example
cost rates split one `m6i.large` node price 50/50 between CPU and memory; this
is an explicitly illustrative request-allocation model, not an AWS invoice.

## Read-only observations on 2026-09-20

- Selected AWS identity/Seoul Region checked privately; no account ID or ARN
  committed. `aws eks list-clusters` returned an empty list in Seoul.
- EKS 1.34 reported `STANDARD_SUPPORT`. `m6i.large` was offered in Seoul AZs
  2a/2b/2c/2d; offering does not guarantee spare capacity.
- EKS cluster quota 100; standard on-demand EC2 quota 16 vCPU; zero running or
  pending EC2 instances; one VPC of quota five currently exists in Seoul.
  NAT gateway usage was zero against a quota of five per AZ; EIP usage was
  zero against a quota of five. The candidate
  needs 4 vCPU across two worker nodes. Permissions and availability at
  creation time remain unproven.
- AWS Price List Query API returned the same public rates as the prior probe:
  `m6i.large` $0.118/hour, NAT $0.059/hour plus $0.059/GB processed, and gp3
  $0.0912/GB-month. Official [EKS pricing](https://aws.amazon.com/eks/pricing/)
  and [VPC pricing](https://aws.amazon.com/vpc/pricing/) still list $0.10 per
  standard-support cluster-hour and $0.005 per public IPv4-hour. Static four-
  hour subtotal remains about $1.62 **before** variable charges and buffer.
- Local Python, Terraform, Helm, kubectl, AWS CLI, and k6 tools were present;
  Prometheus Helm repository was configured.

## Verification and limits

- New plan-auditor tests: 18 passed (CLI input, unexpected LoadBalancer/KMS/log group,
  extra NAT, replacement/deletion, broad CIDR, node count, state, and incomplete
  plan cases). Full Python suite: **573 passed**, one existing Starlette warning.
  Ruff and `git diff --check` passed.
- Disabled Terraform/Helm preflight: PASS, zero planned changes and ephemeral,
  internal monitoring render.
- Saved a **disabled, zero-change** Terraform plan in temporary local storage
  and inspected its real JSON schema: format `1.2`, `complete=true`,
  `applyable=false`, `prior_state=null`, and no resource changes. The auditor
  was corrected to accept `prior_state=null` for a fresh state, while still
  rejecting a disabled/no-op plan. No enabled EKS plan was produced.
- In a local-only temporary `terraform_data` fixture, an enabled gate created
  one local resource. With an expired timestamp and `enabled=false`, Terraform
  planned exactly one destroy and completed it. This supports the worksheet's
  cleanup path, but **does not** test an EKS/VPC destroy.
- No EKS enabled plan, AWS create/write, Helm installation, traffic run, or
  live metric validation happened. Tomorrow's public IP, approved spend,
  stop time, owner, exact plan, and teardown remain undecided.

## Next question

After the operator records the private approvals tomorrow, does a fresh
enabled plan match the reviewed inventory and leave enough wall-clock time for
both the controlled observation and verified teardown?
