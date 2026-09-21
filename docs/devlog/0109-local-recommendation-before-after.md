# 0109 — Test the EKS recommendation against local before/after load

Date: 2026-09-21. **Disposable local kind only; no AWS resources were created.**
The EKS pilot's synthetic recommendation was not applied to EKS. The local
experiment used the same demo Deployment definition but a new kind 1.36.1
cluster and a shorter, fixed performance profile. Raw k6 Pair artifacts and
the exact candidate manifest remain in ignored `.kubefit/` paths locally.

## Why and preregistered comparison

The [third EKS pilot](0108-third-eks-pilot-complete.md) proved observation and
read-only recommendation, not the effect of applying its values. This test
asked a narrower question: under the repository's fixed 160-second local
profile, do the recommended resources pass the existing latency, error,
offered-load, and recovery policy in both measurement orders?

| Resource | Existing base | EKS analysis candidate |
|---|---:|---:|
| CPU request | 1000m | 10m |
| Memory request | 2048Mi | 32Mi |
| CPU limit | 2000m | 20m |
| Memory limit | 4096Mi | 48Mi |

The only changed fields were the container resources. The byte-exact change
bundle is `change-8f734353fc0f5fc9d1ce2964a3b2cf7f`. The committed load
script was `benchmarks/k6/resource_profile.js` (SHA-256
`791fb026e590aeca07246697f10387d42375c471dc3d3c464820cbb82d2c5b73`):
10 seconds at 1 request/s, 60 seconds at 5/s, 30 seconds at 25/s, then
60 seconds at 5/s. Traffic went through the localhost Kubernetes API Service
proxy, which stays bound to the Service across Pod rollouts.

```text
base → candidate → restore base     PASS
candidate → base → restore base     PASS
               ↓
       counterbalanced Pair        PASS
               ↓
   separate Prometheus check: candidate CPU throttling observed
```

The Pair checks performance in both orders; the independent Prometheus check
prevents its PASS from being misread as proof that every safety dimension passed.

## Results

| Order and phase | Steady P95 / P99 ms | Spike P95 / P99 ms | Errors / dropped |
|---|---:|---:|---:|
| Base first: base | 10.686 / 27.337 | 43.217 / 489.211 | 0 / 0 |
| Base first: candidate | 10.769 / 11.942 | 8.084 / 9.241 | 0 / 0 |
| Candidate first: candidate | 10.486 / 18.271 | 8.116 / 9.149 | 0 / 0 |
| Candidate first: base | 10.664 / 17.374 | 8.507 / 15.784 | 0 / 0 |

Both individual artifacts passed the existing policy and restored the base;
the opposite-order Pair
`change-performance-pair-255907615d3d8b3b8cd5be01a94af85f` also passed.
All four phases delivered their fixed load, the recovery time was 5 seconds
for each variant, and no measured HTTP errors or dropped iterations occurred.
The first base spike P99 was much larger than the other three spikes; the
directional difference must **not** be sold as an improvement caused by the
candidate. Two opposite-order trials reduce time-order bias but do not
establish statistical significance.

Prometheus showed a different warning. Over a 30-minute query window that
covered the experiment, the ratio of increased throttled CFS periods to
increased total CFS periods was **0% for the base revision** and about **7.8%
for the candidate revision**. Each of the four candidate Pods showed about
6.6–9.6%; the six observed base Pods showed 0%. The query used separate
Deployment revision Pod-name prefixes and `increase(...[30m])` on
`container_cpu_cfs_throttled_periods_total` and
`container_cpu_cfs_periods_total`. This was a post-hoc diagnostic, not a
preregistered benchmark acceptance check. Kube-state-metrics reported a
maximum of zero restarts across ten observed demo Pods, and the OOMKilled
last-termination series was empty. Those checks do not prove future OOM
safety.

## Decision and limits

**Performance Pair PASS; overall recommendation safety is not established.**
The 20m CPU limit is an apparent throttling risk even though this short,
low-complexity nginx test did not regress beyond the existing latency policy.
Do not publish the 10m/32Mi + 20m/48Mi proposal as a generally safe EKS or
production setting, and do not use this run as proof of AWS cost savings.
The next algorithmic question is whether CPU limit sizing and the benchmark
gate should explicitly account for post-change throttling, with a defined
threshold and repeated tests on a representative workload. This one local
experiment is evidence for that design decision, not enough to pick a
universal new minimum limit.

The proxy and Prometheus tunnels were stopped; the exact `kind-kubefit`
cluster was deleted and `kind get clusters` found none. The real AWS pilot
remained torn down throughout.
