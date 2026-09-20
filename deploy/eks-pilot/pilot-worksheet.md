# EKS pilot day worksheet — not an approval to launch

Use this on the day of the **disposable, non-production** experiment. The
[2026-09-20 read-only snapshot](../../docs/eks-pilot-preflight-2026-09-20.md)
is preparation, not a guarantee of the account state on the experiment day. This worksheet
separates planning, explicit creation approval, live observation, and teardown.
Do not run `terraform apply` merely because a local preflight or plan audit passes.

## Decision fields (fill privately before any create command)

| Field | Required decision |
|---|---|
| AWS account/Region | Exact account from `sts`; `ap-northeast-2` only |
| Cluster name | Unique `kubefit-eks-pilot...`; confirm absent in EKS |
| Operator | Named person responsible for *both* creation and teardown |
| Public API CIDR | Operator's **current** public IPv4 `/32`; recheck on the day |
| Stop time | UTC RFC3339, future and within four hours **at plan time** |
| Total spend ceiling | Operator-approved USD amount **above** refreshed whole-environment estimate |
| Evidence claim | EKS compatibility with synthetic traffic, **not** bill savings |

The budget field is recorded approval, not a hard AWS spending limit. Provisioning,
the one-hour traffic profile, and teardown must all fit before the stop time. If
the remaining time cannot fit the experiment, skip or shorten the **scope**, not
the pre-registered load profile or cleanup. Obtain explicit approval for both
maximum spend and the final create plan; this document supplies neither.

## A. Recheck and generate a plan — reads only

From the repository root, confirm the exact AWS identity, Region, available
cluster name, instance offering/quotas, and current public IP. An EKS list that
is no longer empty requires investigation. Run the disabled preflight first.
No `apply`, `helm install`, or Kubernetes write is part of this section.

```sh
aws sts get-caller-identity
aws configure get region
aws eks list-clusters --region ap-northeast-2
aws ec2 describe-instance-type-offerings --region ap-northeast-2 \
  --location-type availability-zone \
  --filters Name=instance-type,Values=m6i.large
curl -fsS https://checkip.amazonaws.com
.venv/bin/python -m safety.eks_pilot_preflight
```

Use the refreshed [whole-environment estimate](../../docs/eks-validation-plan.md)
and a private, Git-ignored `.kubefit/eks-pilot/approval.tfvars`. Set
`enable_experiment=true`, the unique cluster name, owner tag, exact approved
account ID, current `/32`, positive approved budget, and UTC stop time. Do not
put credentials in the file. The date/time gate is checked during planning and
again when a saved creation plan begins applying; it **does not terminate a
running apply** or delete resources at the deadline.

After the decisions are recorded, save a binary plan **only under the ignored
`.kubefit/` directory**. A plan file may contain sensitive values, so do not
upload or commit it. `terraform show -json` is streamed into the auditor; no
second JSON copy is written.

```sh
mkdir -p .kubefit/eks-pilot
terraform -chdir=deploy/eks-pilot plan \
  -var-file=../../.kubefit/eks-pilot/approval.tfvars \
  -out=../../.kubefit/eks-pilot/create.tfplan
terraform -chdir=deploy/eks-pilot show -json \
  ../../.kubefit/eks-pilot/create.tfplan | \
  .venv/bin/python -m safety.eks_pilot_plan_audit
terraform -chdir=deploy/eks-pilot show \
  ../../.kubefit/eks-pilot/create.tfplan
```

The auditor requires one cluster, one two-node on-demand `m6i.large` node
group, three EKS add-ons (`vpc-cni`, `kube-proxy`, `coredns`), one VPC, four
reviewed subnets, one NAT gateway, and one public IPv4.
It rejects deletion/replacement, unknown resource types, broad API access,
drift, and nonempty managed state. It **does not** prove current prices,
available EC2 capacity, IAM sufficiency, service health, or an AWS spending cap.
Read the full `terraform show` output and compare every managed resource with
the estimate. Any unexpected resource or failed check is **NO-GO**.

## B. Separate creation approval

Only after a person approves the *exact saved plan* and the remaining time is
sufficient may the operator run the following. This is a **chargeable write**
and is intentionally not executed by repository automation:

```sh
terraform -chdir=deploy/eks-pilot apply \
  ../../.kubefit/eks-pilot/create.tfplan
```

If the deadline or current public IP changed, discard the saved plan, recheck
the inputs, and generate a new plan. Do not apply a stale plan. Retain the
local state until every resource has been verified as removed.

