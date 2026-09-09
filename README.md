# KubeFit

**English** | [한국어](README.ko.md)

KubeFit is a GitOps-first Kubernetes resource optimization platform. It analyzes
real workload metrics, explains its recommendations, and proposes reviewed YAML
changes through GitHub pull requests instead of mutating production workloads.

> Measure first. Explain the trade-off. Change through GitOps.

![KubeFit live counterbalanced pair review](docs/assets/pair-review-live.png)

The stored pair on the right was fully replayed from two opposite-order live benchmark
artifacts before the dashboard displayed `PASS`. The editable scenario panel on the
left is example input and is not the source of that verdict.

## MVP scope

- Analyze Kubernetes `Deployment` resources
- Read CPU and memory usage from Prometheus
- Recommend requests and limits from CPU P95 and memory P99
- Compare estimated cost and operational risk
- Generate a Kubernetes YAML patch
- Open a GitHub draft pull request
- Compare before/after behavior with load tests

HPA recommendations, multi-cloud support, predictive incident detection,
Terraform generation, and an AI chatbot are intentionally outside the first
release.

## Verified MVP evidence

KubeFit's MVP is complete on `main`. The current submission evidence is deliberately
split by claim so a cost projection, one passing run, and a repeated experiment cannot
be mistaken for one another:

