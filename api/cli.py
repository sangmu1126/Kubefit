import argparse
import json
import math
import os
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from benchmarks import (
    AlignedMeasurementCollector,
    BenchmarkExecutionLock,
    DeploymentRuntimeSnapshotter,
    KubectlManifestController,
    StepSummaryError,
    SubprocessK6Executor,
    append_markdown_summary,
    append_step_summary,
    assess_benchmark_campaign,
    assess_counterbalanced_pair,
    execute_benchmark,
    load_benchmark_campaign_evidence,
    load_benchmark_campaign_plan,
    write_benchmark_campaign_evidence,
    write_benchmark_campaign_plan,
    write_benchmark_result,
    write_counterbalanced_pair,
)
from collector import (
    DeploymentResources,
    IdentitySnapshotStore,
    KubectlDeploymentCollector,
    PrometheusClient,
    WorkloadMetrics,
)
from evaluator import (
    AnalysisArtifact,
    AnalysisTarget,
    CostAssumptions,
    RecommendationPolicySnapshot,
    assess_observation_readiness,
    evaluate_resources,
)
from gitops import (
    GitHubRestClient,
    ManifestTarget,
    SubprocessGitRemote,
    build_pull_request_plan,
    commit_pull_request_plan,
    generate_resource_patch,
    inspect_repository_plan,
    load_manifest_sources,
    load_proposal_bundle,
    publish_pull_request,
    verify_publication_evidence,
    write_proposal_bundle,
)
from recommender import ObservedUsage, RecommendationPolicy
from safety import (
    ChangeBundleError,
    ChangeExecutionError,
    DeploymentChangeError,
    KubectlPodKillPreflight,
    PodKillExperimentRunner,
    SubprocessChangeK6Executor,
    assess_change_performance_pair,
    assess_podkill_campaign,
    execute_change_bundle,
    execute_change_performance,
    inspect_deployment_change,
    load_change_bundle,
    load_podkill_campaign_evidence,
    load_podkill_campaign_plan,
    render_validation_summary,
    validate_podkill_prerequisites,
    validate_proposal_change,
    write_change_bundle,
    write_change_performance_artifact,
    write_change_performance_pair,
    write_podkill_artifact,
    write_podkill_campaign_evidence,
    write_podkill_campaign_plan,
)


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite decimal number")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _environment_variable_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise argparse.ArgumentTypeError("must be a valid environment variable name")
    return value