## C. Observe, then stop by the deadline

Use a private kubeconfig and the exact `kubefit-eks-pilot` context as shown in
the [read-only EKS pilot](../../docs/eks-pilot.md). Follow the
[monitoring and workload runbook](monitoring-runbook.md): inspect nodes,
install the EKS-only Prometheus values and synthetic Deployment, confirm
kubelet/cAdvisor and kube-state-metrics targets, run UID-verified readiness,
and generate the one-hour controlled traffic **only if cleanup still fits**.
Do not turn this into a production savings claim. Record start/stop times and
non-secret evidence privately.

With the Prometheus port-forward on `19090`, inspect the JSON before any
analysis. `collecting` is expected until the full controlled hour and sample
coverage are present; `blocked` or a Pod UID mismatch ends the attempt.

```sh
.venv/bin/kubefit readiness \
  --context kubefit-eks-pilot --namespace kubefit-demo \
  --deployment overprovisioned-api --container api \
  --prometheus-url http://127.0.0.1:19090 \
  --verify-pod-uid-source --observation-profile demo \
  --identity-store .kubefit/eks-pilot/identities.json \
  > .kubefit/eks-pilot/readiness.json
```

If eligible, a **clearly labeled illustrative** request-cost analysis can use
the refreshed `$0.118/hour` node rate split 50/50 between its 2 vCPU and
8 GiB: `$0.0295/core-hour` and `$0.007375/GiB-hour`. This allocation is a
modeling choice, not an AWS billing rate. It excludes EKS, NAT, EBS, and
transfer costs; lowering Pod requests on fixed two-node capacity need not
lower the actual bill. The default 730-hour projection is hypothetical, not
this four-hour experiment's invoice.
Recheck the node rate on the experiment day; if it changes, recalculate both unit rates
and replace the date in `--price-source` before running this example.

```sh
.venv/bin/kubefit analyze \
  --context kubefit-eks-pilot --namespace kubefit-demo \
  --deployment overprovisioned-api --container api \
  --prometheus-url http://127.0.0.1:19090 \
  --verify-pod-uid-source --observation-profile demo \
  --identity-store .kubefit/eks-pilot/identities.json \
  --cluster-label eks-seoul-pilot --metrics-source-label ephemeral-prometheus \
  --cpu-core-hour-usd 0.0295 --memory-gib-hour-usd 0.007375 \
  --price-source '2026-09-20 Seoul m6i.large 50-50 node-rate allocation' \
  > .kubefit/eks-pilot/analysis.json
```

## D. Teardown is mandatory, even after a failed test

Stop traffic. Check for unexpected LoadBalancer Services/Ingresses, then remove
only this experiment's demo manifests and monitoring release using the fixed
context. Do **not** use a broad account-wide deletion command. If the deadline
has already passed, do not extend the observation: proceed directly to cleanup.

```sh
kubectl --context kubefit-eks-pilot get svc,ingress --all-namespaces
kubectl --context kubefit-eks-pilot delete -f deploy/demo --ignore-not-found
helm --kube-context kubefit-eks-pilot uninstall monitoring --namespace monitoring
```

Review a **destroy-only** plan for the same local Terraform state and exact
experiment resources. `enable_experiment=false` removes the creation gate and
all pilot modules from desired state, so an expired creation deadline must not
be used as a reason to skip cleanup. The destroy plan is not audited by the
create-plan tool; review all its addresses and actions manually.
The expired-gate path was rehearsed only with a local `terraform_data` resource;
real EKS/VPC teardown is still unproven until this pilot is actually removed.

```sh
terraform -chdir=deploy/eks-pilot plan \
  -var='enable_experiment=false' \
  -out=../../.kubefit/eks-pilot/destroy.tfplan
terraform -chdir=deploy/eks-pilot show \
  ../../.kubefit/eks-pilot/destroy.tfplan
# Apply the exact destroy plan only after confirming it targets this pilot state.
terraform -chdir=deploy/eks-pilot apply \
  ../../.kubefit/eks-pilot/destroy.tfplan
```

Verify the named EKS cluster/node group, EC2 instances, node volumes, NAT
gateway, Elastic IP, and any manually added resources are gone using the
[teardown inventory](../../docs/eks-validation-plan.md). A successful Terraform
exit code is not sufficient. Keep the state until these checks complete, then
record actual charges when billing data becomes available. If cleanup fails,
stop all further tests and resolve the exact remaining resource IDs.
