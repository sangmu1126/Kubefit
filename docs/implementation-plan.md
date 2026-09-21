# Implementation plan

KubeFit is built as a sequence of verifiable vertical slices. Each phase must
produce evidence that can be demonstrated independently before the next phase
adds more automation.

## Guiding constraints

- A recommendation is calculated per container replica, because Kubernetes
  resource requests and limits are applied to each Pod.
- The recommendation path never mutates a Kubernetes cluster.
- Cost reduction and operational risk are reported separately.
- Insufficient or unrepresentative observations must be visible to the user.
- Every Git change starts as a reviewable draft pull request.

## Development evidence

Each meaningful implementation slice must add or update an entry in the
[`devlog`](devlog/README.md). Entries record why the work mattered, how the decision
was implemented, which alternatives were rejected, and what reproducible evidence
supports the resulting claim. Failures and remaining uncertainty are documented at
the time they are observed.

## Phase 1: trustworthy resource analysis

**Status: complete for the MVP (2026-08-21).** Validation evidence is recorded in
development journal entries [0001](devlog/0001-trustworthy-analysis.md) through
[0006](devlog/0006-resource-input-boundary.md).

**Goal:** produce a deterministic recommendation from correctly scoped metrics.

- Read a Deployment, its target container, replicas, resources, and matching Pods.
- Query CPU and memory as per-Pod time series rather than a Deployment-wide sum.
- Authorize ReplicaSets through the current Deployment owner UID.
- Clip same-name history at the current Deployment creation timestamp.
- Optionally retain observed ReplicaSet identity after API deletion.
- Parse Kubernetes quantities exactly and support `matchLabels` plus
  `matchExpressions` selectors.
- Use the highest per-Pod CPU P95 and memory P99 so one busy replica is not hidden
  by an average.
- Report sample count and observation coverage.
- Gate actionable recommendations on metric coverage, sample count, and stable replicas.
- Recommend CPU and memory requests with explicit margins and rounding rules.
- Report CPU and memory request changes separately; never add millicores and MiB.

**Done when:** unit tests cover multiple replicas, missing samples, unit conversion,
rounding, and both upsize and downsize recommendations.

## Phase 2: cost and safety evaluation

**Status: complete for the MVP (2026-08-21).** Explicit cost assumptions, runtime
safety signals, and the patch eligibility gate are recorded in development journal
entries [0007](devlog/0007-explicit-cost-model.md) through
[0009](devlog/0009-patch-eligibility-gate.md).

**Goal:** turn a capacity recommendation into a defensible decision.

- Calculate current and recommended monthly costs from explicit CPU and memory
  unit prices, replica count, and monthly hours.
- Collect CPU throttling, container restarts, OOMKilled events, and usage maxima.
- Gate automatic patch generation when observation coverage is insufficient.
- Classify risks from observed signals, not only from the recommendation formula.
- Include all assumptions and warning reasons in the result.

**Done when:** a result explains its price assumptions, projected savings, confidence,
and the signals that produced each risk classification.

## Phase 3: manifest patch generation

**Status: complete for the MVP (2026-08-21).** Minimal scalar replacement, stale
input protection, ambiguity rejection, and golden evidence are recorded in
development journal entry [0010](devlog/0010-minimal-manifest-patch.md).

**Goal:** create a minimal, reviewable GitOps change without touching the cluster.

- Locate the selected Deployment and container in a repository manifest.
- Preserve unrelated YAML fields and formatting where practical.
- Change only CPU and memory requests/limits selected by the policy.
- Produce a unified diff and structured recommendation report.
- Refuse ambiguous workload or container matches.

**Done when:** golden-file tests prove that only the intended container resources
change and invalid or ambiguous manifests fail safely.

## Phase 4: reproducible before/after benchmark

