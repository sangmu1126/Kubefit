# Local Kubernetes and Prometheus

The local environment runs a single-node kind cluster in Docker and installs a
minimal kube-prometheus-stack. Prometheus scrapes the kubelet/cAdvisor metrics
used by KubeFit for container CPU and memory analysis.

## Prerequisites

- Docker Desktop running
- `kubectl`
- `kind`
- `helm`
- the Python development environment from the project quick start

On macOS, the missing command-line tools can be installed with:

```bash
brew install kind helm
```

## Create the environment

```bash
./deploy/local/up.sh
```

The script is idempotent. It creates the `kubefit` kind cluster, installs pinned
version `88.5.0` of `kube-prometheus-stack`, and applies the demo Deployment.
Grafana, Alertmanager, unused control-plane monitoring, and kube-proxy monitoring are
disabled to keep local resource usage and scrape noise small. KubeFit reads workload
CPU and memory from kubelet/cAdvisor and does not consume kube-proxy metrics. Prometheus
stores its two-day history on a 5 GiB PVC with a 4 GB retention-size ceiling, so Pod
and Docker restarts do not reset observation coverage.

Upgrading a cluster created before this storage configuration replaces the old
emptyDir-backed Prometheus Pod once. Its existing metrics are not migrated, so the
readiness accumulation window begins again after that first upgrade.

Check the resulting workloads:

```bash
kubectl --context kind-kubefit get pods -n monitoring
kubectl --context kind-kubefit get pods -n kubefit-demo
kubectl --context kind-kubefit get pvc -n monitoring
```

## Collect a controlled local demo window

Keep Prometheus and the demo Service port-forwards running in separate terminals:

```bash
kubectl --context kind-kubefit port-forward \
  -n monitoring \
  svc/monitoring-kube-prometheus-prometheus \
  9090:9090

kubectl --context kind-kubefit port-forward \
  -n kubefit-demo \
  service/overprovisioned-api \
  8080:80
```

Start the fixed one-hour traffic profile. Do not use old idle samples as controlled
demo evidence:

```bash
KUBEFIT_TARGET_URL=http://localhost:8080/ \
  k6 run benchmarks/k6/observation_profile.js
```

The profile runs 5 RPS warmup, 25 RPS steady traffic, a 100 RPS spike, then 25 RPS
recovery across exactly one hour. After it finishes, check the same one-hour window:

```bash
kubefit readiness \
  --context kind-kubefit \
  --namespace kubefit-demo \
  --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --observation-profile demo
```

`collecting` includes an estimated readiness timestamp only when replicas, Pod
metrics, and container statuses are complete. `blocked` requires intervention and
does not include a time estimate. Demo mode fixes a one-hour window, 60-second step,
90% coverage, and at least 100 samples; attempts to combine it with `--days` or
`--step-seconds` fail. Its recommendation evidence is explicitly non-production.

For a real environment, omit `--observation-profile` to retain the production
default of seven days, a five-minute step, 70% coverage, and at least 100 samples.

## Analyze the demo Deployment

When readiness becomes `eligible`, create the priced analysis artifact:

```bash
kubefit analyze \
  --context kind-kubefit \
  --namespace kubefit-demo \
  --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --observation-profile demo \
  --cpu-core-hour-usd 0.04 \
  --memory-gib-hour-usd 0.005 \
  --price-source example://local-model \
  > .kubefit/analysis.json
```

These rates are deliberately labeled example inputs. Replace them and the source
label with assumptions appropriate to the environment being evaluated. The default
billing horizon is 730 hours and can be changed with `--monthly-hours`.

A controlled demo cannot provide a complete one-hour window until its load profile
finishes, so KubeFit reports low coverage and `unknown` risk until enough samples
have accumulated. A mathematical cost projection is still shown for inspection,
but it does not make an insufficient recommendation actionable. Do not present this
short-window result as a production traffic recommendation.

The analysis artifact also includes CPU throttling P95 and its independent coverage, plus
restart and OOMKilled counts from the current target-container statuses. A quiet new
cluster still reports `unknown` rather than `low` until both usage and throttling
windows satisfy the readiness gates.

