# Development journal

This journal records how KubeFit evolves, not only what the latest code does.
Each entry connects a product claim to a decision, implementation, and reproducible
piece of evidence.

## What to record

Every meaningful development slice answers the same questions:

1. **Why:** What user or operational risk made this work necessary?
2. **What:** What behavior, boundary, or artifact changed?
3. **How:** How was it implemented, including alternatives and trade-offs?
4. **Evidence:** Which test, command, metric, or benchmark supports the result?
5. **Decision:** What is safe to claim now, and what remains unknown?
6. **Next:** Which uncertainty should the next slice reduce?

Failures and rejected approaches belong in the journal. They are often stronger
evidence of engineering judgment than a final screenshot without context.

## Entry index

| Entry | Focus | Main outcome |
|---|---|---|
| [0001](0001-trustworthy-analysis.md) | Trustworthy analysis foundation | Correct per-Pod metrics and a real kind/Prometheus validation path |
| [0002](0002-observation-readiness.md) | Observation readiness | Block actionable changes when metric or replica evidence is incomplete |
| [0003](0003-rollout-history.md) | Rollout history | Preserve previous ReplicaSet metrics while excluding similar names |
| [0004](0004-recreation-boundary.md) | Recreation boundary | Isolate same-name workloads with owner UID and creation-time clipping |
| [0005](0005-identity-snapshot.md) | Identity snapshot | Recover observed ReplicaSet history after API deletion |
| [0006](0006-resource-input-boundary.md) | Resource input boundary | Close Phase 1 with exact quantities and complete selectors |
| [0007](0007-explicit-cost-model.md) | Explicit cost model | Separate transparent request-cost projections from readiness |
| [0008](0008-runtime-safety-signals.md) | Runtime safety signals | Ground risk in throttling, restarts, OOM, and signal coverage |
| [0009](0009-patch-eligibility-gate.md) | Patch eligibility | Close Phase 2 with one structured GitOps safety gate |
| [0010](0010-minimal-manifest-patch.md) | Minimal manifest patch | Generate a stale-safe four-scalar diff without reformatting YAML |
| [0011](0011-reproducible-proposal-bundle.md) | Proposal bundle | Publish immutable, content-addressed benchmark inputs atomically |
| [0012](0012-fixed-load-profile.md) | Fixed load profile | Version comparable before/after load and produce explicit safety verdicts |
| [0013](0013-restoring-benchmark-runner.md) | Restoring benchmark runner | Verify inputs, execute sequentially, and restore before returning |
| [0014](0014-aligned-benchmark-measurement.md) | Aligned benchmark measurement | Align k6, Prometheus, Kubernetes deltas, and proposal-fixed cost |
| [0015](0015-immutable-benchmark-result.md) | Immutable benchmark result | Retain raw evidence and publish a content-addressed, retry-safe result |
| [0016](0016-locked-benchmark-cli.md) | Locked benchmark CLI | Serialize Deployment mutation and compose the full local workflow |
| [0017](0017-proposal-cli.md) | Proposal CLI | Bind analysis identity and repository YAML into an immutable proposal |
| [0018](0018-target-document-isolation.md) | Target document isolation | Apply only the selected Deployment, never neighboring YAML documents |
| [0019](0019-persistent-local-observation.md) | Persistent local observation | Preserve Prometheus evidence across Pod and Docker restarts |
| [0020](0020-readiness-cli.md) | Readiness CLI | Explain whether to wait or intervene before creating a proposal |
| [0021](0021-pull-request-plan.md) | Pull request plan | Turn verified proposal and benchmark evidence into one review contract |
| [0022](0022-transactional-git-commit.md) | Transactional Git commit | Commit one verified file without leaving the checkout on the generated branch |
| [0023](0023-idempotent-draft-pull-request.md) | Idempotent draft pull request | Publish one verified commit without overwriting a remote branch or duplicate PR |
| [0024](0024-publish-cli-secret-boundary.md) | Publish CLI secret boundary | Compose verified publication without accepting or printing a token value |
| [0025](0025-live-publication-preflight.md) | Live publication preflight | Explain artifact, Git, remote, and credential blockers without mutation |
| [0026](0026-live-demo-contract.md) | Live demo contract | Make readiness machine-enforceable and define disposable two-run evidence |
| [0027](0027-publication-evidence-verifier.md) | Publication evidence verifier | Bind two-run GitHub proof back to immutable proposal and benchmark artifacts |
| [0028](0028-least-privilege-helm-package.md) | Least-privilege Helm package | Package the API non-root and scope optional observation RBAC by namespace |
| [0029](0029-kind-helm-integration.md) | kind Helm integration | Build, install, probe, verify RBAC denials, and restore tokenless defaults |
| [0030](0030-explainable-review-dashboard.md) | Explainable review dashboard | Visualize API-owned recommendation, cost, risk, and GitOps gating without duplicating analysis |
| [0031](0031-packaged-dashboard.md) | Packaged dashboard | Build and serve the immutable review UI from the hardened API image |
| [0032](0032-verified-image-sbom.md) | Verified image SBOM | Bind an immutable SPDX inventory to the exact local image ID and detect tampering |
| [0033](0033-analysis-artifact-review.md) | Analysis artifact review | Validate and visualize CLI artifacts while exposing schema v1 replay limits |
| [0034](0034-replayable-analysis-schema.md) | Replayable analysis schema | Replay v2 recommendations while preserving v1 identities and observation gates |
| [0035](0035-live-k6-evidence-boundary.md) | Live k6 evidence boundary | Export P99 and accept only matching boundary overshoot in real fixed-load runs |
| [0036](0036-k6-process-success-boundary.md) | k6 process success boundary | Reject script exceptions even when k6 returns exit code zero |
| [0037](0037-controlled-demo-observation.md) | Controlled demo observation | Separate real multi-day evidence from a fixed one-hour loaded demo window |
| [0038](0038-live-demo-benchmark.md) | Live demo benchmark | Turn three invalid harness runs into a restored, passing before/after result |
| [0039](0039-authenticated-publication-preflight.md) | Authenticated publication preflight | Prove the PASS artifacts and GitHub read boundary are ready without mutation |
| [0040](0040-live-origin-draft-pr.md) | Live origin Draft PR | Create once, reuse once, and independently verify the real one-file GitOps handoff |
| [0041](0041-repository-quality-gates.md) | Repository quality gates | Make Python, dashboard, Helm, and Docker failures visible as independent CI checks |
| [0042](0042-index-bound-benchmark-review.md) | Index-bound benchmark review | Replay indexed before/after evidence on the API and visualize its decision boundary |
| [0043](0043-shareable-full-benchmark-review.md) | Shareable full benchmark review | Open a configured result by artifact-ID URL after complete server-side verification |
| [0044](0044-packaged-runtime-ci-gate.md) | Packaged runtime CI gate | Start the built image and verify health, dashboard, defaults, logs, and cleanup |
| [0045](0045-integrated-release-boundary.md) | Integrated release boundary | Map the merged MVP to exact evidence, exclusions, and a tag-ready checklist |
| [0046](0046-pod-bound-prometheus-evidence.md) | Pod-bound Prometheus evidence | Pair CPU and memory by Pod/time and reject current-replica sample skew |
| [0047](0047-interrupt-safe-benchmark-restoration.md) | Interrupt-safe benchmark restoration | Restore the original Deployment before propagating Ctrl+C |
| [0048](0048-verified-public-package-release.md) | Verified public package release | Publish source-bound image/chart packages and prove anonymous pull access |
| [0049](0049-apache-license-distribution.md) | Apache license distribution | Carry the official license through source, wheel, image, and README |
| [0050](0050-hash-locked-python-environments.md) | Hash-locked Python environments | Separate and verify runtime, development, and build dependency snapshots |
| [0051](0051-supported-python-ci-matrix.md) | Supported Python CI matrix | Enforce the same locked quality gate on Python 3.12, 3.13, and 3.14 |
| [0052](0052-counterbalanced-benchmark-order.md) | Counterbalanced benchmark order | Run either chronological order and expose unavoidable single-trial bias |
| [0053](0053-counterbalanced-pair-assessment.md) | Counterbalanced pair assessment | Bind two verified opposite-order artifacts into one deterministic policy decision |
| [0054](0054-persisted-pair-publication-gate.md) | Persisted pair publication gate | Require replayable opposite-order evidence throughout GitOps publication |
| [0055](0055-counterbalanced-metric-range-review.md) | Counterbalanced metric range review | Show two order-specific changes without inventing statistical confidence |
| [0056](0056-preregistered-benchmark-campaign.md) | Preregistered benchmark campaign | Freeze repeated-pair order and stopping rules before observing outcomes |
| [0057](0057-self-contained-campaign-evidence.md) | Self-contained campaign evidence | Persist only COMPLETE campaigns with every replayable nested pair |
| [0058](0058-optional-campaign-pr-evidence.md) | Optional campaign PR evidence | Bind an explicit completed campaign to the Draft PR and offline proof |
| [0059](0059-campaign-review-dashboard.md) | Campaign review dashboard | Replay and visualize chronological blocks without statistical aggregation |
| [0060](0060-validation-informed-cpu-floor.md) | Validation-informed CPU floor | Raise an unsafe candidate monotonically and retain failed campaign evidence |
| [0061](0061-live-pair-draft-publication.md) | Live pair Draft publication | Publish one verified pair idempotently without overstating an incomplete campaign |
| [0062](0062-verified-v020-release.md) | Verified v0.2.0 release | Publish source-bound image/chart packages and verify anonymous installation |
| [0063](0063-generated-evidence-package-boundary.md) | Generated evidence package boundary | Keep ignored local benchmark evidence out of wheels and Docker images |
| [0064](0064-public-replayable-pair-demo.md) | Public replayable pair demo | Download, verify, and replay the exact pair from one loopback-only command |
| [0065](0065-decision-journey-showcase.md) | Decision Journey Showcase | Connect the retained failure, constrained refinement, replayed Pair, and Draft PR without duplicating analysis |
| [0066](0066-verified-v030-showcase-release.md) | Verified v0.3.0 Showcase release | Publish the presentation image and chart while reusing immutable Pair evidence |
| [0067](0067-operator-triggered-verified-demo.md) | Operator-triggered verified demo | Run recommendation and Pair replay from visible user actions before exposing GitOps evidence |
| [0068](0068-verified-v031-interactive-release.md) | Verified v0.3.1 interactive release | Publish and anonymously verify the two-step demo without rewriting historical Pair evidence |
| [0069](0069-visual-decision-console.md) | Visual Decision Console | Connect live resources, retained rejection, opposite-order replay, policy checks, and GitOps unlock in one guided surface |
| [0070](0070-verified-v032-decision-console-release.md) | Verified v0.3.2 Decision Console release | Publish and anonymously verify the visual demo while preserving the immutable v0.2.0 Pair boundary |
| [0071](0071-open-source-contribution-surface.md) | Open-source contribution surface | Add honest contribution, conduct, security, issue, and pull-request paths without inventing community maturity |
| [0072](0072-submission-evidence-hardening.md) | Submission evidence hardening | Replace an unusable security contact, bind scanner-reported SPDX evidence to v0.3.2, and correct claims without hiding benchmark failures |
| [0073](0073-korean-readme-entrypoint.md) | Korean README entry point | Make Korean the default repository introduction while preserving the complete English reference as an explicit language option |
| [0074](0074-english-readme-entrypoint.md) | English README entry point | Restore English as the default global open-source entry while preserving the Korean evaluator path |
| [0075](0075-observable-demo-walkthrough.md) | Observable demo walkthrough | Make the runtime path visible without conflating live and retained evidence |
| [0076](0076-remove-unused-kube-proxy-target.md) | Remove unused kube-proxy target | Remove a known-down scrape target not used by KubeFit collection |
| [0077](0077-target-health-first-walkthrough.md) | Target-health-first walkthrough | Use cAdvisor target health as the stable default Prometheus check |
| [0078](0078-pr-safety-check-foundation.md) | PR safety check foundation | Turn Pair decisions into CI output and classify supported Deployment changes |
| [0079](0079-reusable-github-safety-action.md) | Reusable GitHub safety Action | Package the bound resource gate as a direct-argument Docker Action |
| [0080](0080-action-fail-closed-smoke.md) | Action fail-closed smoke | Prove packaged PASS and byte-mismatch INVALID behavior in disposable containers |
| [0081](0081-immutable-generic-change-bundle.md) | Immutable generic change bundle | Freeze exact image, replica, or resource inputs before disposable execution |
| [0082](0082-restoring-generic-change-runner.md) | Restoring generic change runner | Execute exact bundles on disposable kind and make base restoration mandatory |
| [0083](0083-change-bound-fixed-load-evidence.md) | Change-bound fixed-load evidence | Bind shared k6 outputs and raw samples to an immutable generic change ID |
| [0084](0084-restored-generic-performance-run.md) | Restored generic performance run | Compare base and candidate fixed load only after mandatory base restoration |
| [0085](0085-immutable-generic-performance-result.md) | Immutable generic performance result | Persist replayable PASS, FAIL, or INVALID evidence behind a fail-closed CLI |
| [0086](0086-opposite-order-generic-performance.md) | Opposite-order generic performance | Produce independently restored base-first and candidate-first artifacts |
| [0087](0087-counterbalanced-generic-performance-pair.md) | Counterbalanced generic performance Pair | Bind two opposite-order trials without averaging away either failure |

## Visual language

Use the smallest visual that explains the decision:

- Mermaid flowchart for data flow and component boundaries
- Mermaid sequence diagram for runtime or GitOps interactions
- Table for before/after metrics and alternatives
- Screenshot only when UI state is itself the evidence

Every visual must have a sentence explaining the conclusion. Raw diagrams without
an engineering decision are not useful evidence.

## Writing workflow

1. Copy [`_template.md`](_template.md) when a development slice starts.
2. Write the problem and success criteria before implementation.
3. Add failed observations while working; do not reconstruct them from memory.
4. Record exact commands and quantitative results after verification.
5. Link the commits only after the slice has been committed.
6. End with the next unresolved question, not a generic task list.

Use sequential filenames such as `0002-cost-model.md`. One entry may cover several
small commits when they answer the same engineering question.