| Claim | Reproducible evidence |
|---|---|
| Resource recommendations, package boundaries, and the demo contract are safety-gated | 490 Python tests on the current source |
| The review UI builds and behaves as specified | 19 dashboard tests and a production Vite build |
| The package renders with least-privilege defaults | Helm lint and default-template validation |
| The production image actually starts | Docker startup, numeric non-root user, health, dashboard, and disabled-storage smoke checks |
| Controlled observation works on Kubernetes | 100,501 requests, 0 errors, and 100% usage/throttling coverage in [record 0060](docs/devlog/0060-validation-informed-cpu-floor.md) |
| Unsafe-looking savings cannot override latency | The 10m candidate and one repeated 20m block were rejected by the fixed steady-P99 gate |
| One counterbalanced pair passed complete replay | [Pair and refinement evidence](docs/devlog/0060-validation-informed-cpu-floor.md) |
| A verified pair becomes a reviewable Git change | Idempotent resource [Draft PR #23](https://github.com/sangmu1126/kubefit/pull/23) and [publication record 0061](docs/devlog/0061-live-pair-draft-publication.md) |
| Repeated evidence is not overstated | The preregistered campaign remains explicitly `incomplete`; no aggregate or significance claim is made |
| Public packages are installable without repository credentials | [v0.3.2 release](https://github.com/sangmu1126/kubefit/releases/tag/v0.3.2) with an anonymously verified image and chart |

The latest live resource PR passed Python 3.12, 3.13, and 3.14 plus Dashboard, Helm,
and Docker in [GitHub Actions](https://github.com/sangmu1126/kubefit/actions/runs/32749825481).
It proposes CPU `1000m/2000m → 20m/40m` and memory `2Gi/4Gi → 32Mi/48Mi`, with an
illustrative request-cost projection of `73.000000 → 1.396125` USD per month. This is
not a measured AWS invoice saving. PR #23 remains Draft and KubeFit did not merge or
deploy it.

The [v0.1.0 release-readiness record](docs/release-readiness.md) preserves the earlier
MVP source boundary. Records [0060](docs/devlog/0060-validation-informed-cpu-floor.md),
[0061](docs/devlog/0061-live-pair-draft-publication.md), and
[0062](docs/devlog/0062-verified-v020-release.md), then
[0063](docs/devlog/0063-generated-evidence-package-boundary.md),
[0064](docs/devlog/0064-public-replayable-pair-demo.md), and
[0065](docs/devlog/0065-decision-journey-showcase.md),
[0066](docs/devlog/0066-verified-v030-showcase-release.md), and
[0067](docs/devlog/0067-operator-triggered-verified-demo.md), and
[0068](docs/devlog/0068-verified-v031-interactive-release.md),
[0069](docs/devlog/0069-visual-decision-console.md), and
[0070](docs/devlog/0070-verified-v032-decision-console-release.md),
[0071](docs/devlog/0071-open-source-contribution-surface.md), and
[0072](docs/devlog/0072-submission-evidence-hardening.md) document the later
counterbalanced validation, authenticated Draft publication, verified public package
release, package-content audit, replayable public evidence, presentation boundary,
operator-triggered demonstration, visual decision path, verified public patch release,
open-source contribution surface, and submission-evidence audit.

## Repository layout

```text
collector/       Kubernetes and Prometheus adapters
recommender/     Resource recommendation domain logic
evaluator/       Cost, stability, and performance evaluation
gitops/          YAML patch and GitHub pull request integration
api/             FastAPI application
dashboard/       React recommendation review dashboard
deploy/          Helm chart and demo manifests
benchmarks/      Load tests and reproducible comparisons
safety/          Change inspection, immutable change bundles, and CI evidence binding
docs/            Architecture, security, and evaluation records
tests/           Unit and integration tests
```

The API now has a multi-stage non-root `Dockerfile` and a Helm chart at
[`deploy/helm/kubefit`](deploy/helm/kubefit). The chart defaults to a tokenless
ServiceAccount, read-only root filesystem, dropped capabilities, health probes, and
explicit resources. Optional observation access creates only namespace-scoped
read-only Roles for explicitly named targets; see the
[chart guide](deploy/helm/kubefit/README.md).

`deploy/local/verify-kubefit-chart.sh` performs the complete disposable-kind proof:
local image build/load, tokenless install, health and packaged-dashboard probes,
scoped RBAC allow/deny matrix, and restoration to tokenless defaults. It refuses
non-kind targets and does not push an image or delete the cluster.

The ordered delivery phases and their completion criteria are documented in
[`docs/implementation-plan.md`](docs/implementation-plan.md).

To run KubeFit against a disposable kind cluster with Prometheus, follow the
[`docs/local-development.md`](docs/local-development.md) guide.

Engineering decisions, failed assumptions, diagrams, and reproducible evidence are
recorded in the [`docs/devlog/`](docs/devlog/README.md) development journal.

The local dashboard sends editable example evidence to the existing evaluation API
or loads the JSON emitted by `kubefit analyze`. New schema v2 artifacts retain
aggregate observation and recommendation-policy inputs, so the API replays the
recommendation, risk, cost comparison, and patch eligibility before returning the
review model. The UI contains no independent recommendation logic. Schema v1
remains compatible and is labeled `integrity_only`; schema v2 is labeled
`recommendation_replayed`. Neither version retains raw Prometheus time series for
percentile aggregation replay. Run it with the API using the commands in the
[local development guide](docs/local-development.md). The multi-stage Docker build
packages its immutable production bundle into the same non-root API image used by
Helm.

Repository CI separates Python, dashboard, Helm, and Docker failures. The Python gate
runs the same hash-locked install, compatibility check, lint, and test sequence on
Python 3.12, 3.13, and 3.14 without fail-fast cancellation. The Docker gate does not
stop at image construction: it starts the packaged image on an ephemeral loopback
port, verifies the numeric non-root user, health endpoint, dashboard HTML, and
disabled-by-default benchmark storage, then removes the exact temporary container.

The separate `Release packages` workflow accepts only an existing annotated semantic
version tag that matches the Python and Helm versions. It publishes an amd64/arm64
GHCR image and OCI Helm chart, then uses a fresh job without package permission to
verify the image digest, anonymous pull/runtime, and anonymous chart pull. The
workflow is a release mechanism, not current publication evidence; use a version only
after that version's final anonymous verification job has passed.

Benchmark results can be reviewed either by selecting a local result directory or
through `/?benchmark=benchmark-<digest>`. Counterbalanced pairs use
`/?pair=benchmark-pair-<digest>`, and completed repeated campaigns use
`/?campaign=benchmark-campaign-evidence-<digest>`. Shareable queries are enabled only
when the API has the corresponding explicit read-only results, pairs, or
`KUBEFIT_BENCHMARK_CAMPAIGN_EVIDENCE_DIRECTORY` root. The server revalidates the
complete evidence before returning a review. Pair review plots both order-specific
changes and their observed minimum–maximum range. Campaign review plots chronological
block position and duration plus planned/observed starting order. Neither view calls
its observations a confidence interval or aggregate effect. KubeFit does not publish
artifacts or make a local directory public automatically.

After building `kubefit:dev`, `deploy/local/generate-image-sbom.sh` resolves the
mutable tag to its complete local image ID and publishes a verified SPDX 2.3
inventory under the ignored `.kubefit/supply-chain/` directory. Repeated runs
rehash and reuse the existing artifact. This is package inventory evidence, not a
vulnerability scan or signature.

The same verifier can inspect a published release without treating a hand-written
dependency summary as a complete SBOM:

```bash
docker pull ghcr.io/sangmu1126/kubefit:0.3.2
KUBEFIT_IMAGE_REFERENCE=ghcr.io/sangmu1126/kubefit:0.3.2 \
  ./deploy/local/generate-image-sbom.sh
```

Release images also carry the BuildKit SBOM and provenance attestations emitted by
the package workflow. These attestations and the local SPDX inventory do not imply
that the image is vulnerability-free or cryptographically signed by the maintainer.
Because the dashboard is compiled into static assets, its bundled JavaScript
dependencies are tracked by `dashboard/package-lock.json` and may not appear as
separate packages in a scanner's final-image SPDX output.

## Quick start

### Replay the verified pair

With Docker running, one command downloads the public self-contained evidence,
verifies its pinned SHA-256, mounts it read-only, and starts the published image:

```bash
./deploy/local/run-verified-pair-demo.sh
```

Open the printed `http://127.0.0.1:8000/?showcase=decision-journey` URL. Press
**추천 계산 실행**, then follow the Decision Console's **20m Pair 검증 계속** action.
The published `v0.3.2` image visualizes current, observed, rejected, and verified
resources plus the source-labeled execution trace, opposite orders, and policy checks.
The exact YAML diff and Draft PR link open only after `PASS`. The script binds only to
loopback, never contacts Kubernetes, and removes its container on Ctrl+C. Set
`KUBEFIT_DEMO_PORT` if port 8000 is already in use.

#### Inspect the runtime behind the showcase

The showcase is easiest to record with one terminal window and two tabs. If the
[local Kubernetes environment](docs/local-development.md) is already running, the
first tab can show the actual kind, Prometheus, and demo-Deployment processes:

```bash
docker ps
kubectl --context kind-kubefit get pods -n monitoring
kubectl --context kind-kubefit get pods -n kubefit-demo
kubectl --context kind-kubefit get deployment overprovisioned-api \
  -n kubefit-demo \
  -o jsonpath='{.spec.template.spec.containers[0].resources}'
```

To inspect the live Prometheus scrape, keep this command running temporarily in the
second tab and open `http://127.0.0.1:9090`:

```bash
kubectl --context kind-kubefit port-forward \
  -n monitoring \
  svc/monitoring-kube-prometheus-prometheus \
  9090:9090
```

For the default low-load check, open **Status → Target health**, filter for `kubelet`,
and confirm that `/metrics`, `/metrics/cadvisor`, and `/metrics/probes` are `UP`.
KubeFit's container CPU and memory source is the cAdvisor endpoint. Target health
proves scrape connectivity, not recommendation safety or benchmark success.

An optional PromQL inspection can display current per-Pod CPU usage in millicores:

```promql
sum by (pod) (
  rate(container_cpu_usage_seconds_total{
    namespace="kubefit-demo",
    container="api"
  }[5m])
) * 1000
```

An idle demo Deployment can correctly report `0m`; generating traffic is not required
for the verified-pair walkthrough. The collector uses this five-minute rate across a
coverage-gated observation window rather than treating one current value as evidence.

After starting the verified-pair demo, a second tab can prove that the packaged
FastAPI server is healthy and that the visible `PASS` comes from full server-side
artifact replay rather than a frontend constant:

```bash
curl --fail http://127.0.0.1:8000/healthz

curl --silent \
  http://127.0.0.1:8000/v1/benchmark-pairs/benchmark-pair-dbc41864dd0dba9537ef228ebb340f60/review \
  | jq '{status, verification_level, benchmark_ids, checks: [.checks[].status]}'
```

`jq` only shortens the output for display. The Pair endpoint performs the same full
review without it. The Prometheus view demonstrates the live collection path; the
showcase itself deliberately replays the separately retained, digest-pinned Pair and
does not claim that the current Prometheus samples produced the recorded verdict.

To test uncommitted current-source changes instead of the published `v0.3.2` image,
build and run with the same script:

```bash
KUBEFIT_DEMO_BUILD_LOCAL=true ./deploy/local/run-verified-pair-demo.sh
```

This route connects a live recommendation calculation to the recorded
`reject → refine → verify → Draft PR` narrative and the same server-replayed Pair.
The UI labels live API responses separately from committed evidence. The published
`v0.3.2` image and current-source path use the same APIs, Decision Console, and
digest-pinned `v0.2.0` Pair evidence; only the application image source differs.

### Open only the editable review scenario

The published image contains both FastAPI and the React review dashboard. No local
Node.js or Kubernetes cluster is required for the editable review scenario:

```bash
docker run --rm -p 127.0.0.1:8000:8000 \
  ghcr.io/sangmu1126/kubefit:0.3.2
```

Open `http://127.0.0.1:8000`. This default screen evaluates editable example inputs
through the API and does not collect from or mutate a cluster. A fully replayed stored
benchmark or pair additionally requires its read-only evidence directory; follow the
[local dashboard guide](docs/local-development.md#open-a-fully-verified-benchmark-pair-or-campaign-link).

### Install the development environment

Requires Python 3.12+.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements/build.lock
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
pytest -q
uvicorn api.main:app --reload
```

Maintainers regenerate all three Python locks with `pip-tools==7.6.1` and
`deploy/local/compile-python-locks.sh`; normal installs consume the reviewed locks
rather than resolving compatible ranges again.

Then open `http://localhost:8000/docs`. Use `POST /v1/recommendations` for the
capacity result alone or `POST /v1/evaluations` for a recommendation plus an
explicit request-cost comparison.

Analyze a live Deployment (with Prometheus reachable locally):

```bash
kubefit readiness --context kind-kubefit \
  --namespace kubefit-demo --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json --days 1

kubefit analyze --namespace kubefit-demo --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --cpu-core-hour-usd 0.04 \
  --memory-gib-hour-usd 0.005 \
  --price-source example://local-model
```

The CLI invokes `kubectl` using the current context, reads only Deployment and
Pod metadata, queries Prometheus, and prints a recommendation with its evidence.
Prometheus must scrape cAdvisor metrics plus `kube_pod_owner` from
kube-state-metrics. KubeFit filters ReplicaSets through their Kubernetes controller
owner UID and clips history to the current Deployment creation time, avoiding
Pod-name prefixes and same-name workload history.

For the local competition demo, do not wait on a one-day mostly idle window. Run the
fixed one-hour `benchmarks/k6/observation_profile.js` traffic profile and use
`--observation-profile demo` for both readiness and analysis. This path requires 90%
coverage and labels its result controlled-demo-only. The default `production`
profile remains multi-day and is intended for representative real traffic.

If a benchmark rejects an aggressive CPU floor, `kubefit reanalyze` can derive a
stricter schema v2 analysis from the retained observation and pricing inputs without
pretending that new metrics were collected. The command can only raise the retained
floor; the override remains explicit in the replayable recommendation policy.

`kubefit readiness` uses the same collection and policy path without requiring
price inputs. It distinguishes evidence that only needs more collection time from
unstable replicas, missing Pod signals, and already observed high-risk conditions.
Time estimates state their stable-replica and continued-scrape assumptions.

The evaluator also reads the cAdvisor CPU throttled-period counters and current
Kubernetes target-container status. Missing throttling metrics or incomplete Pod
status coverage keeps the relevant risk `unknown`; an observed OOMKilled state is
reported as high risk even when the wider observation window is incomplete.

Every evaluation includes `patch_eligibility`. Future GitOps patch generation must
require `eligible`; insufficient readiness and high or unknown safety risks produce
stable machine-readable blocking checks. Medium risk remains an explicit reviewer
warning and never becomes an invisible pass.

The `gitops` package can generate a stale-safe manifest proposal from an eligible
evaluation. It matches one `apps/v1` Deployment and container across supplied YAML
sources, verifies that repository resources still equal the evaluated workload, and
returns patched content, a unified diff, and a SHA-256-backed report without writing
the file. Only the changed resource scalar ranges are replaced, preserving unrelated
documents, fields, comments, order, and quoting.

An eligible patch can be published as an immutable proposal bundle containing the
before/after manifests, canonical evaluation, diff, report, and benchmark context.
Its ID is derived from content, every payload is indexed by SHA-256 and byte size,
and publication uses a private staging directory followed by one directory rename.
Identical retries reuse the bundle; modified or partial existing content is rejected.

The `benchmarks` package defines `kubefit-load-v1`, a fixed k6 warmup → steady →
spike → recovery profile, plus typed before/after measurements and an explicit
safety verdict. Results must reference the same proposal, profile, and complete
offered load before latency, errors, throttling, OOM, and recovery regressions are
evaluated. Per-phase completed iterations must meet the fixed minimum and match
the minimum or its single scheduler-boundary overshoot in each run. Missing work or
two extra iterations remains invalid. Request-cost change is reported separately and
cannot override safety.

The benchmark execution core revalidates every proposal hash before cluster access,
requires an explicit kubectl context, executes a selected `before-after` or
`after-before` sequence, and reapplies the before manifest on every exit path after
mutation starts. Measurement timestamps must prove non-overlapping intervals and
every single sequential result carries an order-bias warning. The CLI is
restricted to an explicitly acknowledged disposable `kind-*` cluster and is not a
production automation interface. After Kubernetes reports rollout completion, the
runner also waits until exactly the desired number of selected Pods remain and every
target container is Running and Ready, preventing terminating rollout Pods from
contaminating the runtime snapshot.

The aligned measurement collector brackets the fixed k6 run with Pod-level runtime
snapshots, queries Prometheus throttling inside that run, derives recovery from raw
timestamped samples, and selects the proposal-fixed monthly request cost. Its output
records run timestamps, Pod identity, and raw/summary hashes. The result publisher
retains those inputs in a separate immutable benchmark artifact.

Completed benchmark executions can now be published separately from their proposal
as immutable `benchmark-<digest>` artifacts. Each result binds canonical before/after
measurements, exact k6 summaries and raw streams, the recomputed verdict, and a
human-readable report with per-file hashes. Identical publication retries reuse the
same result; tampered or partial existing content is never overwritten.

`kubefit benchmark` composes the local workflow under a Deployment-scoped OS lock.
It requires an explicit `kind-*` context and `--confirm-disposable-cluster`, holds
the lock through restoration and result publication, then prints a compact JSON
handoff. A rollout-safe local Service proxy, a Prometheus port-forward, and an
existing immutable proposal are currently required; see
[`docs/local-development.md`](docs/local-development.md).

Operators can counterbalance time-order effects by running the same proposal once in
each order. `kubefit benchmark-pair` fully verifies both content-addressed artifacts
and emits one deterministic PASS/FAIL/INVALID policy-agreement assessment. A passing
assessment is published with complete copies of both benchmark bundles as an immutable
`benchmark-pair-<digest>` artifact. It does not average two samples or claim statistical
significance; this self-contained pair is a mandatory publication input.
The same verified pair supplies an order-aware metric table in the Draft PR and a
read-only dashboard plot. It reports whether both changes improved, regressed, stayed
equal, or pointed in different directions, without averaging the two trials.

For CI, `kubefit check` evaluates the same two immutable result artifacts without
publishing a pair. It prints structured JSON, appends a readable report to
`GITHUB_STEP_SUMMARY` when that variable is present, and exits with status 2 unless
the pair passes:

```bash
kubefit check \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest>
```

`kubefit inspect-change` is a scope gate for a PR's base and candidate
manifest. It ignores YAML formatting, reports image, replica, and CPU/memory resource
changes, and rejects unrelated Deployment changes. Unsupported paths are reported
without their values so CI logs do not disclose environment data.

```bash
kubefit inspect-change \
  --base /tmp/base.yaml \
  --candidate deploy/api.yaml \
  --namespace demo \
  --deployment api
```

For resource proposals, `kubefit validate` closes the evidence-binding gap: it fully
reloads the immutable proposal and both results, requires the pair to reference that
proposal, requires the supplied base/candidate files to be byte-identical to the
proposal input/output, and then enforces the Pair verdict. It emits one end-to-end
Step Summary and exits with status 2 for FAIL or INVALID. Generic image and replica
benchmark bundles are not implemented yet.

```bash
kubefit validate \
  --proposal .kubefit/proposals/proposal-<digest> \
  --base /tmp/base.yaml \
  --candidate deploy/api.yaml \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest>
```

After a release tag contains this action metadata, a Linux GitHub Actions job can run
the same gate without installing Python dependencies. The calling job must first make
the proposal and both result directories available in its workspace:

```yaml
- name: Validate Kubernetes resource change
  uses: sangmu1126/kubefit@<release-tag>
  with:
    proposal: .kubefit/proposals/proposal-<digest>
    base: evidence/base.yaml
    candidate: deploy/api.yaml
    first-result: evidence/benchmark-<before-first-digest>
    second-result: evidence/benchmark-<candidate-first-digest>
```

The Docker action passes each input as a direct process argument rather than evaluating
it in a shell. Local Docker build and container execution are verified; remote GitHub
Actions execution requires publishing a new tag and is not claimed by the current
unreleased source.

As the first boundary for image and replica execution, `kubefit prepare-change`
publishes the exact base/candidate manifests and replayed semantic diff as an immutable
`change-<digest>` bundle. Identical retries reuse the same directory; changed, partial,
symlinked, or non-canonical contents are rejected on load. This bundle identifies input
evidence only. `kubefit execute-change` can then check that those exact manifests deploy
and become ready on an explicitly acknowledged disposable kind cluster, restoring the
base even after candidate failure or interruption. This is not benchmark or PodKill
evidence.

```bash
kubefit prepare-change \
  --base /tmp/base.yaml \
  --candidate deploy/api.yaml \
  --namespace demo \
  --deployment api

kubefit execute-change \
  --change .kubefit/changes/change-<digest> \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster
```

The shared fixed k6 profile accepts a mutually exclusive `change_id` identity.
`kubefit benchmark-change` sequences base/candidate load collection with mandatory base
restoration and publishes a content-addressed `change-performance-<digest>` result. Its
replayable verdict is deliberately limited to fixed-load completeness, latency, error
rate, and recovery time; it makes no cost, throttling, OOM, or fault claim.

Start a rollout-safe Kubernetes API proxy before either order. A Service port-forward
can remain attached to a Pod that the benchmark replaces and must not be used here.

```bash
kubectl --context kind-kubefit proxy \
  --port=8001 \
  --address=127.0.0.1 \
  --accept-hosts='^127\.0\.0\.1$'
```

```bash
kubefit benchmark-change \
  --change .kubefit/changes/change-<digest> \
  --target-url http://127.0.0.1:8001/api/v1/namespaces/kubefit-demo/services/http:overprovisioned-api:80/proxy/ \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster \
  --execution-order before-after
```

Both complete load phases take roughly six minutes in total before rollout time. FAIL
and INVALID results are retained before the command exits with code 2. The generic path
has run against local kind: one 2026-09-10 counterbalanced Pair conservatively failed
because different low-latency percentile checks failed in opposite orders. No PodKill
was allowed from that Pair.

Run a second independent trial with `--execution-order after-before` to collect the
opposite order. Each artifact records and revalidates its order from measurement
timestamps and still carries a sequential-order warning until a later Pair assessment
binds both artifacts.

Bind the two artifacts into a self-contained counterbalanced Pair:

```bash
kubefit benchmark-change-pair \
  --first .kubefit/change-performance/change-performance-<first> \
  --second .kubefit/change-performance/change-performance-<second>
```

The Pair requires distinct opposite-order trials with the same change, target, profile,
and policy. Both trials and their non-order check statuses must pass. A failed Pair is
retained and exits 2; INVALID inputs exit 2 without publishing a Pair artifact. The Pair
reduces directional order bias but does not establish statistical significance.

The fault-safety boundary starts with a read-only preflight:

```bash
kubefit podkill-preflight \
  --context kind-kubefit \
  --namespace demo \
  --deployment api \
  --container api
```

It requires at least two fully ready replicas, follows Deployment UID → owned ReplicaSet
UID → owned Pod UID, excludes selector collisions, and deterministically selects the
oldest ready Pod. The preflight itself remains read-only and is not fault evidence.

After both generic performance orders pass and are bound into one Pair, the controlled
experiment can be run on an explicitly acknowledged disposable kind cluster:

```bash
kubefit podkill-run \
  --change .kubefit/changes/change-<digest> \
  --performance-pair \
    .kubefit/change-performance-pairs/change-performance-pair-<digest> \
  --target-url http://127.0.0.1:8001/api/v1/namespaces/kubefit-demo/services/http:overprovisioned-api:80/proxy/ \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster \
  --confirm-pod-deletion
```

The command verifies the exact change, passing Pair, and target before entering a
workload-scoped lock. It then refreshes the complete ownership preflight, deletes one
exact Pod name with a one-second grace period, and measures both a host-side HTTP
success streak and a new ready Pod UID under a bounded timeout. PASS and timeout FAIL
are written as self-contained, content-addressed `podkill-<digest>` evidence; FAIL exits
with code 2. This path is locally unit-tested and has not yet been exercised against a
live cluster, so the repository does not claim observed recovery performance.

Before collecting repeated fault results, freeze the sample count, failure budget, and
recovery thresholds in a content-addressed campaign plan:

```bash
kubefit podkill-campaign-plan \
  --change .kubefit/changes/change-<digest> \
  --performance-pair \
    .kubefit/change-performance-pairs/change-performance-pair-<digest> \
  --context kind-kubefit \
  --planned-trials 5 \
  --allowed-failed-trials 0 \
  --service-recovery-limit-seconds 3 \
  --replacement-ready-limit-seconds 30
```

The plan requires at least three trials and fixes a complete-all-trials stopping rule.
It does not run faults or claim statistical significance; its purpose is to prevent
choosing the trial count and acceptable thresholds after seeing outcomes.

After collecting every planned `podkill-<digest>` result, assess and package them:

```bash
kubefit podkill-campaign-check \
  --plan .kubefit/podkill-campaigns/podkill-campaign-<digest> \
  --trial .kubefit/podkill-results/podkill-<first> \
  --trial .kubefit/podkill-results/podkill-<second> \
  --trial .kubefit/podkill-results/podkill-<third>
```

The check reloads every nested artifact, rejects duplicates, mixed contexts or targets,
and overlapping trial intervals, then applies the preregistered failure and recovery
limits. Complete PASS and FAIL outcomes are both retained as self-contained
`podkill-campaign-evidence-<digest>` directories; incomplete or invalid input exits 2
without publication. Reported P50/P95 values use nearest-rank summaries of successful
recoveries and are descriptive, not statistical confidence claims.

Repeated evidence can be preregistered with `kubefit benchmark-campaign-plan`. The
immutable plan fixes an explicit pair count, balances and randomizes which execution
order starts each time block, and requires every planned block before completion.
`kubefit benchmark-campaign-check` fully reloads supplied pair artifacts and rejects
duplicates, proposal/profile/cost drift, overlapping time blocks, schedule violations,
and outcome-dependent early stopping. COMPLETE publishes an immutable, self-contained
`benchmark-campaign-evidence-<digest>` with the plan and every pair bundle, then reloads
it before returning success. Campaign completion is optional advanced evidence: it does
not yet compute variance or replace the mandatory single-pair publication gate.

`kubefit analyze` emits a typed schema v2 artifact binding aggregate observation,
policy, and evaluation evidence to Deployment UID and creation time. Loading it
replays the saved recommendation decision. `kubefit propose` consumes that identity
directly with repository-bounded YAML sources and publishes an immutable proposal;
it does not allow the target to be retyped. Benchmark preflight rejects a recreated
Deployment before any apply. Multi-document source files remain byte-exact review
evidence, while benchmark apply and restoration use separately hashed,
single-Deployment manifests so neighboring Services or workloads are never
reconciled.

After a passing benchmark pair, `build_pull_request_plan` independently reloads the
proposal, primary before-after benchmark, and self-contained pair artifact. It replays
both embedded benchmark results and their pair assessment, requires the primary result
to be a member of that pair, and produces a deterministic one-file draft PR contract.
The contract includes the exact expected repository source hash, patched content,
benchmark metrics, pair identity, both order-specific changes and their observed
range, cost caveats, warnings, and rollback guidance. Supplying
`--benchmark-campaign-evidence` explicitly also reloads the complete repeated campaign,
requires the mandatory pair to be one of its chronological blocks, and adds the
campaign IDs and block table to the same PR body. Omitting it preserves the normal
pair-only publication path. This stage is read-only; branch creation and GitHub
publication remain separate adapters.

`commit_pull_request_plan` applies that contract to an explicit clean Git top-level.
It rechecks the source hash and bytes, rejects symlinks and detached HEAD, creates a
one-file commit on the deterministic branch, verifies the resulting Git tree, and
returns to the original branch. An identical existing branch is reused; a collision
fails closed. It does not push or contact GitHub.

`publish_pull_request` revalidates that local handoff, derives the GitHub repository
identity from a credential-free `github.com` remote, and publishes the exact commit
with an absent-ref lease. It reuses only an identical remote ref and an exact open
draft PR contract; conflicting refs, duplicate matches, and edited PRs fail closed.
Ambiguous push or API responses are resolved by observing remote state again. The
GitHub token is sent only as an HTTP authorization header. This library boundary is
exposed by `kubefit publish`, which requires `--confirm-publish` and reads the token
only from a named environment variable. The command never merges or deploys the
change.

`kubefit publish-check` runs the same artifact and local Git validation without
creating a commit, then reads the configured GitHub remote ref and, when a token is
present, repository metadata through a GET request. Its JSON separates blockers
from warnings and always reports `mutation_performed: false`. A `ready` result means
the observable preconditions passed; it does not prove effective branch or pull
request write permission.

The authenticated two-run verification procedure is documented in
[`docs/live-github-demo.md`](docs/live-github-demo.md). It requires a separately named
private disposable repository, captures first-create and second-reuse evidence, and
archives rather than deletes the target by default.

`kubefit verify-publication` then validates the runbook's exact five-file evidence
set without network access. It rebuilds the proposal/benchmark/pair plan, checks the
preflight, two publication outputs, remote ref, and GitHub Draft PR as one contract,
hashes every file, and emits a deterministic `publication-<digest>` verification ID.
When optional campaign evidence is supplied, it additionally binds the campaign IDs
and requires the independently captured GitHub body to equal the generated body.

The example prices are illustrative, not a cloud-provider price claim. Live
analysis requires the caller to provide CPU and memory rates plus a source label.
The output repeats those assumptions and separates current/recommended CPU and
memory request costs. Projected request savings do not necessarily become invoice
savings because node fragmentation, discounts, taxes, and autoscaling replica-hours
are outside this model.

The optional identity store retains observed ReplicaSet names after Kubernetes
deletes their objects. It is an atomic local JSON snapshot containing identifiers
only; a new Deployment UID replaces same-name history rather than merging it.

Example request:

```json
{
  "current": {
    "cpu_request_millicores": 1000,
    "cpu_limit_millicores": 2000,
    "memory_request_mib": 2048,
    "memory_limit_mib": 4096
  },
  "observed": {
    "cpu_p95_millicores": 230,
    "memory_p99_mib": 710,
    "cpu_max_millicores": 400,
    "memory_max_mib": 900,
    "sample_count": 1900,
    "observation_coverage": 0.95,
    "desired_replicas": 2,
    "available_replicas": 2,
    "observed_replicas": 2
  }
}
```

For `/v1/evaluations`, add the following top-level fields to the same request:

```json
{
  "cost_assumptions": {
    "cpu_core_hour_usd": "0.04",
    "memory_gib_hour_usd": "0.005",
    "monthly_hours": "730",
    "price_source": "example://local-model"
  },
  "replica_count": 2
}
```

When maxima or sufficient observation coverage are unavailable, KubeFit reports
the related risk as `unknown` rather than presenting an unsupported low-risk claim.
The recommendation is actionable only when its `readiness.status` is `ready`.

## Project origin

KubeFit grew from lessons about over-allocation and observability learned while
operating an earlier serverless platform. It is independently designed and
implemented as an open-source Kubernetes optimization tool. The prior project
is context, not this repository's codebase or deployment architecture.

## License

KubeFit is distributed under the [Apache License 2.0](LICENSE).

## Community and contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) for the
locked development setup, quality gates, design boundaries, and pull-request process.
Use the structured GitHub issue forms for bugs and feature proposals, and follow the
[Code of Conduct](CODE_OF_CONDUCT.md) in all project spaces.

Do not disclose suspected vulnerabilities in a public issue. The supported-version,
reporting, and product-security boundaries are documented in
[SECURITY.md](SECURITY.md).

## Safety principles

- Read workloads and metrics; never mutate a cluster in the recommendation path.
- Submit changes as draft pull requests with evidence and rollback guidance.
- Never store Kubernetes, Prometheus, or GitHub credentials in the repository.
- Keep recommendation policy deterministic and independently testable.