## Replay the published pair without a cluster

The shortest reproducible dashboard path requires Docker, `curl`, and `tar`, but does
not require kind, Prometheus, Python, or Node.js:

```bash
./deploy/local/run-verified-pair-demo.sh
```

The script downloads `kubefit-demo-evidence-v0.2.0.tar.gz` from the public `v0.2.0`
GitHub Release into the ignored `.kubefit/demo/` cache. It accepts the archive only
when its SHA-256 is
`c646b4483083f8fcedafb397d1cc2355391bc9f98b15a6b157e22b30f2793239`, rejects unsafe
archive paths, mounts the extracted pair read-only, and starts
`ghcr.io/sangmu1126/kubefit:0.3.2` on loopback port 8000. The evidence remains the
immutable `v0.2.0` asset, while the printed Showcase URL selects and replays that exact
counterbalanced Pair through the newer presentation image.

Use another port when necessary:

```bash
KUBEFIT_DEMO_PORT=18001 ./deploy/local/run-verified-pair-demo.sh
```

The first run downloads the pinned image and 507 KB evidence archive. Later runs reuse
the digest-verified cache. Ctrl+C shuts down and removes only the demo container. This
flow replays retained controlled-demo evidence; it does not collect new metrics,
contact a cluster, establish statistical significance, or deploy Draft PR #23.

### Run the Decision Journey from the current source

The default path uses the published `v0.3.2` image. Build the current working tree
instead when validating changes that have not been released:

```bash
KUBEFIT_DEMO_BUILD_LOCAL=true ./deploy/local/run-verified-pair-demo.sh
```

The script tags the local build as `kubefit:decision-journey`, reuses the same
digest-pinned public evidence, and prints
`http://127.0.0.1:8000/?showcase=decision-journey`. The screen visualizes the recorded
failed 10m proposal, monotonic 20m refinement, server-replayed Pair PASS, retained
mixed signals, and Draft PR boundary. It does not execute a new benchmark or contact
Kubernetes. Without `KUBEFIT_DEMO_BUILD_LOCAL=true`, the script pulls the public
`v0.3.2` image and opens the same Showcase route.

## Review an evaluation in the dashboard

The first dashboard slice is a local review surface over the existing evaluation
API. Start the API in one terminal:

```bash
uvicorn api.main:app --reload
```

Install the locked frontend dependencies and start Vite in another terminal:

```bash
npm --prefix dashboard install
npm --prefix dashboard run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/v1` and `/healthz` to
`http://127.0.0.1:8000`, so no permissive CORS policy is required. Choose
`검증 가능` to inspect an eligible example or `근거 부족` to see why a numerical
cost projection remains blocked when coverage and safety signals are absent.

To review the artifact created in the previous section, choose
`analysis.json 불러오기` and select `.kubefit/analysis.json`. The browser applies a
1 MiB usability guard and sends the raw JSON to `POST /v1/analysis-reviews`. The API
parses the typed artifact and recomputes resource deltas, request-cost comparison,
and patch eligibility before the existing result view renders it. This operation
does not contact Kubernetes, modify repository files, or create a proposal.

The artifact context shows Deployment UID and creation time so a screenshot does
not lose workload incarnation identity. Current `kubefit analyze` output is schema
v2 and retains aggregate `ObservedUsage` plus all recommendation policy parameters.
The API reruns recommendation, risk, cost, and eligibility and labels an exact match
`RECOMMENDATION REPLAYED`. Older schema v1 remains accepted as `INTEGRITY ONLY`.
Neither schema retains raw Prometheus time series, so the P95/P99 aggregation itself
cannot be replayed. Producer authentication and repository-byte binding are also
outside this endpoint.

The examples are editable API payloads, not live cluster collection. Uploaded
artifacts can originate from collection, but neither dashboard review label is
proof of uninterrupted live collection or percentile aggregation replay. Price
values use `example://local-model` and must not be presented as provider prices or
measured invoice savings.

### Open a fully verified benchmark, pair, or campaign link

After `kubefit benchmark` has produced an immutable result, restart the API with an
explicit results root:

```bash
KUBEFIT_BENCHMARK_RESULTS_DIRECTORY=benchmarks/results \
KUBEFIT_BENCHMARK_PAIRS_DIRECTORY=benchmarks/pairs \
KUBEFIT_BENCHMARK_CAMPAIGN_EVIDENCE_DIRECTORY=benchmarks/campaign-evidence \
  uvicorn api.main:app --reload
```

Keep Vite running and open the exact result through:

```text
http://127.0.0.1:5173/?benchmark=benchmark-<digest>
http://127.0.0.1:5173/?pair=benchmark-pair-<digest>
http://127.0.0.1:5173/?campaign=benchmark-campaign-evidence-<digest>
```

The browser sends only one validated artifact ID. The API selects that child of the
corresponding configured root and runs the complete filesystem loader before returning
the review. Roots must be regular directories, IDs cannot contain path components, and
symlinked artifact directories are rejected. Supplying more than one of `benchmark`,
`pair`, and `campaign` is rejected as ambiguous. Missing configuration and unknown IDs
return 404 without exposing arbitrary filesystem paths.

For the packaged image, mount the evidence roots read-only and use the same queries on
port 8000:

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/benchmarks/results:/var/lib/kubefit/results:ro" \
  -v "$PWD/benchmarks/pairs:/var/lib/kubefit/pairs:ro" \
  -v "$PWD/benchmarks/campaign-evidence:/var/lib/kubefit/campaign-evidence:ro" \
  -e KUBEFIT_BENCHMARK_RESULTS_DIRECTORY=/var/lib/kubefit/results \
  -e KUBEFIT_BENCHMARK_PAIRS_DIRECTORY=/var/lib/kubefit/pairs \
  -e KUBEFIT_BENCHMARK_CAMPAIGN_EVIDENCE_DIRECTORY=/var/lib/kubefit/campaign-evidence \
  kubefit:dev
```

```text
http://127.0.0.1:8000/?benchmark=benchmark-<digest>
http://127.0.0.1:8000/?pair=benchmark-pair-<digest>
http://127.0.0.1:8000/?campaign=benchmark-campaign-evidence-<digest>
http://127.0.0.1:8000/?showcase=decision-journey
```

`FULL ARTIFACT REPLAY` means the server rechecked the exact file set, every payload
size and SHA-256 digest, the aggregate content digest, raw k6/summary relationships,
the generated report, and the policy verdict. It still does not make a controlled
approximately 160-second run representative of production traffic. The environment
variables expose no artifacts unless an operator deliberately provisions the
directories; KubeFit does not upload or host local evidence automatically.

`CAMPAIGN FULL ARTIFACT REPLAY` additionally rechecks the plan, every nested pair and
benchmark, chronological non-overlap, schedule, and completion decision. Its horizontal
bars show when each block occurred and how long its measurement window lasted. They do
not compare performance magnitude. `aggregation_performed: false` is retained in the
API contract, and the UI does not calculate an average or confidence interval.

`PAIR FULL ARTIFACT REPLAY` additionally rechecks both embedded result bundles and
the pair decision, then derives six lower-is-better signals. Each row places the two
order-specific deltas around a zero line. The connecting minimum–maximum segment is
only the observed range of those two points. It is not a confidence interval, variance
estimate, or statistical significance claim. When a baseline is zero, the UI uses the
native-unit delta instead of inventing an infinite percentage.

Verify the frontend independently with:

```bash
npm --prefix dashboard run build
npm --prefix dashboard test
npm --prefix dashboard audit
```

The two-process setup is for hot-reload development. The Docker build runs the
locked frontend build in a separate Node stage and copies only `dist/` into the
non-root Python runtime. The Helm Service therefore serves the same dashboard at
`/` without Vite or a CORS policy.

## Run a published proposal benchmark

The benchmark command is intentionally restricted to an explicit kind context and
requires acknowledgement because it temporarily applies before and after manifests.
Create an immutable proposal from an eligible analysis without retyping its target:

```bash
kubefit propose \
  --analysis .kubefit/analysis.json \
  --repository-root . \
  --manifest deploy/demo/overprovisioned-api.yaml