def _git_remote_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise argparse.ArgumentTypeError("must be a safe Git remote name")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kubefit")
    subcommands = parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="analyze a Kubernetes Deployment")
    _add_observation_arguments(analyze)
    analyze.add_argument("--cpu-core-hour-usd", required=True, type=_positive_decimal)
    analyze.add_argument("--memory-gib-hour-usd", required=True, type=_positive_decimal)
    analyze.add_argument("--monthly-hours", type=_positive_decimal, default=Decimal("730"))
    analyze.add_argument("--price-source", required=True)
    reanalyze = subcommands.add_parser(
        "reanalyze",
        help="derive a stricter analysis from retained replay inputs without recollection",
    )
    reanalyze.add_argument("--analysis", required=True, type=Path)
    reanalyze.add_argument(
        "--minimum-cpu-millicores", required=True, type=_positive_int
    )
    readiness = subcommands.add_parser(
        "readiness", help="explain whether observation evidence is proposal-ready"
    )
    _add_observation_arguments(readiness)
    propose = subcommands.add_parser(
        "propose", help="create an immutable proposal from an analysis and YAML"
    )
    propose.add_argument("--analysis", required=True, type=Path)
    propose.add_argument("--repository-root", type=Path, default=Path("."))
    propose.add_argument("--manifest", required=True, nargs="+", type=Path)
    propose.add_argument("--output-dir", type=Path, default=Path(".kubefit/proposals"))
    benchmark = subcommands.add_parser(
        "benchmark", help="benchmark a proposal on a disposable kind cluster"
    )
    benchmark.add_argument("--proposal", required=True, type=Path)
    benchmark.add_argument("--target-url", required=True)
    benchmark.add_argument("--prometheus-url", default="http://localhost:9090")
    benchmark.add_argument("--context", required=True)
    benchmark.add_argument(
        "--confirm-disposable-cluster",
        required=True,
        action="store_true",
        help="acknowledge that this command temporarily applies manifests",
    )
    benchmark.add_argument("--results-dir", type=Path, default=Path("benchmarks/results"))
    benchmark.add_argument("--lock-dir", type=Path, default=Path(".kubefit/benchmark-locks"))
    benchmark.add_argument(
        "--k6-script",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks" / "k6" / "resource_profile.js",
    )
    benchmark.add_argument("--rollout-timeout-seconds", type=int, default=120)
    benchmark.add_argument("--k6-timeout-seconds", type=int, default=240)
    benchmark.add_argument(
        "--execution-order",
        choices=("before-after", "after-before"),
        default="before-after",
        help="measurement order; run both orders as separate trials to counterbalance time bias",
    )
    benchmark_pair = subcommands.add_parser(
        "benchmark-pair",
        help="assess two opposite-order benchmark artifacts without cluster mutation",
    )
    benchmark_pair.add_argument("--first", required=True, type=Path)
    benchmark_pair.add_argument("--second", required=True, type=Path)
    benchmark_pair.add_argument(
        "--output-dir", type=Path, default=Path("benchmarks/pairs")
    )
    check = subcommands.add_parser(
        "check",
        help="enforce a counterbalanced benchmark pair as a CI safety gate",
    )
    check.add_argument("--first", required=True, type=Path)
    check.add_argument("--second", required=True, type=Path)
    check.add_argument(
        "--step-summary",
        type=Path,
        help="append Markdown to this path (defaults to GITHUB_STEP_SUMMARY when set)",
    )
    inspect_change = subcommands.add_parser(
        "inspect-change",
        help="classify supported and unsupported changes to one apps/v1 Deployment",
    )
    inspect_change.add_argument("--base", required=True, type=Path)
    inspect_change.add_argument("--candidate", required=True, type=Path)
    inspect_change.add_argument("--namespace", default="default")
    inspect_change.add_argument("--deployment", required=True)
    validate = subcommands.add_parser(
        "validate",
        help="bind a proposal and exact PR manifests to counterbalanced evidence",
    )
    validate.add_argument("--proposal", required=True, type=Path)
    validate.add_argument("--base", required=True, type=Path)
    validate.add_argument("--candidate", required=True, type=Path)
    validate.add_argument("--first", required=True, type=Path)
    validate.add_argument("--second", required=True, type=Path)
    validate.add_argument("--step-summary", type=Path)
    prepare_change = subcommands.add_parser(
        "prepare-change",
        help="publish an immutable base/candidate Deployment change bundle",
    )
    prepare_change.add_argument("--base", required=True, type=Path)
    prepare_change.add_argument("--candidate", required=True, type=Path)
    prepare_change.add_argument("--namespace", default="default")
    prepare_change.add_argument("--deployment", required=True)
    prepare_change.add_argument(
        "--output-dir", type=Path, default=Path(".kubefit/changes")
    )
    execute_change = subcommands.add_parser(
        "execute-change",
        help="apply a change bundle on disposable kind and always restore its base",
    )
    execute_change.add_argument("--change", required=True, type=Path)
    execute_change.add_argument("--context", required=True)
    execute_change.add_argument("--container", required=True)
    execute_change.add_argument(
        "--confirm-disposable-cluster",
        required=True,
        action="store_true",
        help="acknowledge that exact bundle manifests will be temporarily applied",
    )
    execute_change.add_argument("--rollout-timeout-seconds", type=_positive_int, default=120)
    benchmark_change = subcommands.add_parser(
        "benchmark-change",
        help="benchmark an immutable generic change and restore base on disposable kind",
    )
    benchmark_change.add_argument("--change", required=True, type=Path)
    benchmark_change.add_argument("--target-url", required=True)
    benchmark_change.add_argument("--context", required=True)
    benchmark_change.add_argument("--container", required=True)
    benchmark_change.add_argument(
        "--confirm-disposable-cluster",
        required=True,
        action="store_true",
        help="acknowledge that exact change manifests will be temporarily applied",
    )
    benchmark_change.add_argument(
        "--results-dir", type=Path, default=Path(".kubefit/change-performance")
    )
    benchmark_change.add_argument(
        "--lock-dir", type=Path, default=Path(".kubefit/benchmark-locks")
    )
    benchmark_change.add_argument(
        "--k6-script",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "k6"
        / "resource_profile.js",
    )
    benchmark_change.add_argument(
        "--rollout-timeout-seconds", type=_positive_int, default=120
    )
    benchmark_change.add_argument("--k6-timeout-seconds", type=_positive_int, default=240)
    benchmark_change.add_argument(
        "--execution-order",
        choices=("before-after", "after-before"),
        default="before-after",
        help="measurement order; produce both orders before a counterbalanced decision",
    )
    benchmark_change_pair = subcommands.add_parser(
        "benchmark-change-pair",
        help="assess and persist two opposite-order generic performance results",
    )
    benchmark_change_pair.add_argument("--first", required=True, type=Path)
    benchmark_change_pair.add_argument("--second", required=True, type=Path)
    benchmark_change_pair.add_argument(
        "--output-dir", type=Path, default=Path(".kubefit/change-performance-pairs")
    )
    podkill_preflight = subcommands.add_parser(
        "podkill-preflight",
        help="select one safe Deployment Pod for a future disposable-kind fault test",
    )
    podkill_preflight.add_argument("--context", required=True)
    podkill_preflight.add_argument("--namespace", default="default")
    podkill_preflight.add_argument("--deployment", required=True)
    podkill_preflight.add_argument("--container", required=True)
    podkill_run = subcommands.add_parser(
        "podkill-run",
        help="delete one approved kind Pod and persist bounded recovery evidence",
    )
    podkill_run.add_argument("--change", required=True, type=Path)
    podkill_run.add_argument("--performance-pair", required=True, type=Path)
    podkill_run.add_argument("--target-url", required=True)
    podkill_run.add_argument("--context", required=True)
    podkill_run.add_argument("--container", required=True)
    podkill_run.add_argument(
        "--confirm-disposable-cluster",
        required=True,
        action="store_true",
        help="acknowledge that the target is a disposable kind cluster",
    )
    podkill_run.add_argument(
        "--confirm-pod-deletion",
        required=True,
        action="store_true",
        help="acknowledge that one exact eligible Pod will be deleted",
    )
    podkill_run.add_argument(
        "--results-dir", type=Path, default=Path(".kubefit/podkill-results")
    )
    podkill_run.add_argument(
        "--lock-dir", type=Path, default=Path(".kubefit/benchmark-locks")
    )
    podkill_run.add_argument("--timeout-seconds", type=_positive_float, default=120)
    podkill_run.add_argument(
        "--probe-interval-seconds", type=_positive_float, default=0.5
    )
    podkill_run.add_argument(
        "--probe-timeout-seconds", type=_positive_float, default=2
    )
    podkill_run.add_argument(
        "--required-consecutive-successes", type=_positive_int, default=3
    )
    podkill_campaign = subcommands.add_parser(
        "podkill-campaign-plan",
        help="preregister repeated PodKill count, stopping rule, and recovery limits",
    )
    podkill_campaign.add_argument("--change", required=True, type=Path)
    podkill_campaign.add_argument("--performance-pair", required=True, type=Path)
    podkill_campaign.add_argument("--context", required=True)
    podkill_campaign.add_argument("--planned-trials", required=True, type=_positive_int)
    podkill_campaign.add_argument(
        "--allowed-failed-trials", type=_non_negative_int, default=0
    )
    podkill_campaign.add_argument(
        "--service-recovery-limit-seconds", required=True, type=_positive_float
    )
    podkill_campaign.add_argument(
        "--replacement-ready-limit-seconds", required=True, type=_positive_float
    )
    podkill_campaign.add_argument(
        "--output-dir", type=Path, default=Path(".kubefit/podkill-campaigns")
    )
    podkill_campaign_check = subcommands.add_parser(
        "podkill-campaign-check",
        help="assess and persist a complete preregistered PodKill campaign",
    )
    podkill_campaign_check.add_argument("--plan", required=True, type=Path)
    podkill_campaign_check.add_argument(
        "--trial", required=True, action="append", type=Path
    )
    podkill_campaign_check.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".kubefit/podkill-campaign-evidence"),
    )
    campaign_plan = subcommands.add_parser(
        "benchmark-campaign-plan",
        help="preregister a balanced randomized schedule of repeated benchmark pairs",
    )
    campaign_plan.add_argument("--proposal", required=True, type=Path)
    campaign_plan.add_argument("--planned-pairs", required=True, type=int)
    campaign_plan.add_argument("--randomization-seed-file", required=True, type=Path)
    campaign_plan.add_argument(
        "--output-dir", type=Path, default=Path("benchmarks/campaigns")
    )
    campaign_check = subcommands.add_parser(
        "benchmark-campaign-check",
        help="verify collected pairs against a preregistered campaign without mutation",
    )
    campaign_check.add_argument("--plan", required=True, type=Path)
    campaign_check.add_argument("--pair", required=True, action="append", type=Path)
    campaign_check.add_argument(
        "--output-dir", type=Path, default=Path("benchmarks/campaign-evidence")
    )
    publish = subcommands.add_parser(
        "publish", help="commit a verified proposal and open or reuse a GitHub draft PR"
    )
    publish.add_argument("--proposal", required=True, type=Path)
    publish.add_argument("--benchmark", required=True, type=Path)
    publish.add_argument("--benchmark-pair", required=True, type=Path)
    publish.add_argument("--benchmark-campaign-evidence", type=Path)
    publish.add_argument("--repository-root", type=Path, default=Path("."))
    publish.add_argument("--remote", type=_git_remote_name, default="origin")
    publish.add_argument(
        "--github-token-env",
        type=_environment_variable_name,
        default="GITHUB_TOKEN",
        metavar="NAME",
        help="environment variable containing the GitHub token (default: GITHUB_TOKEN)",
    )
    publish.add_argument(
        "--confirm-publish",
        required=True,
        action="store_true",
        help="acknowledge that this command creates a branch and draft pull request",
    )
    publish_check = subcommands.add_parser(
        "publish-check", help="inspect Draft PR publication prerequisites without mutation"
    )
    publish_check.add_argument("--proposal", required=True, type=Path)
    publish_check.add_argument("--benchmark", required=True, type=Path)
    publish_check.add_argument("--benchmark-pair", required=True, type=Path)
    publish_check.add_argument("--benchmark-campaign-evidence", type=Path)
    publish_check.add_argument("--repository-root", type=Path, default=Path("."))
    publish_check.add_argument("--remote", type=_git_remote_name, default="origin")
    publish_check.add_argument(
        "--github-token-env",
        type=_environment_variable_name,
        default="GITHUB_TOKEN",
        metavar="NAME",
        help="environment variable containing the GitHub token (default: GITHUB_TOKEN)",
    )
    verify_publication = subcommands.add_parser(
        "verify-publication",
        help="verify captured two-run Draft PR evidence without network access",
    )
    verify_publication.add_argument("--proposal", required=True, type=Path)
    verify_publication.add_argument("--benchmark", required=True, type=Path)
    verify_publication.add_argument("--benchmark-pair", required=True, type=Path)
    verify_publication.add_argument("--benchmark-campaign-evidence", type=Path)
    verify_publication.add_argument("--evidence-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "verify-publication":
        _run_verify_publication(args)
        return
    if args.command == "publish-check":
        if not _run_publish_check(args):
            raise SystemExit(2)
        return
    if args.command == "publish":
        _run_publish(args)
        return
    if args.command == "benchmark":
        _run_benchmark(args)
        return
    if args.command == "benchmark-pair":
        _run_benchmark_pair(args)
        return
    if args.command == "check":
        _run_check(args)
        return
    if args.command == "inspect-change":
        _run_inspect_change(args)
        return
    if args.command == "validate":
        _run_validate(args)
        return
    if args.command == "prepare-change":
        _run_prepare_change(args)
        return
    if args.command == "execute-change":
        _run_execute_change(args)
        return
    if args.command == "benchmark-change":
        _run_benchmark_change(args)
        return
    if args.command == "benchmark-change-pair":
        _run_benchmark_change_pair(args)
        return
    if args.command == "podkill-preflight":
        _run_podkill_preflight(args)
        return
    if args.command == "podkill-run":
        _run_podkill(args)
        return
    if args.command == "podkill-campaign-plan":
        _run_podkill_campaign_plan(args)
        return
    if args.command == "podkill-campaign-check":
        _run_podkill_campaign_check(args)
        return
    if args.command == "benchmark-campaign-plan":
        _run_benchmark_campaign_plan(args)
        return
    if args.command == "benchmark-campaign-check":
        _run_benchmark_campaign_check(args)
        return
    if args.command == "propose":
        _run_propose(args)
        return
    if args.command == "readiness":
        _run_readiness(args)
        return
    if args.command == "reanalyze":
        _run_reanalyze(args)
        return
    _run_analyze(args)


def _add_observation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--namespace", "-n", default="default")
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--container")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument(
        "--observation-profile",
        choices=("production", "demo"),
        default="production",
        help=(
            "production uses configurable multi-day evidence; "
            "demo fixes a 1-hour controlled window"
        ),
    )
    parser.add_argument("--days", type=int)
    parser.add_argument("--step-seconds", type=int)
    parser.add_argument("--identity-store", type=Path)
    parser.add_argument("--context")
    parser.add_argument(
        "--minimum-cpu-millicores",
        type=_positive_int,
        help=(
            "explicit recommendation floor retained in the replayable policy; "
            "use only with documented workload-specific validation evidence"
        ),
    )


