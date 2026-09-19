# 0100 — EKS ephemeral observation plan

## Why

The local Prometheus configuration requires the kind `standard` StorageClass.
Copying it into a disposable EKS experiment could leave a Pending PVC or require
new EBS CSI and volume costs. The EKS Terraform draft also does not install
monitoring or a workload, so metric availability remained an untested gap.

## How and what

```text
kind values: 5 GiB persistent PVC ──x──> disposable EKS
                                      ↓
EKS values: 2 GiB emptyDir → kubelet/cAdvisor + kube-state-metrics
                                      ↓
                         UID check → controlled demo readiness
```

Added a separate values file for chart `88.5.0`, with a six-hour retention
window, 1 GB TSDB retention-size target, and a 2 GiB Pod-local `emptyDir`.
The observation runbook records context/collision checks, render inspection,
metric queries, stop conditions, and cleanup. It explicitly does not equate a
locally rendered chart with live EKS metrics or a cost-saving result.

## Verification and limits

- Local `helm template` for chart `88.5.0` and Kubernetes `1.34.0` succeeded.
- Rendered Prometheus custom resource has `storage.emptyDir.sizeLimit: 2Gi`.
- Rendered manifests contain kubelet `/metrics/cadvisor` and
  kube-state-metrics ServiceMonitors, with no PVC, Ingress, or LoadBalancer.
- No EKS cluster, Helm release, namespace, Pod, load, or cloud charge was
  created by this work. Live target health and actual scheduling remain unknown.

## Next question

Can an approved, time-boxed EKS experiment supply a full UID-consistent
one-hour window while staying inside the reviewed cost and teardown boundary?