**Status: complete for the MVP (2026-08-22).** Immutable proposal inputs and the
fixed load/verdict contract were completed on 2026-08-21 and are documented in
development journal entries [0011](devlog/0011-reproducible-proposal-bundle.md),
[0012](devlog/0012-fixed-load-profile.md), and
[0013](devlog/0013-restoring-benchmark-runner.md). The restoring execution core is
also complete. Entry [0014](devlog/0014-aligned-benchmark-measurement.md) adds the
time-aligned k6, Prometheus, Kubernetes-delta, and proposal-cost collector.
Entry [0015](devlog/0015-immutable-benchmark-result.md) completes durable raw
evidence and atomic result publication. Entry
[0016](devlog/0016-locked-benchmark-cli.md) completes the Deployment-scoped execution
lock and local CLI composition. Entry
[0017](devlog/0017-proposal-cli.md) completes the analysis-bound proposal command and
live Deployment identity preflight. Entry
[0018](devlog/0018-target-document-isolation.md) separates full review provenance
from single-Deployment executable manifests. Entry
[0019](devlog/0019-persistent-local-observation.md) preserves local Prometheus
history across Pod recreation so readiness can accumulate honestly. Entry
[0020](devlog/0020-readiness-cli.md) makes that progress and its time assumptions
machine-readable. Entry [0035](devlog/0035-live-k6-evidence-boundary.md) runs the
unchanged baseline against the real Service, fixes P99 summary export, and aligns
the offered-load contract with k6's observed boundary scheduling. Entry
[0036](devlog/0036-k6-process-success-boundary.md) rejects k6's structured script
exception even when the process exits zero, while preserving mandatory typed-output
checks. Entry
[0037](devlog/0037-controlled-demo-observation.md) replaces the meaningless local
24-hour idle wait with a fixed one-hour, loaded, explicitly non-production demo
profile while preserving the multi-day production default. Entry
[0038](devlog/0038-live-demo-benchmark.md) records the eligible schema v2 proposal,
three rejected harness outcomes, rollout stabilization, one-iteration k6 boundary,
and the final passing live comparison with mandatory restoration.

**Goal:** validate savings without concealing performance regressions.

- Provide a local demo cluster setup and an intentionally overprovisioned service.
- Run a fixed load profile before and after the proposed patch.
- Compare request cost, latency P95/P99, throttling, OOM events, error rate, and
  recovery time after a traffic spike.
- Store machine-readable benchmark results and a human-readable summary.

**Completion evidence:** the documented disposable-kind workflow produced immutable
benchmark `benchmark-f84d0caf061d50a5d93bc03088eb0247` with verdict `pass`, then
restored the original Deployment at 2/2 Ready. The result reported 0% request errors,
no throttling, OOM, or restarts, bounded P95/P99 latency changes, and a separately
labeled 98.088% illustrative request-cost reduction.

## Phase 5: GitHub draft pull request

**Status: complete for the MVP (2026-08-22).** Entry
[0021](devlog/0021-pull-request-plan.md) establishes
load-time semantic verification for proposal/result artifacts and produces a
deterministic, draft-only, one-file review contract. Entry
[0022](devlog/0022-transactional-git-commit.md) adds stale-safe, one-file local branch
commits with rollback and idempotent reuse. Entry
[0023](devlog/0023-idempotent-draft-pull-request.md) adds compare-and-swap branch
publication and exact-contract Draft PR creation with ambiguous-response recovery.
Entry [0024](devlog/0024-publish-cli-secret-boundary.md) exposes the complete command
with environment-only token intake, explicit mutation acknowledgement, and safe JSON
output. Entry [0025](devlog/0025-live-publication-preflight.md) adds mutation-free
artifact, local Git, remote ref, and API readiness diagnostics. Authentication repair,
a disposable target, and the live two-run demonstration remain open. Entry
[0026](devlog/0026-live-demo-contract.md) makes blocked readiness fail closed for
automation and defines the exact disposable setup, two-run assertions, independent
GitHub evidence, and archive-first cleanup procedure. Entry
[0027](devlog/0027-publication-evidence-verifier.md) binds that exact five-file proof
back to the immutable proposal/benchmark and emits a content-addressed verification
result. Entry [0039](devlog/0039-authenticated-publication-preflight.md) validates
the passing live artifacts, clean repository, absent publication branch, GitHub
repository identity, and authenticated read access without mutation. Creating the
separate private target and performing the live two-run publication were originally
left open. Entry [0040](devlog/0040-live-origin-draft-pr.md) records the revised
target decision and actual `origin` publication: the first run created Draft PR #1,
the second reused its exact branch, commit, and PR, and an independent GitHub query
confirmed one changed manifest file. No merge or deployment was performed.

**Goal:** deliver the validated change through the repository's review workflow.

- Create a dedicated branch and commit only the manifest patch and report.
- Open a draft pull request with evidence, expected cost change, risks, benchmark
  results, and rollback guidance.
