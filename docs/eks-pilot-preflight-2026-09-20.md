# EKS pilot read-only preflight — 2026-09-20

No AWS infrastructure was created or modified for this snapshot. The active
AWS CLI identity was checked, but its account number/ARN are intentionally not
committed here. The planned Region is `ap-northeast-2`; recheck account and
Region immediately before the actual plan and create decision.

| Read-only check | Observed on 2026-09-20 | Meaning / remaining risk |
|---|---|---|
| EKS clusters in Seoul | None | No name collision observed on this date; recheck before planning |
| EKS Kubernetes 1.34 | `STANDARD_SUPPORT` | Matches the draft; status can change later |
| `m6i.large` offerings | AZs `2a`, `2b`, `2c`, `2d` | Offering is not a capacity reservation |
| EKS cluster quota | 100 | Not a guarantee of permission to create |
| EC2 on-demand standard quota | 16 vCPU | Two planned workers need 4 vCPU; recheck usage |
| Running/pending EC2 instances | None | Capacity and other consumers can change |
| VPCs in Seoul | 1 of quota 5 | The new pilot VPC still needs an exact plan review |
| NAT gateways | 0 available/pending; quota 5 per AZ | One planned NAT still incurs hourly and per-GB charges |
| EC2-VPC Elastic IPs | 0 of quota 5 | One planned NAT address still needs a plan and cleanup review |

The AWS Price List Query API was also re-run for public on-demand Seoul prices:
`m6i.large` $0.118/hour, NAT Gateway $0.059/hour and $0.059/GB processed,
gp3 storage $0.0912/GB-month. AWS's public pages continue to list standard
EKS support at $0.10/cluster-hour and public IPv4 at $0.005/address-hour.
The candidate's static four-hour lower bound remains about **$1.62**, before
data processing, transfer, logs, image storage, taxes, mistakes, or any
unplanned charge. It is neither a bill forecast nor a spending cap. Sources:
[AWS Price List Query API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-price-list-query-api.html),
[EKS pricing](https://aws.amazon.com/eks/pricing/),
[VPC/IPv4 pricing](https://aws.amazon.com/vpc/pricing/).

The `kubectl`, `helm`, `terraform`, `aws`, `k6`, and Python tooling is installed
locally. The Prometheus Helm repository is configured. The current operator
public IPv4 `/32`, the experiment's UTC stop time, dollar ceiling, and cleanup owner
are **not** recorded; they must be supplied on the day. No enabled Terraform
plan or actual EKS scheduling/metric check has been performed. Follow the
[pilot worksheet](../deploy/eks-pilot/pilot-worksheet.md) rather than treating
this snapshot as authorization.