```

Then keep separate Service and Prometheus port-forwards running:

```bash
kubectl --context kind-kubefit proxy \
  --port=8001 \
  --address=127.0.0.1 \
  --accept-hosts='^127\.0\.0\.1$'

kubectl --context kind-kubefit port-forward \
  -n monitoring service/monitoring-kube-prometheus-prometheus 9090:9090

kubefit benchmark \
  --proposal .kubefit/proposals/proposal-<digest> \
  --target-url http://127.0.0.1:8001/api/v1/namespaces/kubefit-demo/services/http:overprovisioned-api:80/proxy/ \
  --prometheus-url http://localhost:9090 \
  --context kind-kubefit \
  --confirm-disposable-cluster \
  --execution-order before-after

kubefit benchmark \
  --proposal .kubefit/proposals/proposal-<digest> \
  --target-url http://127.0.0.1:8001/api/v1/namespaces/kubefit-demo/services/http:overprovisioned-api:80/proxy/ \
  --prometheus-url http://localhost:9090 \
  --context kind-kubefit \
  --confirm-disposable-cluster \
  --execution-order after-before
```

The command locks the target Deployment, revalidates its analysis identity, restores
the before manifest on every exit path, and publishes a separate immutable result
under `benchmarks/results/`. If the repository YAML contains multiple documents,
only the selected Deployment document is applied; the complete source remains in
the proposal solely as review provenance. The fixed profile requires each phase to
meet its promised iteration minimum. Each run may contain at most one extra
scheduling-boundary iteration; missing work or two extra iterations is invalid. Use
the Kubernetes API Service proxy for the application target: `kubectl port-forward`
selects one backing Pod and disconnects when the benchmark rollout replaces it.
Prometheus can remain port-forwarded because the benchmark does not roll it out.

The two commands counterbalance chronological order and first publish independent
result artifacts. Every artifact records its actual order and emits a warning because
one sequential trial cannot separate resource effects from warm-up or time drift. The
pair command then verifies their identity, order, and policy-state agreement and, only
for PASS, publishes a self-contained immutable bundle:

```bash
kubefit benchmark-pair \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest> \
  --output-dir benchmarks/pairs
```

The JSON status is `pass` only when both fully verified artifacts reference the same
proposal, use opposite orders and identical profile/cost bases, expose identical
non-order policy check states, and both individual verdicts pass. PASS prints the
`benchmark-pair-<digest>` path and reuse state. The directory contains the canonical
assessment, report, hashes, and complete copies of both result bundles. `fail` or
`invalid` returns exit code 2 after printing the reasons and does not publish a pair.

For a CI-only verdict, use the same two results with `check`. When GitHub Actions sets
`GITHUB_STEP_SUMMARY`, KubeFit appends the trial and policy tables automatically and
returns exit code 2 for FAIL or INVALID:

```bash
kubefit check \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest>
```

Before running a benchmark, a PR can be restricted to the currently recognized
Deployment fields. Obtain the base version outside the repository, then inspect the
semantic YAML change:

```bash
git show origin/main:deploy/api.yaml > /tmp/kubefit-base.yaml
kubefit inspect-change \
  --base /tmp/kubefit-base.yaml \
  --candidate deploy/api.yaml \
  --namespace demo \
  --deployment api
```

The command passes only when at least one image, replica, or CPU/memory resource field
changed and no other Deployment field changed. Unsupported changes expose JSON Pointer
paths but suppress their values. The untrusted-input boundary also rejects duplicate
mapping keys, YAML anchors/aliases, symlinked inputs, and manifests above 1 MB. This
scope result is not yet cryptographically bound
to an arbitrary benchmark pair. For an existing KubeFit resource proposal, close that
gap with the end-to-end gate:

```bash
kubefit validate \
  --proposal .kubefit/proposals/proposal-<digest> \
  --base /tmp/kubefit-base.yaml \
  --candidate deploy/api.yaml \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest>
