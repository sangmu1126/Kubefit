# Disposable EKS observation runbook

**Partially exercised; full load profile failed.** The [second EKS probe](../../docs/devlog/0105-second-eks-pilot.md)
confirmed Pod metrics and UID-verified partial readiness, but both local
port-forwards lost their EKS API stream before the one-hour load finished.
Follow this only after the [EKS GO/NO-GO gate](../../docs/eks-validation-plan.md)
and a separately approved infrastructure plan. It applies only to the new,
experiment-owned cluster; it must not install into an existing production
cluster. KubeFit itself stays outside EKS and only reads the selected workload.

```text
approved EKS cluster
  ├─ kube-prometheus-stack: kubelet/cAdvisor + kube-state-metrics
  ├─ kubefit-demo/overprovisioned-api: two synthetic Pods
  └─ local KubeFit CLI: UID check → readiness → analysis
```

The [EKS values](prometheus-values.yaml) retain at most six hours of metrics
on a 2 GiB Pod-local `emptyDir`; Prometheus Pod replacement loses the entire
history. They create neither a PVC nor an external LoadBalancer, but worker
root volumes, NAT data processing, and image pulls still cost money. An EKS
StorageClass/EBS CSI add-on is not needed for this particular Prometheus setup.
This is deliberately less durable than the kind development environment.
[Prometheus Operator storage documentation](https://prometheus-operator.dev/docs/platform/storage/)
describes the default ephemeral behavior.

## 1. Local render check, before any cluster write

From the repository root, `helm` must already have the `prometheus-community`
repository configured. The chart version is the same pinned `88.5.0` used by
the local path. Render it with the EKS values and inspect the Prometheus custom
resource, kubelet `/metrics/cadvisor` ServiceMonitor, and kube-state-metrics
ServiceMonitor. The rendered manifests must contain **no** PersistentVolumeClaim,
Ingress, or `type: LoadBalancer`. `helm template` does not contact the cluster.

```sh
helm template monitoring prometheus-community/kube-prometheus-stack \
  --version 88.5.0 --namespace monitoring --kube-version 1.34.0 \
  --values deploy/eks-pilot/prometheus-values.yaml
```

Re-render with the exact chart and Kubernetes versions selected in the approved
plan. A successful local render does not prove that EKS scrape targets are UP.
The repository's [local preflight](README.md) automates these rendered-manifest
checks alongside the disabled Terraform plan; run it before considering a
non-default plan.

## 2. Preflight the exact context

Use a private, ignored `KUBECONFIG` under `.kubefit/eks-pilot`, as described in
the [read-only EKS pilot](../../docs/eks-pilot.md). Verify the selected AWS account,
Region, cluster name, Kubernetes context, cleanup deadline, remaining time, and
available node capacity. Confirm there is **no** existing `monitoring` Helm
release or `kubefit-demo` namespace in this newly created cluster. A collision
is a stop condition, not a reason to overwrite someone else's resources.

```sh
aws sts get-caller-identity
kubectl --context kubefit-eks-pilot cluster-info
kubectl --context kubefit-eks-pilot get nodes -o wide
helm --kube-context kubefit-eks-pilot list --namespace monitoring --all
kubectl --context kubefit-eks-pilot get namespace kubefit-demo
```

`get namespace` returning NotFound is expected. A different context, a preexisting
release/namespace, insufficient allocatable CPU or memory, or too little time for
observation **and** teardown means stop. The demo's two replicas request a total
of 2 vCPU and 4 GiB before monitoring and EKS system Pods are added; two
`m6i.large` nodes are a hypothesis, not a scheduling guarantee.

## 3. Install only after explicit approval

These commands **write to EKS and incur charges**; they are not part of the
current local validation. Keep the fixed context in every command.

```sh
helm --kube-context kubefit-eks-pilot upgrade --install monitoring \
  prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace --version 88.5.0 \
  --values deploy/eks-pilot/prometheus-values.yaml --wait --timeout 10m

kubectl --context kubefit-eks-pilot apply -f deploy/demo
kubectl --context kubefit-eks-pilot rollout status \
  deployment/overprovisioned-api -n kubefit-demo --timeout 5m
```

On `Pending`, `ImagePullBackOff`, missing nodes, or a failed Helm install, stop
the observation and begin teardown within the approved time; do not add nodes,
volumes, or a public LoadBalancer ad hoc.

## 4. Prove that the required metrics are from this cluster

```sh
kubectl --context kubefit-eks-pilot get pods -n monitoring
kubectl --context kubefit-eks-pilot get pods -n kubefit-demo -o wide
kubectl --context kubefit-eks-pilot get pvc --all-namespaces
kubectl --context kubefit-eks-pilot -n monitoring port-forward \
  service/monitoring-kube-prometheus-prometheus 19090:9090
```

The port-forward runs in its own terminal. `get pvc` should not show a PVC
created by this monitoring release. In the Prometheus UI at `localhost:19090`,
check Target health for kubelet/cAdvisor and kube-state-metrics. Query these
series for the **current** demo Pods; each must return a nonempty result:

```promql
container_cpu_usage_seconds_total{namespace="kubefit-demo",container="api"}
container_memory_working_set_bytes{namespace="kubefit-demo",container="api"}
container_cpu_cfs_throttled_periods_total{namespace="kubefit-demo",container="api"}
kube_pod_info{namespace="kubefit-demo",pod=~"overprovisioned-api-.*"}
kube_pod_owner{namespace="kubefit-demo",pod=~"overprovisioned-api-.*"}
```

Then run the [read-only UID-verified readiness command](../../docs/eks-pilot.md)
against `http://127.0.0.1:19090`. An initially `collecting` result is normal:
the `demo` profile needs a full controlled one-hour window and enough samples.
Use `--observation-profile demo` **only** for this synthetic workload. If the
deadline leaves enough time for observation **and cleanup**, port-forward the
demo Service in another terminal and run the fixed-load profile from the local
machine, recording its start time:

```sh
kubectl --context kubefit-eks-pilot -n kubefit-demo port-forward \
  service/overprovisioned-api 18080:80
```

```sh
KUBEFIT_TARGET_URL=http://127.0.0.1:18080/ \
  k6 run benchmarks/k6/observation_profile.js
```

If there is not enough time, stop without attempting to shorten the profile.
Port-forward traffic proves only a controlled observation path; it is not an
EKS ingress or end-user latency benchmark. A long, high-rate run through a
single local port-forward was interrupted in the second probe. If either the
Service or Prometheus tunnel drops, the run is invalid: stop, preserve the
failed result, and do not silently reconnect or treat the partial window as
the preregistered one-hour profile. A different load-delivery method needs a
new capacity, cost, and test-plan review before another paid attempt.
Missing metrics, UID mismatch, Pod replacement, or Prometheus restart also
invalidate the observation window;
do not interpret an empty/idle graph as a valid recommendation. The production
seven-day profile is not feasible in this short disposable pilot.

### Proposed next attempt: in-cluster k6 Job (not deployed)

The [second probe](../../docs/devlog/0105-second-eks-pilot.md) lost both local
API port-forwards after about 35 minutes even though the nodes and Pods stayed
up. The [proposed Job](observation-job.yaml) runs the **same committed 60-minute
script** inside `kubefit-demo`, sending traffic to the internal Service without
keeping a local port-forward alive. A local API disconnect can interrupt status
or log retrieval but should not itself terminate a running Job. This is a new
test topology, not a successful validation or a drop-in comparison with the
failed local run.

Before another separately approved, chargeable attempt, verify that two
`m6i.large` nodes can also schedule this Job's 250m/256Mi request alongside
the demo and monitoring Pods. The Job can consume up to 1 CPU and 1 GiB, so
co-location may affect the measured workload. Account for its image pull and
node pressure; do not add nodes or alter the fixed k6 rates ad hoc. Its pinned
image started as a non-root user and loaded the fixed script in local Docker,
but this Job has **not** run on EKS. A `Pending`, `ImagePullBackOff`, failed Job,
nonzero k6 exit, dropped
iteration, or excessive request error invalidates the profile.

Only on the new approved cluster, after confirming neither object exists and
recording the script checksum, create the ConfigMap **from the source file**
and the fixed Job. Do not apply the whole `deploy/eks-pilot` directory.

```sh
shasum -a 256 benchmarks/k6/observation_profile.js
kubectl --context kubefit-eks-pilot -n kubefit-demo get \
  configmap/kubefit-observation-profile job/kubefit-observation
# Both NotFound responses are required before creation.
kubectl --context kubefit-eks-pilot -n kubefit-demo create configmap \
  kubefit-observation-profile \
  --from-file=observation_profile.js=benchmarks/k6/observation_profile.js
kubectl --context kubefit-eks-pilot create -f deploy/eks-pilot/observation-job.yaml
```

Periodically inspect Job conditions, Pod phase/restarts, worker capacity, and
the current demo Pod UIDs. Do not rely on one long `kubectl wait` stream to
prove completion. After completion **and before its one-hour TTL deletes the
Job**, save logs to the ignored local evidence directory and confirm the Job
condition is `Complete`; the JSON summary's `duration_minutes: 60` alone does
not prove elapsed runtime. A failed or timed-out Job must remain failed evidence.

```sh
kubectl --context kubefit-eks-pilot -n kubefit-demo get job kubefit-observation
kubectl --context kubefit-eks-pilot -n kubefit-demo get pods \
  -l batch.kubernetes.io/job-name=kubefit-observation
kubectl --context kubefit-eks-pilot -n kubefit-demo logs job/kubefit-observation \
  > .kubefit/eks-pilot/k6-job.log
kubectl --context kubefit-eks-pilot -n kubefit-demo get job kubefit-observation \
  -o jsonpath='{.status.conditions}'
```

The Prometheus tunnel is still needed for local KubeFit readiness, but it may
be restarted for **read-only collection** after a temporary disconnect; first
reconfirm the same Prometheus Pod and current workload UIDs. A Prometheus Pod
restart loses `emptyDir` history and invalidates the window. Before teardown,
remove this experiment's Job and ConfigMap explicitly, after retaining its
logs; namespace deletion is the final fallback.

```sh
kubectl --context kubefit-eks-pilot -n kubefit-demo delete job \
  kubefit-observation --ignore-not-found
kubectl --context kubefit-eks-pilot -n kubefit-demo delete configmap \
  kubefit-observation-profile --ignore-not-found
```

## 5. Cleanup while the approved deadline remains

Stop traffic and retain only redacted evidence. Before deleting the cluster,
check all namespaces for `LoadBalancer` Services and Ingresses; none should
have been created by this runbook. Remove the demo and monitoring release from
the exact context, then review a Terraform destroy plan for **this state only**.
Do not delete the state until the [teardown inventory](../../docs/eks-validation-plan.md)
confirms the cluster, node group, EC2 instances, root volumes, NAT gateway,
Elastic IP, and any manually added resources are gone. Helm uninstall alone
does not destroy EKS or stop its control-plane charge.