- Make repeated runs idempotent and never commit credentials.
- Leave merge and cluster rollout to humans and the existing GitOps controller.

**Completion evidence:** [Draft PR #1](https://github.com/sangmu1126/kubefit/pull/1)
is open against `main` from commit `9a4697302d5fe727f7bbdd2a84259facc154d4e5`.
Its body names the exact proposal and passing benchmark, reports cost and safety
evidence, and includes rollback guidance. GitHub independently reports Draft state
and exactly one changed file, `deploy/demo/overprovisioned-api.yaml`.

## Phase 6: presentation layer and packaging

**Status: complete for the MVP (2026-08-24).** Entry
[0028](devlog/0028-least-privilege-helm-package.md) adds a non-root API image and
Helm chart with tokenless defaults, explicit resources/probes, and namespace-scoped
opt-in read-only RBAC. Entry [0029](devlog/0029-kind-helm-integration.md) completes the
local image build, kind installation, live health probe, RBAC allow/deny matrix, and
tokenless restoration evidence. Entry
[0030](devlog/0030-explainable-review-dashboard.md) adds an API-backed review surface
for recommendation evidence, cost, risk, and the patch gate without duplicating
analysis logic. Entry [0031](devlog/0031-packaged-dashboard.md) builds that UI in a
separate Node stage, serves it from the hardened API image, and verifies it through
the kind Helm Service without expanding RBAC. Entry
[0032](devlog/0032-verified-image-sbom.md) binds a tamper-evident SPDX inventory to
the exact local image ID. Entry
[0033](devlog/0033-analysis-artifact-review.md) connects CLI analysis output to the
dashboard through an API-owned integrity review while exposing schema v1 replay
limits. Entry [0034](devlog/0034-replayable-analysis-schema.md) adds schema v2
aggregate observations and versioned policy inputs, replays the complete decision,
and preserves v1 content identities. Entry
[0041](devlog/0041-repository-quality-gates.md) adds independent Python, dashboard,
Helm, and Docker CI jobs with read-only permissions, bounded execution, and
commit-pinned external Actions. Entry
[0042](devlog/0042-index-bound-benchmark-review.md) adds a compact API-replayed
benchmark review and before/after visualization while explicitly separating indexed
metadata binding from complete raw-evidence verification. Entry
[0043](devlog/0043-shareable-full-benchmark-review.md) adds an operator-configured
artifact-ID URL that reuses complete filesystem verification without automatically
publishing local evidence. Entry
[0044](devlog/0044-packaged-runtime-ci-gate.md) closes the phase by starting the exact
packaged image in CI and checking its non-root runtime, health endpoint, dashboard,
disabled storage default, failure logs, and deterministic cleanup. Raw percentile
recomputation from Prometheus, vulnerability policy, source provenance, signing, and
external image/chart publication remain post-MVP hardening work.

**Goal:** make the completed workflow easy to install and demonstrate.

- Add a small dashboard over the existing API after the CLI workflow is complete.
  Example review, v1 integrity review, v2 recommendation replay, and packaged
  delivery are complete. Indexed benchmark verdict replay is complete; raw percentile
  replay remains explicitly out of scope. A configured stored result can now be opened
  by artifact-ID query after full server-side verification.
- Package KubeFit with a Helm chart and least-privilege read-only RBAC.
- Document security boundaries, metric prerequisites, limitations, and rollback.

HPA recommendations, multi-cloud pricing catalogs, predictive incident detection,
Terraform generation, and an AI chatbot remain outside the first release.

## Post-v0.1.0 correctness hardening

**Status: in progress (2026-08-24).** Entry
[0046](devlog/0046-pod-bound-prometheus-evidence.md) closes the highest-priority
collector audit finding by retaining Prometheus Pod identities and timestamps,
pairing CPU/memory evidence, and gating extreme current-Pod sample skew. The
immutable `v0.1.0` source tag remains unchanged; these fixes target a later patch
release. Entry [0047](devlog/0047-interrupt-safe-benchmark-restoration.md) then
closes the benchmark interruption gap by restoring the original Deployment before
propagating Ctrl+C and retaining both causes if restoration fails. Entry
[0048](devlog/0048-verified-public-package-release.md) adds a source-bound
multi-architecture image and OCI chart publisher with a credential-free anonymous
pull/runtime gate. Live publication remains open until that hosted gate passes.
Entry [0049](devlog/0049-apache-license-distribution.md) aligns the Apache-2.0
declaration with a complete root license and carries it through wheel and image
distribution without inventing an empty NOTICE file. Entry
[0050](devlog/0050-hash-locked-python-environments.md) then separates runtime,
development, and build dependency snapshots, requires hashes in CI and Docker, and
proves the same locks on Python 3.12 and 3.14. Entry
[0051](devlog/0051-supported-python-ci-matrix.md) continuously applies the same
locked quality gate to Python 3.12, 3.13, and 3.14 on hosted runners without
fail-fast cancellation. Entry
[0052](devlog/0052-counterbalanced-benchmark-order.md) adds deterministic
before-first and candidate-first execution, rejects overlapping intervals, and
carries the remaining single-trial order bias into artifacts and PR review notes.
Entry [0053](devlog/0053-counterbalanced-pair-assessment.md) then fully verifies and
binds two opposite-order artifacts into one content-addressed policy agreement
decision without claiming two samples establish statistical significance.
Entry [0054](devlog/0054-persisted-pair-publication-gate.md) persists PASS as a
self-contained replayable artifact and makes that artifact, both member IDs, and the
primary-result membership check mandatory throughout preflight, publication, and
offline verification.
Entry [0055](devlog/0055-counterbalanced-metric-range-review.md) derives six
order-aware metric comparisons after full replay and renders the two observed points,
their direction, and minimum–maximum range in both the Draft PR and dashboard without
calling it a confidence interval.
Entry [0056](devlog/0056-preregistered-benchmark-campaign.md) freezes repeated-pair
count, balanced randomized first-order schedule, and a complete-all stopping rule
before collection, then verifies exact chronological evidence without calculating a
statistical effect.
Entry [0057](devlog/0057-self-contained-campaign-evidence.md) persists only COMPLETE
campaigns with their plan and every full pair bundle, then independently replays the
entire nested evidence tree without changing the default publication requirement.
Entry [0058](devlog/0058-optional-campaign-pr-evidence.md) lets an operator explicitly
bind that evidence to preflight, Draft PR body, publication output, and offline proof,
while requiring the mandatory pair to belong to the campaign and leaving omission
fully backward-compatible.
Entry [0059](devlog/0059-campaign-review-dashboard.md) adds an opt-in, fully replayed
campaign API and timeline that compares preregistered with observed block order and
duration while making the absence of statistical aggregation machine-readable.
Entry [0060](devlog/0060-validation-informed-cpu-floor.md) records a live controlled
demo in which the counterbalanced gate rejected an aggressive floor, adds a monotonic
schema v2 reanalysis path, and preserves an incomplete repeated campaign instead of
weakening latency policy or selecting favorable reruns.
Entry [0061](devlog/0061-live-pair-draft-publication.md) exercises the pair-only MVP
publication boundary against the project repository, proves idempotent Draft PR reuse
and offline evidence verification, and captures the fully replayed pair dashboard
without representing the incomplete campaign as attached evidence.
Entry [0062](devlog/0062-verified-v020-release.md) binds the `v0.2.0` source tag to the
Python, dashboard, Helm, and image versions, publishes both multi-architecture image
and OCI chart, and verifies anonymous pull plus container startup before creating the
public GitHub release. It also retires the superseded generated Draft PR while keeping
the current validation-backed proposal unmerged.
Entry [0063](devlog/0063-generated-evidence-package-boundary.md) audits a dirty local
build rather than only a clean CI checkout, finds ignored pair and campaign artifacts
inside the resulting wheel, and makes generated-data exclusion an executable wheel and
Docker-context contract. It also distinguishes a local demo bundle from evidence that
has actually been approved for public distribution.
Entry [0064](devlog/0064-public-replayable-pair-demo.md) publishes that inspected pair
only after explicit approval, verifies an anonymous redownload against both the pinned
and GitHub-reported digest, and reduces the reviewer path to one loopback-only Docker
command that still performs full server-side artifact replay.
Entry [0065](devlog/0065-decision-journey-showcase.md) adds a focused read-only
presentation route over the retained failure, monotonic refinement, API-replayed Pair,
and Draft PR boundary. It keeps recorded values linked to committed evidence, derives
the active PASS from the existing review API, and preserves the older public image's
compatible Pair-detail default until a release includes the new frontend.
Entry [0066](devlog/0066-verified-v030-showcase-release.md) publishes that frontend as
the `v0.3.0` multi-architecture image and OCI chart, passes anonymous package checks,
and verifies the one-command Showcase against the public image. It deliberately keeps
the controlled Pair on its original `v0.2.0` asset and digest rather than relabeling
historical evidence as newly collected release data.

Entry [0067](devlog/0067-operator-triggered-verified-demo.md) corrects the remaining
demonstration gap: the Showcase now waits for visible operator actions, runs the
existing recommendation and full Pair replay APIs in sequence, and unlocks recorded
YAML and Draft PR evidence only after replay `PASS`. Live calculations and historical
measurements are labeled separately, and the flow remains read-only.

Entry [0068](devlog/0068-verified-v031-interactive-release.md) packages that interaction
as `v0.3.1`, verifies the public multi-architecture image and OCI chart anonymously,
then executes both APIs from a fresh public-image pull. The application version
advances without changing the digest-pinned `v0.2.0` controlled Pair.

The MVP implementation and public packaging are complete. Campaign drill-down,
supply-chain signing, or production-cluster evidence remain post-MVP and must not be
implied by the controlled-demo claims.

Entry [0069](devlog/0069-visual-decision-console.md) is a presentation-only follow-up.
It replaces sparse result cards with a guided Decision Console that visualizes the
resource path, evidence-source trace, rejected candidate, counterbalanced orders, and
policy checks. It does not expand the MVP or weaken the existing live/recorded and
read-only boundaries.

Entry [0070](devlog/0070-verified-v032-decision-console-release.md) packages that
surface as `v0.3.2`, verifies the image and chart anonymously, then replays the full
Pair from a fresh public-image pull. The application advances while the controlled
Pair remains the immutable, digest-pinned `v0.2.0` asset.

Entry [0071](devlog/0071-open-source-contribution-surface.md) closes the submission's
repository-entry gap with contribution, conduct, security, issue, and pull-request
contracts. It states the current single-maintainer and disabled private-reporting
limits rather than presenting planned community maturity as existing adoption.

Entry [0072](devlog/0072-submission-evidence-hardening.md) follows that recorded limit
with an enabled GitHub private vulnerability-reporting route, verifies the public
`v0.3.2` image as a scanner-reported 131-package SPDX inventory bound to its
immutable digest, and narrows submission wording to what the repository and
controlled benchmark evidence actually demonstrate.

Entry [0073](devlog/0073-korean-readme-entrypoint.md) makes the repository's default
README a Korean evaluator and newcomer path, preserves the complete English document
as `README.en.md`, and keeps bidirectional language navigation plus the same safety
and evidence boundaries in both entry points.

Entry [0074](devlog/0074-english-readme-entrypoint.md) supersedes only the default
language decision from entry 0073: `README.md` again provides the complete English
global open-source entry, while the unchanged Korean evaluator path remains available
as `README.ko.md` through bidirectional navigation.

Entry [0111](devlog/0111-korean-final-readme.md) supersedes that navigation decision
for the final repository state: `README.md` is the current Korean entry point and
`README.en.md` retains the English reference. Entries 0073 and 0074 remain historical
records of earlier language decisions.

Entry [0075](devlog/0075-observable-demo-walkthrough.md) makes the submission demo's
runtime inspectable without turning it into a new benchmark campaign. Both README
languages now connect Docker and kind status, an optional current Prometheus scrape,
packaged FastAPI health, and full Pair replay while explicitly separating live samples
from the digest-pinned historical verdict.

Entry [0076](devlog/0076-remove-unused-kube-proxy-target.md) removes the local
kube-proxy ServiceMonitor that was predictably unreachable in kind and unused by
KubeFit. The focused stack now reports no unhealthy targets while preserving the
kubelet/cAdvisor and kube-state-metrics inputs required for workload analysis.

Entry [0077](devlog/0077-target-health-first-walkthrough.md) makes kubelet/cAdvisor
target health the default low-load Prometheus inspection in both README languages.
PromQL remains optional with an explicit idle-`0m` explanation, avoiding unnecessary
load while preserving the live-scrape versus retained-verdict boundary.