```

`validate` fully reloads every artifact, verifies both results reference the proposal,
requires the provided manifests to be byte-identical to the proposal input and output,
and enforces the Pair verdict. This exact binding intentionally rejects even a
semantically equivalent rewritten candidate because it was not the measured proposal
payload. Generic image/replica execution bundles remain outside the current boundary.

### Consume the validation gate as a GitHub Action

The repository root contains a Docker action that maps five required evidence paths
directly to `kubefit validate`. On a Linux runner, after checking out the calling
repository and downloading or generating its evidence directories:

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

Docker container actions require a Linux runner with Docker. The current source was
verified by building `Dockerfile.action`, mounting the retained evidence read-only, and
observing PASS plus an appended Step Summary. The Action image is separate from the
non-root API image so Linux runner-owned `0700` evidence remains readable; it contains
no dashboard or cluster tooling and invokes only the read-only validation command. A
real hosted-runner check remains release evidence, not a local claim.

After building the Action image, verify both packaged verdict paths with retained
repository-relative evidence:

```bash
deploy/local/verify-safety-action.sh \
  kubefit-action:local \
  .kubefit/proposals/proposal-<digest> \
  .kubefit/proposals/proposal-<digest>/manifests/before/<source-path> \
  .kubefit/proposals/proposal-<digest>/manifests/after/<source-path> \
  benchmarks/pairs/benchmark-pair-<digest>/trials/benchmark-<first> \
  benchmarks/pairs/benchmark-pair-<digest>/trials/benchmark-<second>
```

The script first expects PASS, then appends a newline to a temporary candidate and
expects INVALID with exit code 2. Repository evidence is mounted read-only; temporary
files and both `--rm` containers are removed on completion.

### Freeze a generic Deployment change input

Before a future image or replica benchmark can claim a result, freeze its exact inputs:

```bash
kubefit prepare-change \
  --base /tmp/base.yaml \
  --candidate deploy/api.yaml \
  --namespace demo \
  --deployment api \
  --output-dir .kubefit/changes
```

The output is a content-addressed `change-<digest>` directory containing canonical
change JSON and byte-exact manifests. Loading the bundle verifies its file set, sizes,
hashes, aggregate digest, directory identity, and replayed semantic decision. Repeating
the command with identical input reuses the same artifact. This is an input artifact;
it does not yet authorize cluster mutation or claim performance/fault evidence.

### Execute the frozen change on disposable kind

Only after reviewing the semantic change, apply the exact bundle to an expendable
local kind cluster:

```bash
kubefit execute-change \
  --change .kubefit/changes/change-<digest> \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster \
  --rollout-timeout-seconds 120
```

The command accepts only an explicit `kind-*` context. It applies and waits for the
base, applies and waits for the candidate, and then reapplies and waits for the base.
Base restoration is also attempted after a failed rollout or `Ctrl+C`; a restoration
failure is reported as the dominant safety error. A PASS proves only that both exact
Deployment manifests reached the controller's rollout-ready condition and the base was
restored. It does not measure latency, cost, throttling, OOM behavior, or fault recovery.

The fixed profile at `benchmarks/k6/resource_profile.js` also accepts
`KUBEFIT_CHANGE_ID=change-<digest>` instead of `KUBEFIT_PROPOSAL_ID`. Exactly one
identity is required. The generic executor validates that emitted identity and retains
the typed summary bytes, raw k6 samples, timestamps, recovery observation, and their
hashes. The internal generic performance runner applies and measures base, applies and
measures candidate, then restores base before returning a replayable latency/error/load/
recovery verdict. Run and persist that contract with:

```bash
kubefit benchmark-change \
  --change .kubefit/changes/change-<digest> \
  --target-url http://127.0.0.1:8080 \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster \
  --execution-order before-after \
  --results-dir .kubefit/change-performance
```

The target URL must remain reachable from the host-side k6 process throughout both
160-second profiles. The command holds the same per-context Deployment lock as resource
benchmarks. It restores base, atomically writes exact summary/raw evidence plus policy
and verdict, reloads the artifact, then prints only its identity and status. FAIL or
INVALID evidence remains on disk and produces exit code 2.

Collect the opposite order as a separate restored artifact:

```bash
kubefit benchmark-change \
  --change .kubefit/changes/change-<digest> \
  --target-url http://127.0.0.1:8080 \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster \
  --execution-order after-before