def _collect_observation(
    args: argparse.Namespace,
) -> tuple[DeploymentResources, WorkloadMetrics, ObservedUsage]:
    workload = KubectlDeploymentCollector(context=args.context).collect(
        args.namespace, args.deployment, args.container
    )
    replica_sets = workload.replica_sets
    if args.identity_store is not None:
        identity = IdentitySnapshotStore(args.identity_store).remember(
            namespace=workload.namespace,
            name=workload.name,
            uid=workload.uid,
            created_at=workload.created_at,
            replica_sets=workload.replica_sets,
        )
        replica_sets = list(identity.replica_sets)
    observation_days, step_seconds, _ = _observation_configuration(args)
    metrics = PrometheusClient(args.prometheus_url).workload_metrics(
        workload.namespace,
        replica_sets,
        workload.pods,
        workload.container,
        workload.created_at,
        observation_days=observation_days,
        step_seconds=step_seconds,
    )
    observed = ObservedUsage(
        cpu_p95_millicores=metrics.cpu_p95_millicores,
        memory_p99_mib=metrics.memory_p99_mib,
        cpu_max_millicores=metrics.cpu_max_millicores,
        memory_max_mib=metrics.memory_max_mib,
        observation_days=metrics.observation_days,
        step_seconds=metrics.step_seconds,
        sample_count=metrics.sample_count,
        observation_coverage=metrics.observation_coverage,
        desired_replicas=workload.desired_replicas,
        available_replicas=workload.available_replicas,
        observed_replicas=len(workload.pods),
        metric_pod_count=metrics.metric_pod_count,
        minimum_current_pod_sample_count=metrics.minimum_current_pod_sample_count,
        workload_uid=workload.uid,
        workload_created_at=workload.created_at,
        history_clipped=metrics.history_clipped,
        authorized_replica_set_count=len(replica_sets),
        identity_snapshot_enabled=args.identity_store is not None,
        cpu_throttling_p95_percent=metrics.cpu_throttling_p95_percent,
        cpu_throttling_max_percent=metrics.cpu_throttling_max_percent,
        cpu_throttling_sample_count=metrics.cpu_throttling_sample_count,
        cpu_throttling_pod_count=metrics.cpu_throttling_pod_count,
        cpu_throttling_observation_coverage=(metrics.cpu_throttling_observation_coverage),
        minimum_current_pod_throttling_sample_count=(
            metrics.minimum_current_pod_throttling_sample_count
        ),
        container_status_count=workload.container_status_count,
        restart_count=workload.restart_count,
        oom_killed_count=workload.oom_killed_count,
    )
    return workload, metrics, observed


