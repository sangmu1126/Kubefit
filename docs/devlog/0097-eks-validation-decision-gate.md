# 0097: EKS validation decision gate

- **Date:** 2026-09-19
- **Status:** planned; no AWS resources created
- **Related phase:** post-MVP EKS compatibility
- **Change type:** documentation-only decision gate

## Why

The current account has no EKS cluster in the selected Region. A live pilot could
prove integration, but the cluster, nodes, storage, and networking incur separate
charges. A short synthetic experiment also cannot support a production savings
claim. Provisioning needs an explicit claim and cleanup boundary first.

## Success criteria

- Distinguish an existing-workload analysis from a disposable compatibility test.
- Require time, budget, resource inventory, and cleanup ownership before creation.
- Define evidence and stop conditions without creating any AWS resource.
- Avoid treating AWS Budgets notifications as a real-time spending cutoff.

## What changed and how

The [EKS validation decision gate](../eks-validation-plan.md) records two routes,
a cost worksheet, pre-provisioning GO/NO-GO checks, stop conditions, and an
inventory-based teardown rule. It deliberately contains no `apply`, cluster-create,
or broad delete command.

```mermaid
flowchart LR
    C[Claim to prove] --> B[Approved budget and stop time]
    B --> P[Reviewed provisioning plan]
    P --> E[Bounded EKS experiment]
    E --> D[Exact teardown and inventory verification]
    D --> R[Redacted evidence and actual-cost review]
```

The sequence prevents the experiment itself from becoming an unbounded operational
liability. It is not proof that EKS compatibility already works.

## Problems encountered

The one-hour controlled profile is not interchangeable with a seven-day production
observation. A temporary cluster can establish connectivity and synthetic
compatibility, but not representative savings. Budget alerts also lag billing data,
so the plan relies on a wall-clock stop and verified deletion.

## Evidence

The AWS EKS pricing and deletion documentation and AWS Budgets update-frequency
documentation were checked on 2026-09-19. Local checks: `git diff --check`.
No provisioning or deletion command was executed.

## Decision and limitations

The project is ready to **decide whether** an EKS experiment is worth its cost,
not to run one. Node type, network architecture, maximum duration, budget, and
owner remain unset. Until those are explicit and approved, the correct state is
NO-GO.

## Next question

Is there an existing EKS workload with monitoring access, or should a small,
time-boxed synthetic compatibility pilot be budgeted separately?