```

The run model derives chronological order from non-overlapping measurement timestamps
and rejects a declared order that conflicts with them. Both commands restore base.
They do not yet form a counterbalanced Pair verdict.

If an aggressive candidate fails, keep that immutable result and do not repeat the
same trial until it passes. A documented workload-specific CPU floor can be raised
from retained schema v2 evidence without recollecting or altering percentiles:

```bash
kubefit reanalyze \
  --analysis .kubefit/analysis.json \
  --minimum-cpu-millicores 20 \
  > .kubefit/refined-analysis.json
```

`reanalyze` preserves the target, workload identity, observed usage, price assumptions,
and every other policy input. It rejects schema v1 inputs and attempts to lower the
previous CPU floor. Retain the failed benchmark as the reason for the override, create
a new proposal, and preregister a new campaign before examining the refined candidate.
The command does not turn a failed benchmark into a pass or authorize publication.

### Preregister repeated pair collection

Do this before looking at any repeated-pair outcomes. Choose the number of pairs from
the experiment's time budget and decision risk; KubeFit deliberately does not label a
particular count statistically sufficient. Generate at least 16 bytes of seed outside
the repository and create the immutable schedule:

```bash
umask 077
openssl rand 32 > /tmp/kubefit-campaign.seed

kubefit benchmark-campaign-plan \
  --proposal .kubefit/proposals/proposal-<digest> \
  --planned-pairs 4 \
  --randomization-seed-file /tmp/kubefit-campaign.seed \
  --output-dir benchmarks/campaigns
```

The command does not run a benchmark. Read `report.md`, then execute every block in
the printed order using `kubefit benchmark --execution-order ...` twice followed by
`kubefit benchmark-pair`. Keep cluster version, node shape, proposal, load profile,
traffic source, and cost inputs fixed. If a processing failure produces no PASS pair,
repeat that same preregistered block rather than changing the schedule.

Check progress by repeating `--pair` for every completed block. Input path order does
not matter because verified measurement timestamps determine block order:

```bash
kubefit benchmark-campaign-check \
  --plan benchmarks/campaigns/benchmark-campaign-<digest> \
  --pair benchmarks/pairs/benchmark-pair-<block-1-digest> \
  --pair benchmarks/pairs/benchmark-pair-<block-2-digest> \
  --pair benchmarks/pairs/benchmark-pair-<block-3-digest> \
  --pair benchmarks/pairs/benchmark-pair-<block-4-digest> \
  --output-dir benchmarks/campaign-evidence
```

`complete` returns exit 0. A valid prefix is `incomplete` and returns exit 2, preventing
results-based early stopping from looking complete. Duplicate evidence, extra pairs,
overlapping trial/block times, a different proposal, profile or cost basis, and a
first-order schedule mismatch are `invalid` and also return exit 2. The checker does
not calculate an average, variance, confidence interval, or publication authorization.
The seed hash commits to the supplied seed but cannot prove how randomly it was chosen.

On COMPLETE, the command writes and immediately reloads
`benchmarks/campaign-evidence/benchmark-campaign-evidence-<digest>`. It contains the
plan, completion, report, and complete copies of every pair and nested benchmark result.
For `N` pairs the exact total is `21N + 5` files; three pairs therefore produce 68.
Reversing `--pair` arguments reuses the same bytes because retained timestamps define
chronology. INCOMPLETE or INVALID creates no output directory. The self-contained
design is portable but duplicates raw k6 bytes, so inspect available disk before a
large campaign.

## Publish a verified Draft PR

Publication requires a passing immutable benchmark result, a passing immutable pair
containing that result, a clean repository on an attached branch, and a credential-free
public GitHub remote. Git push authentication must already be available through an SSH
agent or Git credential helper. Supply the
GitHub API token through an environment variable, never as a command argument. For
example, an existing GitHub CLI login can provide it without placing the value in
shell history:

Run the read-only preflight first:

```bash
GITHUB_TOKEN="$(gh auth token)" kubefit publish-check \
  --proposal .kubefit/proposals/proposal-<digest> \
  --benchmark benchmarks/results/benchmark-<digest> \
  --benchmark-pair benchmarks/pairs/benchmark-pair-<digest> \
  --repository-root . \
  --remote origin