def _observation_configuration(
    args: argparse.Namespace,
) -> tuple[int | float, int, RecommendationPolicy]:
    minimum_cpu_millicores = (
        args.minimum_cpu_millicores
        if args.minimum_cpu_millicores is not None
        else RecommendationPolicy().minimum_cpu_millicores
    )
    if args.observation_profile == "demo":
        if args.days is not None or args.step_seconds is not None:
            raise SystemExit(
                "demo observation profile fixes a 1-hour window and 60-second step; "
                "do not combine it with --days or --step-seconds"
            )
        return (
            1 / 24,
            60,
            RecommendationPolicy(
                minimum_observation_coverage=0.9,
                minimum_sample_count=100,
                minimum_cpu_millicores=minimum_cpu_millicores,
            ),
        )
    return (
        args.days if args.days is not None else 7,
        args.step_seconds if args.step_seconds is not None else 300,
        RecommendationPolicy(minimum_cpu_millicores=minimum_cpu_millicores),
    )


def _run_analyze(args: argparse.Namespace) -> None:
    workload, _, observed = _collect_observation(args)
    _, _, policy = _observation_configuration(args)
    evaluation = evaluate_resources(
        workload.resources,
        observed,
        CostAssumptions(
            cpu_core_hour_usd=args.cpu_core_hour_usd,
            memory_gib_hour_usd=args.memory_gib_hour_usd,
            monthly_hours=args.monthly_hours,
            price_source=args.price_source,
        ),
        workload.desired_replicas,
        policy,
    )
    result = AnalysisArtifact(
        schema_version=2,
        target=AnalysisTarget(
            namespace=workload.namespace,
            deployment=workload.name,
            container=workload.container,
        ),
        workload_uid=workload.uid,
        workload_created_at=workload.created_at,
        evaluation=evaluation,
        observed_usage=observed,
        recommendation_policy=RecommendationPolicySnapshot.from_policy(policy),
    )
    print(result.model_dump_json(indent=2))