```

It verifies the artifacts and clean checkout, inspects deterministic local and
remote branch state, and performs only a GitHub repository GET when the token is
present. It never creates a commit, branch, or PR. Resolve every `blockers` entry
before publication. Treat `ready` as preflight evidence rather than proof of write
permission; organization rules and token scopes are enforced only by the live write.

Then publish with an explicit acknowledgement:

```bash
GITHUB_TOKEN="$(gh auth token)" kubefit publish \
  --proposal .kubefit/proposals/proposal-<digest> \
  --benchmark benchmarks/results/benchmark-<digest> \
  --benchmark-pair benchmarks/pairs/benchmark-pair-<digest> \
  --repository-root . \
  --remote origin \
  --confirm-publish
```

The token needs repository pull-request write access, while Git push uses the
repository's existing Git authentication. `--github-token-env NAME` can select a
different environment variable by name; the token value is never a CLI option.

To attach a completed repeated campaign, add the same explicit option to both commands:

```bash
--benchmark-campaign-evidence \
  benchmarks/campaign-evidence/benchmark-campaign-evidence-<digest>
```

The campaign must reference the same proposal and contain the mandatory
`--benchmark-pair`. KubeFit replays the entire nested artifact and adds its IDs,
completion count, chronological block table, and a no-significance caveat to the PR
body. Omitting the option preserves the standard pair-only gate. This references
verified evidence in the review contract; it does not upload the raw campaign bundle to
GitHub. Choose the option before the first publication because KubeFit refuses to
rewrite a divergent existing Draft PR body.

The command creates or reuses the deterministic local commit, creates the remote
branch only if absent, and opens or reuses an exact matching Draft PR. It prints
only repository, branch, commit, PR URL/number, and reuse flags as JSON. It does not
merge, approve, mark ready, deploy, persist credentials, or delete a remote branch.
This workflow is covered by isolated local tests but has not yet been exercised
against a disposable live GitHub repository.

For the first authenticated verification, follow
[the live GitHub demonstration runbook](live-github-demo.md). It uses a separately
named private repository, makes blocked preflight exit nonzero, captures two-run
idempotency evidence outside the checkout, and archives the target by default.

## Remove the environment

This permanently deletes the local kind cluster and its Prometheus data:

```bash
./deploy/local/down.sh
```

Set `KUBEFIT_CLUSTER_NAME` to use a different cluster name. The Prometheus chart
version can be overridden temporarily with `KUBEFIT_PROMETHEUS_CHART_VERSION`, but
changes should be validated before updating the pinned repository default.

## Verify the packaged API

With Docker and the disposable cluster running, execute:

```bash
./deploy/local/verify-kubefit-chart.sh
```

The script never changes the ambient kubectl context. It builds the local image,
loads it into the explicit kind cluster, verifies the tokenless API health endpoint
and packaged dashboard, temporarily exercises namespace-scoped observation RBAC,
proves denied watch/Secret/update/cross-namespace operations, and restores tokenless
defaults. It leaves the KubeFit release installed in `kubefit-system` but does not
change the demo or Prometheus releases.

## Generate a verified local image SBOM

Docker Desktop with Docker Scout and `jq` are required. After building the image,
run:

```bash
docker build --tag kubefit:dev .
./deploy/local/generate-image-sbom.sh
```

The script resolves `kubefit:dev` to its full local image ID, generates SPDX 2.3
against that ID, verifies the expected Python runtime and absence of Node/npm
packages, then atomically publishes `artifact.json` and `sbom.spdx.json` under
`.kubefit/supply-chain/image-sbom-<digest>/`. A second run validates the stored
hash, byte size, SPDX structure, and package assertions before reporting
`reused: true`.

Set `KUBEFIT_IMAGE_REFERENCE` to inspect a different local reference and
`KUBEFIT_SBOM_OUTPUT_DIR` to select another evidence directory. The output is a
package inventory only. It does not apply a CVE policy, bind the image to a Git
revision, sign evidence, or publish an OCI attachment.