def _run_reanalyze(args: argparse.Namespace) -> None:
    if args.analysis.is_symlink() or not args.analysis.is_file():
        raise SystemExit("analysis must be a regular, non-symlinked JSON file")
    try:
        previous = AnalysisArtifact.model_validate_json(args.analysis.read_bytes())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"analysis JSON is invalid: {exc}") from exc
    if previous.schema_version != 2:
        raise SystemExit("reanalyze requires schema v2 replay inputs")
    observed = previous.observed_usage
    snapshot = previous.recommendation_policy
    assert observed is not None and snapshot is not None
    if args.minimum_cpu_millicores < snapshot.minimum_cpu_millicores:
        raise SystemExit("reanalyze cannot lower the retained CPU recommendation floor")
    policy_values = snapshot.model_dump(exclude={"algorithm"})
    policy_values["minimum_cpu_millicores"] = args.minimum_cpu_millicores
    policy = RecommendationPolicy(**policy_values)
    evaluation = evaluate_resources(
        previous.evaluation.current,
        observed,
        previous.evaluation.cost.assumptions,
        previous.evaluation.cost.replica_count,
        policy,
    )
    result = AnalysisArtifact(
        schema_version=2,
        target=previous.target,
        workload_uid=previous.workload_uid,
        workload_created_at=previous.workload_created_at,
        evaluation=evaluation,
        observed_usage=observed,
        recommendation_policy=RecommendationPolicySnapshot.from_policy(policy),
    )
    print(result.model_dump_json(indent=2))


def _run_readiness(args: argparse.Namespace) -> None:
    workload, metrics, observed = _collect_observation(args)
    _, _, policy = _observation_configuration(args)
    report = assess_observation_readiness(
        target=AnalysisTarget(
            namespace=workload.namespace,
            deployment=workload.name,
            container=workload.container,
        ),
        current=workload.resources,
        observed=observed,
        observed_at=metrics.requested_start + timedelta(days=metrics.observation_days),
        policy=policy,
    )
    print(report.model_dump_json(indent=2))


def _run_benchmark(args: argparse.Namespace) -> None:
    if not args.context.startswith("kind-"):
        raise SystemExit("benchmark command only accepts an explicit kind-* context")
    proposal = load_proposal_bundle(args.proposal)
    kubernetes = KubectlDeploymentCollector(context=args.context)
    measurement = AlignedMeasurementCollector(
        k6=SubprocessK6Executor(
            target_url=args.target_url,
            script_path=args.k6_script,
            timeout_seconds=args.k6_timeout_seconds,
        ),
        snapshot=DeploymentRuntimeSnapshotter(kubernetes),
        prometheus=PrometheusClient(args.prometheus_url),
    )
    controller = KubectlManifestController(
        context=args.context,
        rollout_timeout_seconds=args.rollout_timeout_seconds,
    )
    with BenchmarkExecutionLock(
        root=args.lock_dir,
        context=args.context,
        namespace=proposal.target.namespace,
        deployment=proposal.target.deployment,
    ):
        run = execute_benchmark(
            args.proposal,
            controller,
            measurement,
            execution_order=args.execution_order,
        )
        artifact = write_benchmark_result(args.results_dir, run)
    print(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "path": str(artifact.path),
                "proposal_id": artifact.proposal_id,
                "verdict": run.verdict.status,
                "execution_order": args.execution_order,
                "restored": run.restored,
                "reused": artifact.reused,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_benchmark_pair(args: argparse.Namespace) -> None:
    assessment = assess_counterbalanced_pair(args.first, args.second)
    if assessment.status != "pass":
        print(assessment.model_dump_json(indent=2))
        raise SystemExit(2)
    artifact = write_counterbalanced_pair(args.output_dir, args.first, args.second)
    output = assessment.model_dump(mode="json")
    output.update(
        {
            "path": str(artifact.path),
            "reused": artifact.reused,
            "files": artifact.files,
        }
    )
    print(json.dumps(output, indent=2, sort_keys=True))


def _run_check(args: argparse.Namespace) -> None:
    assessment = assess_counterbalanced_pair(args.first, args.second)
    summary_path = args.step_summary
    if summary_path is None and os.environ.get("GITHUB_STEP_SUMMARY"):
        summary_path = Path(os.environ["GITHUB_STEP_SUMMARY"])
    try:
        if summary_path is not None:
            append_step_summary(summary_path, assessment)
    except StepSummaryError as exc:
        raise SystemExit(str(exc)) from exc
    output = assessment.model_dump(mode="json")
    output["step_summary"] = str(summary_path) if summary_path is not None else None
    print(json.dumps(output, indent=2, sort_keys=True))
    if assessment.status != "pass":
        raise SystemExit(2)


def _run_inspect_change(args: argparse.Namespace) -> None:
    try:
        change = inspect_deployment_change(
            args.base,
            args.candidate,
            namespace=args.namespace,
            deployment=args.deployment,
        )
    except DeploymentChangeError as exc:
        raise SystemExit(str(exc)) from exc
    print(change.model_dump_json(indent=2))
    if change.status != "supported":
        raise SystemExit(2)


def _run_validate(args: argparse.Namespace) -> None:
    result = validate_proposal_change(
        args.proposal,
        args.base,
        args.candidate,
        args.first,
        args.second,
    )
    summary_path = args.step_summary
    if summary_path is None and os.environ.get("GITHUB_STEP_SUMMARY"):
        summary_path = Path(os.environ["GITHUB_STEP_SUMMARY"])
    try:
        if summary_path is not None:
            append_markdown_summary(summary_path, render_validation_summary(result))
    except StepSummaryError as exc:
        raise SystemExit(str(exc)) from exc
    output = result.model_dump(mode="json")
    output["step_summary"] = str(summary_path) if summary_path is not None else None
    print(json.dumps(output, indent=2, sort_keys=True))
    if result.status != "pass":
        raise SystemExit(2)


def _run_prepare_change(args: argparse.Namespace) -> None:
    try:
        artifact = write_change_bundle(
            args.output_dir,
            args.base,
            args.candidate,
            namespace=args.namespace,
            deployment=args.deployment,
        )
    except ChangeBundleError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "path": str(artifact.path),
                "reused": artifact.reused,
                "files": artifact.files,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_execute_change(args: argparse.Namespace) -> None:
    if not args.context.startswith("kind-"):
        raise SystemExit("change execution is restricted to an explicit kind-* context")
    controller = KubectlManifestController(
        context=args.context,
        rollout_timeout_seconds=args.rollout_timeout_seconds,
    )
    try:
        result = execute_change_bundle(
            args.change,
            controller,
            container=args.container,
        )
    except ChangeExecutionError as exc:
        raise SystemExit(str(exc)) from exc
    print(result.model_dump_json(indent=2))


def _run_benchmark_change(args: argparse.Namespace) -> None:
    if not args.context.startswith("kind-"):
        raise SystemExit("change benchmark is restricted to an explicit kind-* context")
    change = load_change_bundle(args.change)
    controller = KubectlManifestController(
        context=args.context,
        rollout_timeout_seconds=args.rollout_timeout_seconds,
    )
    load = SubprocessChangeK6Executor(
        target_url=args.target_url,
        script_path=args.k6_script,
        timeout_seconds=args.k6_timeout_seconds,
    )
    with BenchmarkExecutionLock(
        root=args.lock_dir,
        context=args.context,
        namespace=change.change.namespace,
        deployment=change.change.deployment,
    ):
        run = execute_change_performance(
            args.change,
            controller,
            load,
            container=args.container,
            execution_order=args.execution_order,
        )
        artifact = write_change_performance_artifact(args.results_dir, run)
    print(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "change_id": artifact.change_id,
                "path": str(artifact.path),
                "verdict": artifact.status,
                "execution_order": run.execution_order,
                "restored": run.restored,
                "reused": artifact.reused,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if artifact.status != "pass":
        raise SystemExit(2)


def _run_benchmark_change_pair(args: argparse.Namespace) -> None:
    assessment = assess_change_performance_pair(args.first, args.second)
    if assessment.status == "invalid":
        print(assessment.model_dump_json(indent=2))
        raise SystemExit(2)
    artifact = write_change_performance_pair(
        args.output_dir,
        args.first,
        args.second,
    )
    output = assessment.model_dump(mode="json")
    output.update(
        {
            "path": str(artifact.path),
            "reused": artifact.reused,
            "files": artifact.files,
        }
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    if assessment.status != "pass":
        raise SystemExit(2)


def _run_podkill_preflight(args: argparse.Namespace) -> None:
    try:
        inspector = KubectlPodKillPreflight(args.context)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    result = inspector.inspect(
        ManifestTarget(
            namespace=args.namespace,
            deployment=args.deployment,
            container=args.container,
        )
    )
    print(result.model_dump_json(indent=2))


def _run_podkill(args: argparse.Namespace) -> None:
    if not args.context.startswith("kind-"):
        raise SystemExit("PodKill is restricted to an explicit kind-* context")
    change = load_change_bundle(args.change)
    target = ManifestTarget(
        namespace=change.change.namespace,
        deployment=change.change.deployment,
        container=args.container,
    )
    try:
        validate_podkill_prerequisites(args.change, args.performance_pair, target)
        inspector = KubectlPodKillPreflight(args.context)
        runner = PodKillExperimentRunner(
            args.context,
            inspector=inspector,
            timeout_seconds=args.timeout_seconds,
            probe_interval_seconds=args.probe_interval_seconds,
            probe_timeout_seconds=args.probe_timeout_seconds,
            required_consecutive_successes=args.required_consecutive_successes,
        )
        with BenchmarkExecutionLock(
            root=args.lock_dir,
            context=args.context,
            namespace=target.namespace,
            deployment=target.deployment,
        ):
            validate_podkill_prerequisites(
                args.change, args.performance_pair, target
            )
            approved = inspector.inspect(target)
            result = runner.run(approved, args.target_url)
            artifact = write_podkill_artifact(
                args.results_dir,
                args.change,
                args.performance_pair,
                result,
            )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "change_id": artifact.change_id,
                "performance_pair_id": artifact.performance_pair_id,
                "path": str(artifact.path),
                "status": artifact.status,
                "reused": artifact.reused,
                "deleted_pod_uid": result.deleted.pod_uid,
                "replacement_pod_uid": (
                    result.replacement.pod_uid if result.replacement else None
                ),
                "service_recovery_seconds": result.service_recovery_seconds,
                "replacement_ready_seconds": result.replacement_ready_seconds,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if artifact.status != "pass":
        raise SystemExit(2)


def _run_podkill_campaign_plan(args: argparse.Namespace) -> None:
    try:
        artifact = write_podkill_campaign_plan(
            args.output_dir,
            args.change,
            args.performance_pair,
            args.context,
            args.planned_trials,
            args.allowed_failed_trials,
            args.service_recovery_limit_seconds,
            args.replacement_ready_limit_seconds,
        )
        plan = load_podkill_campaign_plan(artifact.path)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                **plan.model_dump(mode="json"),
                "path": str(artifact.path),
                "reused": artifact.reused,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_podkill_campaign_check(args: argparse.Namespace) -> None:
    try:
        assessment = assess_podkill_campaign(args.plan, args.trial)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if assessment.status in {"incomplete", "invalid"}:
        print(assessment.model_dump_json(indent=2))
        raise SystemExit(2)
    try:
        artifact = write_podkill_campaign_evidence(
            args.output_dir, args.plan, args.trial
        )
        loaded = load_podkill_campaign_evidence(artifact.path)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    output = assessment.model_dump(mode="json")
    output.update(
        {
            "artifact_id": artifact.artifact_id,
            "path": str(artifact.path),
            "reused": artifact.reused,
            "trial_ids": loaded.assessment.trial_ids,
        }
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    if assessment.status == "fail":
        raise SystemExit(2)


def _run_benchmark_campaign_plan(args: argparse.Namespace) -> None:
    seed_path = args.randomization_seed_file
    if seed_path.is_symlink() or not seed_path.is_file():
        raise SystemExit("randomization seed must be a regular, non-symlinked file")
    seed = seed_path.read_bytes()
    artifact = write_benchmark_campaign_plan(
        args.output_dir,
        args.proposal,
        args.planned_pairs,
        seed,
    )
    plan = load_benchmark_campaign_plan(artifact.path)
    print(
        json.dumps(
            {
                **plan.model_dump(mode="json"),
                "path": str(artifact.path),
                "reused": artifact.reused,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_benchmark_campaign_check(args: argparse.Namespace) -> None:
    completion = assess_benchmark_campaign(args.plan, args.pair)
    if completion.status != "complete":
        print(completion.model_dump_json(indent=2))
        raise SystemExit(2)
    artifact = write_benchmark_campaign_evidence(
        args.output_dir, args.plan, args.pair
    )
    loaded = load_benchmark_campaign_evidence(artifact.path)
    print(
        json.dumps(
            {
                **completion.model_dump(mode="json"),
                "artifact_id": artifact.artifact_id,
                "path": str(artifact.path),
                "reused": artifact.reused,
                "files": artifact.files,
                "pair_ids": loaded.completion.pair_ids,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_propose(args: argparse.Namespace) -> None:
    if args.analysis.is_symlink() or not args.analysis.is_file():
        raise SystemExit("analysis must be a regular, non-symlinked JSON file")
    try:
        analysis = AnalysisArtifact.model_validate_json(args.analysis.read_bytes())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"analysis JSON is invalid: {exc}") from exc
    sources = load_manifest_sources(args.repository_root, args.manifest)
    target = ManifestTarget(
        namespace=analysis.target.namespace,
        deployment=analysis.target.deployment,
        container=analysis.target.container,
    )
    patch = generate_resource_patch(sources, target, analysis.evaluation)
    proposal = write_proposal_bundle(
        args.output_dir,
        patch,
        analysis.evaluation,
        analysis=analysis,
    )
    print(
        json.dumps(
            {
                "artifact_id": proposal.artifact_id,
                "path": str(proposal.path),
                "reused": proposal.reused,
                "target": target.model_dump(),
                "change_count": len(patch.report.changes),
                "warnings": patch.report.eligibility_warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_publish(args: argparse.Namespace) -> None:
    token = os.environ.get(args.github_token_env)
    if token is None or not token.strip():
        raise SystemExit(
            f"publish requires a non-empty {args.github_token_env} environment variable"
        )
    try:
        github = GitHubRestClient(token)
        plan = build_pull_request_plan(
            args.proposal,
            args.benchmark,
            args.benchmark_pair,
            args.benchmark_campaign_evidence,
        )
        commit = commit_pull_request_plan(args.repository_root, plan)
        published = publish_pull_request(
            args.repository_root,
            plan,
            commit,
            github,
            remote=args.remote,
        )
    except Exception as exc:
        detail = str(exc).replace(token, "[REDACTED]")
        raise SystemExit(f"publish failed: {detail}") from None
    output: dict[str, object] = {
        "repository": f"{published.repository.owner}/{published.repository.name}",
        "remote": published.remote,
        "branch": published.branch_name,
        "commit_sha": published.commit_sha,
        "branch_reused": published.branch_reused,
        "pull_request_number": published.pull_request_number,
        "pull_request_url": published.pull_request_url,
        "pull_request_reused": published.pull_request_reused,
        "draft": True,
    }
    campaign_evidence_id = getattr(plan, "benchmark_campaign_evidence_id", None)
    if campaign_evidence_id is not None:
        output["benchmark_campaign_evidence_id"] = (
            campaign_evidence_id
        )
    print(json.dumps(output, indent=2, sort_keys=True))


def _run_publish_check(args: argparse.Namespace) -> bool:
    token = os.environ.get(args.github_token_env)
    checks: list[dict[str, object]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "ready",
        "mutation_performed": False,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }

    try:
        plan = build_pull_request_plan(
            args.proposal,
            args.benchmark,
            args.benchmark_pair,
            args.benchmark_campaign_evidence,
        )
    except Exception as exc:
        detail = _redact_secret(str(exc), token)
        checks.append({"name": "artifacts", "status": "blocked", "detail": detail})
        blockers.append(f"artifact verification failed: {detail}")
        return _print_publish_check(report)
    artifact_check = {
        "name": "artifacts",
        "status": "ready",
        "proposal_id": plan.proposal_id,
        "benchmark_id": plan.benchmark_id,
        "benchmark_pair_id": plan.benchmark_pair_id,
        "benchmark_ids": plan.benchmark_ids,
        "planned_branch": plan.branch_name,
    }
    campaign_evidence_id = getattr(plan, "benchmark_campaign_evidence_id", None)
    if campaign_evidence_id is not None:
        artifact_check.update(
            {
                "benchmark_campaign_evidence_id": campaign_evidence_id,
                "benchmark_campaign_id": plan.benchmark_campaign_id,
                "benchmark_campaign_pair_ids": plan.benchmark_campaign_pair_ids,
            }
        )
    checks.append(artifact_check)

    try:
        local = inspect_repository_plan(args.repository_root, plan)
    except Exception as exc:
        detail = _redact_secret(str(exc), token)
        checks.append({"name": "local_repository", "status": "blocked", "detail": detail})
        blockers.append(f"local repository check failed: {detail}")
        return _print_publish_check(report)
    checks.append(
        {
            "name": "local_repository",
            "status": "ready",
            "base_branch": local.base_branch,
            "base_commit_sha": local.base_commit_sha,
            "planned_path": local.file_path,
            "local_branch_state": local.local_branch_state,
            "local_commit_sha": local.local_commit_sha,
        }
    )

    git_remote = SubprocessGitRemote()
    repository = None
    try:
        repository = git_remote.repository(local.repository_root, args.remote)
        remote_sha = git_remote.branch_sha(
            local.repository_root, args.remote, plan.branch_name
        )
        if remote_sha is None:
            remote_state = "absent"
        elif local.local_commit_sha == remote_sha:
            remote_state = "reusable"
        else:
            remote_state = "collision"
            blockers.append(
                "remote branch exists but does not match a verified reusable local commit"
            )
        checks.append(
            {
                "name": "git_remote",
                "status": "blocked" if remote_state == "collision" else "ready",
                "repository": f"{repository.owner}/{repository.name}",
                "remote": args.remote,
                "remote_branch_state": remote_state,
                "remote_commit_sha": remote_sha,
            }
        )
    except Exception as exc:
        detail = _redact_secret(str(exc), token)
        checks.append({"name": "git_remote", "status": "blocked", "detail": detail})
        blockers.append(f"Git remote check failed: {detail}")

    if token is None or not token.strip():
        checks.append(
            {
                "name": "github_api",
                "status": "blocked",
                "token_env": args.github_token_env,
                "token_present": False,
            }
        )
        blockers.append(
            f"GitHub API token is missing from {args.github_token_env}"
        )
    elif repository is None:
        checks.append(
            {
                "name": "github_api",
                "status": "blocked",
                "token_env": args.github_token_env,
                "token_present": True,
                "detail": "GitHub repository identity is unavailable",
            }
        )
        blockers.append("GitHub API check requires a verified remote identity")
    else:
        try:
            access = GitHubRestClient(token).inspect_repository(repository)
            checks.append(
                {
                    "name": "github_api",
                    "status": "ready",
                    "token_env": args.github_token_env,
                    "token_present": True,
                    "repository_readable": True,
                    "default_branch": access.default_branch,
                    "private": access.private,
                    "permissions_reported": access.permissions_reported,
                    "enabled_permissions": access.enabled_permissions,
                }
            )
            warnings.append(
                "read-only API access does not prove branch or pull-request write permission"
            )
            if access.default_branch != local.base_branch:
                warnings.append(
                    "local base branch differs from the GitHub default branch"
                )
        except Exception as exc:
            detail = _redact_secret(str(exc), token)
            checks.append(
                {
                    "name": "github_api",
                    "status": "blocked",
                    "token_env": args.github_token_env,
                    "token_present": True,
                    "detail": detail,
                }
            )
            blockers.append(f"GitHub API read check failed: {detail}")

    return _print_publish_check(report)


def _run_verify_publication(args: argparse.Namespace) -> None:
    try:
        verified = verify_publication_evidence(
            args.proposal,
            args.benchmark,
            args.benchmark_pair,
            args.evidence_dir,
            args.benchmark_campaign_evidence,
        )
    except Exception as exc:
        raise SystemExit(f"publication evidence verification failed: {exc}") from None
    print(verified.model_dump_json(indent=2))


def _redact_secret(detail: str, secret: str | None) -> str:
    if secret:
        return detail.replace(secret, "[REDACTED]")
    return detail


def _print_publish_check(report: dict[str, object]) -> bool:
    blockers = report["blockers"]
    report["status"] = "blocked" if blockers else "ready"
    print(json.dumps(report, indent=2, sort_keys=True))
    return not bool(blockers)


if __name__ == "__main__":
    main()
