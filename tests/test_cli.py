import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.cli as cli_module
from api.cli import build_parser
from evaluator import AnalysisArtifact, AnalysisTarget, evaluate_patch_eligibility
from gitops import ManifestPatchError
from recommender import CurrentResources
from tests.test_analysis_artifact import replayable_analysis
from tests.test_benchmark_artifact import completed_run
from tests.test_manifest import FIXTURES, eligible_evaluation
from tests.test_readiness import OBSERVED_AT, observed_usage


def eligible_analysis() -> AnalysisArtifact:
    return AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="demo", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    )


def test_analyze_requires_and_parses_explicit_prices() -> None:
    args = build_parser().parse_args(
        [
            "analyze",
            "--deployment",
            "demo",
            "--cpu-core-hour-usd",
            "0.04",
            "--memory-gib-hour-usd",
            "0.005",
            "--price-source",
            "example://local-model",
        ]
    )

    assert args.cpu_core_hour_usd == Decimal("0.04")
    assert args.memory_gib_hour_usd == Decimal("0.005")
    assert args.monthly_hours == Decimal("730")


def test_analyze_rejects_missing_prices() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["analyze", "--deployment", "demo"])


def test_analyze_rejects_non_positive_price() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "analyze",
                "--deployment",
                "demo",
                "--cpu-core-hour-usd",
                "0",
                "--memory-gib-hour-usd",
                "0.005",
                "--price-source",
                "example://local-model",
            ]
        )


def test_observation_policy_retains_explicit_workload_cpu_floor() -> None:
    args = build_parser().parse_args(
        [
            "readiness",
            "--deployment",
            "demo",
            "--observation-profile",
            "demo",
            "--minimum-cpu-millicores",
            "20",
        ]
    )

    _, _, policy = cli_module._observation_configuration(args)

    assert policy.minimum_cpu_millicores == 20


def test_observation_policy_rejects_non_positive_cpu_floor() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "readiness",
                "--deployment",
                "demo",
                "--minimum-cpu-millicores",
                "0",
            ]
        )


def test_analyze_emits_replayable_schema_v2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replayable = replayable_analysis()
    workload = SimpleNamespace(
        namespace="demo",
        name="api",
        container="api",
        uid=replayable.workload_uid,
        created_at=replayable.workload_created_at,
        resources=replayable.evaluation.current,
        desired_replicas=2,
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_observation",
        lambda args: (workload, None, replayable.observed_usage),
    )

    cli_module.main(
        [
            "analyze",
            "--deployment",
            "api",
            "--cpu-core-hour-usd",
            "0.04",
            "--memory-gib-hour-usd",
            "0.005",
            "--price-source",
            "example://test",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    restored = AnalysisArtifact.model_validate(output)
    assert restored.schema_version == 2
    assert restored.observed_usage == replayable.observed_usage
    assert restored.recommendation_policy is not None
    assert restored.recommendation_policy.algorithm == "resource-recommendation/v1"


def test_reanalyze_raises_only_the_retained_cpu_floor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = replayable_analysis()
    source_path = tmp_path / "analysis.json"
    source_path.write_text(source.model_dump_json(indent=2))

    cli_module.main(
        [
            "reanalyze",
            "--analysis",
            str(source_path),
            "--minimum-cpu-millicores",
            "400",
        ]
    )

    refined = AnalysisArtifact.model_validate_json(capsys.readouterr().out)
    assert refined.observed_usage == source.observed_usage
    assert refined.recommendation_policy is not None
    assert refined.recommendation_policy.minimum_cpu_millicores == 400
    assert refined.evaluation.recommendation.recommended.cpu_request_millicores == 400
    assert refined.evaluation.cost.assumptions == source.evaluation.cost.assumptions


def test_reanalyze_rejects_a_lower_cpu_floor(tmp_path: Path) -> None:
    source = replayable_analysis()
    source_path = tmp_path / "analysis.json"
    source_path.write_text(source.model_dump_json(indent=2))

    with pytest.raises(SystemExit, match="cannot lower"):
        cli_module.main(
            [
                "reanalyze",
                "--analysis",
                str(source_path),
                "--minimum-cpu-millicores",
                "5",
            ]
        )


def test_readiness_does_not_require_price_arguments() -> None:
    args = build_parser().parse_args(
        [
            "readiness",
            "--deployment",
            "demo",
            "--context",
            "kind-kubefit",
        ]
    )

    assert args.deployment == "demo"
    assert args.context == "kind-kubefit"
    assert not hasattr(args, "cpu_core_hour_usd")


def test_demo_observation_profile_has_fixed_short_window_and_strict_coverage() -> None:
    args = build_parser().parse_args(
        ["readiness", "--deployment", "demo", "--observation-profile", "demo"]
    )

    days, step_seconds, policy = cli_module._observation_configuration(args)

    assert days == pytest.approx(1 / 24)
    assert step_seconds == 60
    assert policy.minimum_observation_coverage == 0.9
    assert policy.minimum_sample_count == 100


def test_demo_observation_profile_rejects_window_override() -> None:
    args = build_parser().parse_args(
        [
            "readiness",
            "--deployment",
            "demo",
            "--observation-profile",
            "demo",
            "--days",
            "1",
        ]
    )

    with pytest.raises(SystemExit, match="fixes a 1-hour window"):
        cli_module._observation_configuration(args)


def test_benchmark_accepts_explicit_counterbalanced_execution_order() -> None:
    args = build_parser().parse_args(
        [
            "benchmark",
            "--proposal",
            "proposal",
            "--target-url",
            "http://127.0.0.1:8080",
            "--context",
            "kind-kubefit",
            "--confirm-disposable-cluster",
            "--execution-order",
            "after-before",
        ]
    )

    assert args.execution_order == "after-before"


@pytest.mark.parametrize(("status", "exit_code"), [("pass", None), ("fail", 2)])
def test_benchmark_pair_prints_machine_enforceable_assessment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    exit_code: int | None,
) -> None:
    paths: list[tuple[Path, Path]] = []

    class Assessment:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"status": status}

        def model_dump_json(self, *, indent: int) -> str:
            assert indent == 2
            return json.dumps({"status": status})

    def assess(first: Path, second: Path):
        paths.append((first, second))
        return Assessment()

    Assessment.status = status
    monkeypatch.setattr(cli_module, "assess_counterbalanced_pair", assess)
    monkeypatch.setattr(
        cli_module,
        "write_counterbalanced_pair",
        lambda output, first, second: SimpleNamespace(
            path=output / "benchmark-pair-test",
            reused=False,
            files=["pair.json"],
        ),
    )
    arguments = [
        "benchmark-pair",
        "--first",
        "first-result",
        "--second",
        "second-result",
        "--output-dir",
        "pair-results",
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert paths == [(Path("first-result"), Path("second-result"))]
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == status
    if status == "pass":
        assert output["path"] == "pair-results/benchmark-pair-test"
        assert output["reused"] is False
        assert output["files"] == ["pair.json"]
    else:
        assert set(output) == {"status"}


@pytest.mark.parametrize(("status", "exit_code"), [("pass", None), ("fail", 2)])
def test_check_writes_ci_summary_and_enforces_the_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    exit_code: int | None,
) -> None:
    summary_path = tmp_path / "step-summary.md"
    assessment = SimpleNamespace(
        status=status,
        model_dump=lambda *, mode: {"status": status, "assessment_id": "pair"},
    )
    summaries = []
    monkeypatch.setattr(
        cli_module, "assess_counterbalanced_pair", lambda first, second: assessment
    )
    monkeypatch.setattr(
        cli_module,
        "append_step_summary",
        lambda path, value: summaries.append((path, value)),
    )
    arguments = [
        "check",
        "--first",
        "first-result",
        "--second",
        "second-result",
        "--step-summary",
        str(summary_path),
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert summaries == [(summary_path, assessment)]
    assert json.loads(capsys.readouterr().out) == {
        "assessment_id": "pair",
        "status": status,
        "step_summary": str(summary_path),
    }


def test_check_uses_github_step_summary_environment_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path = tmp_path / "github-summary.md"
    assessment = SimpleNamespace(
        status="pass",
        model_dump=lambda *, mode: {"status": "pass"},
    )
    paths = []
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setattr(
        cli_module, "assess_counterbalanced_pair", lambda first, second: assessment
    )
    monkeypatch.setattr(
        cli_module,
        "append_step_summary",
        lambda path, value: paths.append(path),
    )

    cli_module.main(["check", "--first", "first", "--second", "second"])

    assert paths == [summary_path]


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [("supported", None), ("unsupported", 2), ("unchanged", 2)],
)
def test_inspect_change_is_a_machine_enforceable_scope_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    exit_code: int | None,
) -> None:
    change = SimpleNamespace(
        status=status,
        model_dump_json=lambda *, indent: json.dumps({"status": status}, indent=indent),
    )
    calls = []
    monkeypatch.setattr(
        cli_module,
        "inspect_deployment_change",
        lambda base, candidate, *, namespace, deployment: (
            calls.append((base, candidate, namespace, deployment)) or change
        ),
    )
    arguments = [
        "inspect-change",
        "--base",
        "base.yaml",
        "--candidate",
        "candidate.yaml",
        "--namespace",
        "demo",
        "--deployment",
        "api",
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert calls == [(Path("base.yaml"), Path("candidate.yaml"), "demo", "api")]
    assert json.loads(capsys.readouterr().out) == {"status": status}


@pytest.mark.parametrize(("status", "exit_code"), [("pass", None), ("invalid", 2)])
def test_validate_enforces_bound_end_to_end_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    exit_code: int | None,
) -> None:
    summary_path = tmp_path / "summary.md"
    result = SimpleNamespace(
        status=status,
        model_dump=lambda *, mode: {"status": status, "proposal_id": "proposal"},
    )
    calls = []
    summaries = []
    monkeypatch.setattr(
        cli_module,
        "validate_proposal_change",
        lambda *paths: calls.append(paths) or result,
    )
    monkeypatch.setattr(cli_module, "render_validation_summary", lambda value: "report\n")
    monkeypatch.setattr(
        cli_module,
        "append_markdown_summary",
        lambda path, content: summaries.append((path, content)),
    )
    arguments = [
        "validate",
        "--proposal",
        "proposal",
        "--base",
        "base.yaml",
        "--candidate",
        "candidate.yaml",
        "--first",
        "first-result",
        "--second",
        "second-result",
        "--step-summary",
        str(summary_path),
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert calls == [
        (
            Path("proposal"),
            Path("base.yaml"),
            Path("candidate.yaml"),
            Path("first-result"),
            Path("second-result"),
        )
    ]
    assert summaries == [(summary_path, "report\n")]
    assert json.loads(capsys.readouterr().out) == {
        "proposal_id": "proposal",
        "status": status,
        "step_summary": str(summary_path),
    }


def test_prepare_change_publishes_a_machine_readable_artifact(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setattr(
        cli_module,
        "write_change_bundle",
        lambda output, base, candidate, *, namespace, deployment: (
            calls.append((output, base, candidate, namespace, deployment))
            or SimpleNamespace(
                artifact_id="change-" + "a" * 32,
                path=output / ("change-" + "a" * 32),
                reused=False,
                files=["change-bundle.json", "change.json"],
            )
        ),
    )

    cli_module.main(
        [
            "prepare-change",
            "--base",
            "base.yaml",
            "--candidate",
            "candidate.yaml",
            "--namespace",
            "demo",
            "--deployment",
            "api",
            "--output-dir",
            "changes",
        ]
    )

    assert calls == [
        (Path("changes"), Path("base.yaml"), Path("candidate.yaml"), "demo", "api")
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["artifact_id"] == "change-" + "a" * 32
    assert output["reused"] is False


def test_execute_change_composes_disposable_kind_execution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: dict[str, object] = {}

    def controller(**kwargs):
        calls["controller"] = kwargs
        return "controller"

    def execute(path, selected_controller, *, container):
        calls["execute"] = (path, selected_controller, container)
        return SimpleNamespace(model_dump_json=lambda **_: '{"status":"pass"}')

    monkeypatch.setattr(cli_module, "KubectlManifestController", controller)
    monkeypatch.setattr(cli_module, "execute_change_bundle", execute)

    cli_module.main(
        [
            "execute-change",
            "--change",
            "changes/change-abc",
            "--context",
            "kind-kubefit",
            "--container",
            "api",
            "--confirm-disposable-cluster",
            "--rollout-timeout-seconds",
            "45",
        ]
    )

    assert calls == {
        "controller": {"context": "kind-kubefit", "rollout_timeout_seconds": 45},
        "execute": (Path("changes/change-abc"), "controller", "api"),
    }
    assert json.loads(capsys.readouterr().out) == {"status": "pass"}


def test_execute_change_rejects_non_kind_context() -> None:
    with pytest.raises(SystemExit, match=r"restricted to an explicit kind-\* context"):
        cli_module.main(
            [
                "execute-change",
                "--change",
                "changes/change-abc",
                "--context",
                "production",
                "--container",
                "api",
                "--confirm-disposable-cluster",
            ]
        )


@pytest.mark.parametrize(("status", "exit_code"), [("pass", None), ("fail", 2)])
def test_benchmark_change_persists_verdict_inside_target_lock(
    status: str,
    exit_code: int | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[object] = []

    class FakeLock:
        def __init__(self, **kwargs) -> None:
            events.append(("lock", kwargs))

        def __enter__(self):
            events.append("lock-enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            events.append("lock-exit")

    monkeypatch.setattr(
        cli_module,
        "load_change_bundle",
        lambda path: SimpleNamespace(
            change=SimpleNamespace(namespace="demo", deployment="api")
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "KubectlManifestController",
        lambda **kwargs: ("controller", kwargs),
    )
    monkeypatch.setattr(
        cli_module,
        "SubprocessChangeK6Executor",
        lambda **kwargs: ("load", kwargs),
    )
    monkeypatch.setattr(cli_module, "BenchmarkExecutionLock", FakeLock)

    def execute_with_order(path, controller, load, *, container, execution_order):
        events.append(("execute", path, controller, load, container, execution_order))
        return SimpleNamespace(
            restored=True,
            execution_order=execution_order,
            verdict=SimpleNamespace(status=status),
        )

    def publish(path, run):
        events.append(("publish", path, run))
        return SimpleNamespace(
            artifact_id="change-performance-" + "a" * 32,
            change_id="change-" + "b" * 32,
            path=path / ("change-performance-" + "a" * 32),
            status=status,
            reused=False,
        )

    monkeypatch.setattr(cli_module, "execute_change_performance", execute_with_order)
    monkeypatch.setattr(cli_module, "write_change_performance_artifact", publish)
    arguments = [
        "benchmark-change",
        "--change",
        "changes/change-abc",
        "--target-url",
        "http://127.0.0.1:8080",
        "--context",
        "kind-kubefit",
        "--container",
        "api",
        "--confirm-disposable-cluster",
        "--results-dir",
        "results",
        "--lock-dir",
        "locks",
        "--execution-order",
        "after-before",
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert events[0] == (
        "lock",
        {
            "root": Path("locks"),
            "context": "kind-kubefit",
            "namespace": "demo",
            "deployment": "api",
        },
    )
    assert events[1] == "lock-enter"
    assert events[-1] == "lock-exit"
    assert next(event for event in events if isinstance(event, tuple) and event[0] == "publish")
    output = json.loads(capsys.readouterr().out)
    assert output["verdict"] == status
    assert output["execution_order"] == "after-before"


def test_benchmark_change_rejects_non_kind_context() -> None:
    with pytest.raises(SystemExit, match=r"restricted to an explicit kind-\* context"):
        cli_module.main(
            [
                "benchmark-change",
                "--change",
                "changes/change-abc",
                "--target-url",
                "http://127.0.0.1:8080",
                "--context",
                "production",
                "--container",
                "api",
                "--confirm-disposable-cluster",
            ]
        )


@pytest.mark.parametrize(
    ("status", "persisted", "exit_code"),
    [("pass", True, None), ("fail", True, 2), ("invalid", False, 2)],
)
def test_benchmark_change_pair_preserves_fail_but_not_invalid(
    status: str,
    persisted: bool,
    exit_code: int | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    writes: list[tuple[Path, Path, Path]] = []
    assessment = SimpleNamespace(
        status=status,
        model_dump_json=lambda **_: json.dumps({"status": status}),
        model_dump=lambda **_: {"status": status, "assessment_id": "pair-id"},
    )
    monkeypatch.setattr(
        cli_module,
        "assess_change_performance_pair",
        lambda first, second: assessment,
    )

    def write(output, first, second):
        writes.append((output, first, second))
        return SimpleNamespace(
            path=output / "change-performance-pair-abc",
            reused=False,
            files=["pair.json"],
        )

    monkeypatch.setattr(cli_module, "write_change_performance_pair", write)
    arguments = [
        "benchmark-change-pair",
        "--first",
        "results/first",
        "--second",
        "results/second",
        "--output-dir",
        "pairs",
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert bool(writes) is persisted
    if persisted:
        assert writes == [
            (Path("pairs"), Path("results/first"), Path("results/second"))
        ]
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_podkill_preflight_builds_explicit_target_and_prints_selection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[object] = []

    class Inspector:
        def __init__(self, context: str) -> None:
            calls.append(("context", context))

        def inspect(self, target):
            calls.append(("target", target))
            return SimpleNamespace(
                model_dump_json=lambda **_: json.dumps(
                    {"selected": {"pod": "api-old", "pod_uid": "pod-uid"}}
                )
            )

    monkeypatch.setattr(cli_module, "KubectlPodKillPreflight", Inspector)

    cli_module.main(
        [
            "podkill-preflight",
            "--context",
            "kind-kubefit",
            "--namespace",
            "demo",
            "--deployment",
            "api",
            "--container",
            "api",
        ]
    )

    assert calls[0] == ("context", "kind-kubefit")
    assert calls[1][0] == "target"
    assert calls[1][1].model_dump() == {
        "namespace": "demo",
        "deployment": "api",
        "container": "api",
    }
    assert json.loads(capsys.readouterr().out)["selected"]["pod"] == "api-old"


def test_podkill_preflight_rejects_non_kind_context() -> None:
    with pytest.raises(SystemExit, match=r"kind-\*"):
        cli_module.main(
            [
                "podkill-preflight",
                "--context",
                "production",
                "--deployment",
                "api",
                "--container",
                "api",
            ]
        )


@pytest.mark.parametrize(("status", "exit_code"), [("pass", None), ("fail", 2)])
def test_podkill_run_binds_prerequisites_locks_and_persists_result(
    status: str,
    exit_code: int | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[object] = []
    target_change = SimpleNamespace(
        change=SimpleNamespace(namespace="demo", deployment="api")
    )
    approved = SimpleNamespace(selected=SimpleNamespace(pod_uid="old-uid"))
    result = SimpleNamespace(
        status=status,
        deleted=SimpleNamespace(pod_uid="old-uid"),
        replacement=(
            SimpleNamespace(pod_uid="replacement-uid") if status == "pass" else None
        ),
        service_recovery_seconds=1.0 if status == "pass" else None,
        replacement_ready_seconds=2.0 if status == "pass" else None,
    )

    class Inspector:
        def __init__(self, context: str) -> None:
            events.append(("inspector", context))

        def inspect(self, target):
            events.append(("inspect", target))
            return approved

    class Runner:
        def __init__(self, context: str, **kwargs) -> None:
            events.append(("runner", context, kwargs))

        def run(self, selected, url):
            events.append(("run", selected, url))
            return result

    class Lock:
        def __init__(self, **kwargs) -> None:
            events.append(("lock", kwargs))

        def __enter__(self):
            events.append("lock-enter")

        def __exit__(self, *args):
            events.append("lock-exit")

    monkeypatch.setattr(cli_module, "load_change_bundle", lambda path: target_change)
    monkeypatch.setattr(cli_module, "KubectlPodKillPreflight", Inspector)
    monkeypatch.setattr(cli_module, "PodKillExperimentRunner", Runner)
    monkeypatch.setattr(cli_module, "BenchmarkExecutionLock", Lock)
    monkeypatch.setattr(
        cli_module,
        "validate_podkill_prerequisites",
        lambda change, pair, target: events.append(("validate", change, pair, target)),
    )

    def persist(output, change, pair, measured):
        events.append(("persist", output, change, pair, measured))
        return SimpleNamespace(
            artifact_id="podkill-" + "a" * 32,
            change_id="change-" + "b" * 32,
            performance_pair_id="change-performance-pair-" + "c" * 32,
            path=output / ("podkill-" + "a" * 32),
            status=status,
            reused=False,
        )

    monkeypatch.setattr(cli_module, "write_podkill_artifact", persist)
    arguments = [
        "podkill-run",
        "--change",
        "changes/change-abc",
        "--performance-pair",
        "pairs/pair-abc",
        "--target-url",
        "http://127.0.0.1:8080",
        "--context",
        "kind-kubefit",
        "--container",
        "api",
        "--confirm-disposable-cluster",
        "--confirm-pod-deletion",
        "--results-dir",
        "podkills",
        "--lock-dir",
        "locks",
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert [event[0] for event in events if isinstance(event, tuple)].count(
        "validate"
    ) == 2
    assert events.index("lock-enter") < next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "run"
    )
    assert events[-1] == "lock-exit"
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == status
    assert output["deleted_pod_uid"] == "old-uid"


def test_podkill_run_rejects_non_kind_before_reading_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_change_bundle",
        lambda path: pytest.fail("must reject before reading artifacts"),
    )

    with pytest.raises(SystemExit, match=r"kind-\*"):
        cli_module.main(
            [
                "podkill-run",
                "--change",
                "change",
                "--performance-pair",
                "pair",
                "--target-url",
                "http://127.0.0.1:8080",
                "--context",
                "production",
                "--container",
                "api",
                "--confirm-disposable-cluster",
                "--confirm-pod-deletion",
            ]
        )


def test_podkill_run_requires_explicit_deletion_confirmation() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "podkill-run",
                "--change",
                "change",
                "--performance-pair",
                "pair",
                "--target-url",
                "http://127.0.0.1:8080",
                "--context",
                "kind-kubefit",
                "--container",
                "api",
                "--confirm-disposable-cluster",
            ]
        )


def test_benchmark_campaign_plan_reads_seed_file_and_prints_frozen_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed = tmp_path / "seed"
    seed.write_bytes(b"campaign seed")
    calls = []
    artifact = SimpleNamespace(
        path=Path("campaigns/benchmark-campaign-test"),
        reused=False,
    )
    plan = SimpleNamespace(
        model_dump=lambda *, mode: {
            "campaign_id": "benchmark-campaign-" + "a" * 32,
            "planned_pairs": 3,
            "schedule": [{"block": 1, "first_trial_order": "after-before"}],
        }
    )
    monkeypatch.setattr(
        cli_module,
        "write_benchmark_campaign_plan",
        lambda output, proposal, pairs, randomization_seed: (
            calls.append((output, proposal, pairs, randomization_seed)) or artifact
        ),
    )
    monkeypatch.setattr(
        cli_module, "load_benchmark_campaign_plan", lambda path: plan
    )

    cli_module.main(
        [
            "benchmark-campaign-plan",
            "--proposal",
            "proposal",
            "--planned-pairs",
            "3",
            "--randomization-seed-file",
            str(seed),
            "--output-dir",
            "campaigns",
        ]
    )

    assert calls == [(Path("campaigns"), Path("proposal"), 3, b"campaign seed")]
    output = json.loads(capsys.readouterr().out)
    assert output["campaign_id"].startswith("benchmark-campaign-")
    assert output["path"] == "campaigns/benchmark-campaign-test"
    assert output["reused"] is False


@pytest.mark.parametrize(("status", "exit_code"), [("complete", None), ("incomplete", 2)])
def test_benchmark_campaign_check_is_machine_enforceable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    exit_code: int | None,
) -> None:
    calls = []

    class Completion:
        def model_dump_json(self, *, indent: int) -> str:
            assert indent == 2
            return json.dumps({"status": status})

        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"status": status}

    Completion.status = status
    monkeypatch.setattr(
        cli_module,
        "assess_benchmark_campaign",
        lambda plan, pairs: calls.append((plan, pairs)) or Completion(),
    )
    monkeypatch.setattr(
        cli_module,
        "write_benchmark_campaign_evidence",
        lambda output, plan, pairs: SimpleNamespace(
            artifact_id="benchmark-campaign-evidence-" + "a" * 32,
            path=output / ("benchmark-campaign-evidence-" + "a" * 32),
            reused=False,
            files=["evidence.json"],
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "load_benchmark_campaign_evidence",
        lambda path: SimpleNamespace(
            completion=SimpleNamespace(pair_ids=["benchmark-pair-" + "b" * 32])
        ),
    )
    arguments = [
        "benchmark-campaign-check",
        "--plan",
        "campaign",
        "--pair",
        "pair-one",
        "--pair",
        "pair-two",
        "--output-dir",
        "campaign-evidence",
    ]

    if exit_code is None:
        cli_module.main(arguments)
    else:
        with pytest.raises(SystemExit) as raised:
            cli_module.main(arguments)
        assert raised.value.code == exit_code

    assert calls == [(Path("campaign"), [Path("pair-one"), Path("pair-two")])]
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == status
    if status == "complete":
        assert output["artifact_id"].startswith("benchmark-campaign-evidence-")
        assert output["path"].startswith("campaign-evidence/")
        assert output["reused"] is False
        assert output["files"] == ["evidence.json"]
    else:
        assert output == {"status": status}


def test_readiness_prints_machine_readable_collection_progress(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workload = SimpleNamespace(
        namespace="demo",
        name="demo",
        container="api",
        resources=CurrentResources(
            cpu_request_millicores=1000,
            cpu_limit_millicores=2000,
            memory_request_mib=2048,
            memory_limit_mib=4096,
        ),
    )
    metrics = SimpleNamespace(
        requested_start=OBSERVED_AT - timedelta(days=1),
        observation_days=1,
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_observation",
        lambda args: (workload, metrics, observed_usage()),
    )

    cli_module.main(["readiness", "--deployment", "demo"])

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "collecting"
    assert output["workload_uid"] == "deployment-uid"
    assert output["usage"]["sample_count"] == 64
    assert output["usage"]["required_sample_count"] == 405
    assert output["estimated_readiness_at"] == "2026-08-21T14:15:00Z"


def test_benchmark_requires_explicit_mutation_acknowledgement() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "benchmark",
                "--proposal",
                "proposal",
                "--target-url",
                "http://localhost:8080",
                "--context",
                "kind-kubefit",
            ]
        )


def test_benchmark_parses_explicit_local_boundaries() -> None:
    args = build_parser().parse_args(
        [
            "benchmark",
            "--proposal",
            "proposal",
            "--target-url",
            "http://localhost:8080",
            "--context",
            "kind-kubefit",
            "--confirm-disposable-cluster",
            "--results-dir",
            "results",
            "--lock-dir",
            "locks",
        ]
    )

    assert args.proposal == Path("proposal")
    assert args.context == "kind-kubefit"
    assert args.confirm_disposable_cluster is True
    assert args.results_dir == Path("results")
    assert args.lock_dir == Path("locks")


def test_benchmark_command_composes_execution_inside_target_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proposal, run = completed_run(tmp_path)
    events: list[str] = []
    values: dict[str, object] = {}

    class FakeLock:
        def __init__(self, **kwargs) -> None:
            values["lock"] = kwargs

        def __enter__(self):
            events.append("lock-enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            events.append("lock-exit")

    monkeypatch.setattr(cli_module, "KubectlDeploymentCollector", lambda **_: "kubernetes")
    monkeypatch.setattr(cli_module, "DeploymentRuntimeSnapshotter", lambda value: "snapshot")
    monkeypatch.setattr(cli_module, "PrometheusClient", lambda value: "prometheus")
    monkeypatch.setattr(cli_module, "SubprocessK6Executor", lambda **kwargs: "k6")
    monkeypatch.setattr(cli_module, "AlignedMeasurementCollector", lambda **kwargs: "measurement")
    monkeypatch.setattr(cli_module, "KubectlManifestController", lambda **kwargs: "controller")
    monkeypatch.setattr(cli_module, "BenchmarkExecutionLock", FakeLock)

    def execute(path, controller, measurement, *, execution_order):
        assert path == proposal.path
        assert controller == "controller"
        assert measurement == "measurement"
        assert execution_order == "before-after"
        events.append("execute")
        return run

    def publish(path, completed):
        assert path == tmp_path / "results"
        assert completed is run
        events.append("publish")
        return SimpleNamespace(
            artifact_id="benchmark-" + "a" * 32,
            proposal_id=proposal.artifact_id,
            path=path / ("benchmark-" + "a" * 32),
            reused=False,
        )

    monkeypatch.setattr(cli_module, "execute_benchmark", execute)
    monkeypatch.setattr(cli_module, "write_benchmark_result", publish)

    cli_module.main(
        [
            "benchmark",
            "--proposal",
            str(proposal.path),
            "--target-url",
            "http://localhost:8080",
            "--context",
            "kind-kubefit",
            "--confirm-disposable-cluster",
            "--results-dir",
            str(tmp_path / "results"),
            "--lock-dir",
            str(tmp_path / "locks"),
        ]
    )

    assert events == ["lock-enter", "execute", "publish", "lock-exit"]
    assert values["lock"] == {
        "root": tmp_path / "locks",
        "context": "kind-kubefit",
        "namespace": "demo",
        "deployment": "demo",
    }
    output = json.loads(capsys.readouterr().out)
    assert output["artifact_id"] == "benchmark-" + "a" * 32
    assert output["verdict"] == run.verdict.status
    assert output["execution_order"] == "before-after"
    assert output["restored"] is True


def test_benchmark_command_rejects_non_kind_context_before_loading_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_proposal_bundle",
        lambda path: pytest.fail("proposal must not be loaded"),
    )

    with pytest.raises(SystemExit, match="kind-\\*"):
        cli_module.main(
            [
                "benchmark",
                "--proposal",
                "proposal",
                "--target-url",
                "http://localhost:8080",
                "--context",
                "production",
                "--confirm-disposable-cluster",
            ]
        )


def test_propose_creates_and_reuses_immutable_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(eligible_analysis().model_dump_json(indent=2))
    original = (FIXTURES / "input.yaml").read_bytes()
    arguments = [
        "propose",
        "--analysis",
        str(analysis_path),
        "--repository-root",
        str(FIXTURES),
        "--manifest",
        "input.yaml",
        "--output-dir",
        str(tmp_path / "proposals"),
    ]

    cli_module.main(arguments)
    first = json.loads(capsys.readouterr().out)
    cli_module.main(arguments)
    second = json.loads(capsys.readouterr().out)

    assert first["artifact_id"].startswith("proposal-")
    assert first["change_count"] == 4
    assert first["target"] == {
        "namespace": "demo",
        "deployment": "demo",
        "container": "api",
    }
    assert first["reused"] is False
    assert second["artifact_id"] == first["artifact_id"]
    assert second["reused"] is True
    assert (FIXTURES / "input.yaml").read_bytes() == original


def test_propose_rejects_invalid_analysis_before_source_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = tmp_path / "analysis.json"
    analysis.write_text("not-json")
    monkeypatch.setattr(
        cli_module,
        "load_manifest_sources",
        lambda root, paths: pytest.fail("manifest must not be loaded"),
    )

    with pytest.raises(SystemExit, match="analysis JSON is invalid"):
        cli_module.main(
            [
                "propose",
                "--analysis",
                str(analysis),
                "--manifest",
                "demo.yaml",
            ]
        )


def test_propose_rejects_blocked_evaluation_without_output(tmp_path: Path) -> None:
    analysis = eligible_analysis()
    analysis.evaluation.recommendation.readiness.status = "insufficient_data"
    analysis.evaluation.recommendation.readiness.reasons = ["test block"]
    analysis.evaluation.patch_eligibility = evaluate_patch_eligibility(
        analysis.evaluation.recommendation
    )
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(analysis.model_dump_json())
    output = tmp_path / "proposals"

    with pytest.raises(ManifestPatchError, match="test block"):
        cli_module.main(
            [
                "propose",
                "--analysis",
                str(analysis_path),
                "--repository-root",
                str(FIXTURES),
                "--manifest",
                "input.yaml",
                "--output-dir",
                str(output),
            ]
        )

    assert not output.exists()


def test_publish_requires_explicit_mutation_acknowledgement() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "publish",
                "--proposal",
                "proposal",
                "--benchmark",
                "benchmark",
                "--benchmark-pair",
                "benchmark-pair",
            ]
        )


def test_publish_parser_accepts_only_an_environment_variable_name() -> None:
    args = build_parser().parse_args(
        [
            "publish",
            "--proposal",
            "proposal",
            "--benchmark",
            "benchmark",
            "--benchmark-pair",
            "benchmark-pair",
            "--github-token-env",
            "KUBEFIT_GITHUB_TOKEN",
            "--confirm-publish",
        ]
    )

    assert args.github_token_env == "KUBEFIT_GITHUB_TOKEN"
    assert not hasattr(args, "github_token")

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "publish",
                "--proposal",
                "proposal",
                "--benchmark",
                "benchmark",
                "--benchmark-pair",
                "benchmark-pair",
                "--github-token-env",
                "NOT-A-NAME",
                "--confirm-publish",
            ]
        )


def test_publish_commands_reject_unsafe_remote_names_before_execution() -> None:
    for command in ("publish", "publish-check"):
        arguments = [
            command,
            "--proposal",
            "proposal",
            "--benchmark",
            "benchmark",
            "--benchmark-pair",
            "benchmark-pair",
            "--remote",
            "--upload-pack=malicious",
        ]
        if command == "publish":
            arguments.append("--confirm-publish")
        with pytest.raises(SystemExit):
            build_parser().parse_args(arguments)


def test_publication_commands_accept_only_explicit_optional_campaign_evidence() -> None:
    for command in ("publish", "publish-check", "verify-publication"):
        arguments = [
            command,
            "--proposal",
            "proposal",
            "--benchmark",
            "benchmark",
            "--benchmark-pair",
            "benchmark-pair",
            "--benchmark-campaign-evidence",
            "campaign-evidence",
        ]
        if command == "publish":
            arguments.append("--confirm-publish")
        if command == "verify-publication":
            arguments.extend(["--evidence-dir", "publication-evidence"])

        args = build_parser().parse_args(arguments)

        assert args.benchmark_campaign_evidence == Path("campaign-evidence")


def test_publish_rejects_missing_token_before_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        cli_module,
        "build_pull_request_plan",
        lambda *_: pytest.fail("plan must not be built without a token"),
    )

    with pytest.raises(SystemExit, match="non-empty GITHUB_TOKEN"):
        cli_module.main(
            [
                "publish",
                "--proposal",
                "proposal",
                "--benchmark",
                "benchmark",
                "--benchmark-pair",
                "benchmark-pair",
                "--confirm-publish",
            ]
        )


def test_publish_composes_verified_stages_and_prints_safe_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "test-secret-token"
    monkeypatch.setenv("KUBEFIT_TOKEN", token)
    events: list[object] = []
    plan = object()
    commit = object()
    github = object()

    def client(value: str):
        assert value == token
        events.append("client")
        return github

    def build(
        proposal: Path,
        benchmark: Path,
        pair: Path,
        campaign_evidence: Path | None,
    ):
        events.append(("plan", proposal, benchmark, pair, campaign_evidence))
        return plan

    def commit_plan(root: Path, value):
        assert value is plan
        events.append(("commit", root))
        return commit

    def publish(root: Path, plan_value, commit_value, client_value, *, remote: str):
        assert (plan_value, commit_value, client_value) == (plan, commit, github)
        events.append(("publish", root, remote))
        return SimpleNamespace(
            repository=SimpleNamespace(owner="acme", name="workloads"),
            remote=remote,
            branch_name="kubefit/demo",
            commit_sha="a" * 40,
            branch_reused=False,
            pull_request_number=42,
            pull_request_url="https://github.com/acme/workloads/pull/42",
            pull_request_reused=False,
        )

    monkeypatch.setattr(cli_module, "GitHubRestClient", client)
    monkeypatch.setattr(cli_module, "build_pull_request_plan", build)
    monkeypatch.setattr(cli_module, "commit_pull_request_plan", commit_plan)
    monkeypatch.setattr(cli_module, "publish_pull_request", publish)

    cli_module.main(
        [
            "publish",
            "--proposal",
            "proposal-artifact",
            "--benchmark",
            "benchmark-artifact",
            "--benchmark-pair",
            "pair-artifact",
            "--repository-root",
            str(tmp_path),
            "--remote",
            "upstream",
            "--github-token-env",
            "KUBEFIT_TOKEN",
            "--confirm-publish",
        ]
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert token not in output_text
    assert output == {
        "branch": "kubefit/demo",
        "branch_reused": False,
        "commit_sha": "a" * 40,
        "draft": True,
        "pull_request_number": 42,
        "pull_request_reused": False,
        "pull_request_url": "https://github.com/acme/workloads/pull/42",
        "remote": "upstream",
        "repository": "acme/workloads",
    }
    assert events == [
        "client",
        (
            "plan",
            Path("proposal-artifact"),
            Path("benchmark-artifact"),
            Path("pair-artifact"),
            None,
        ),
        ("commit", tmp_path),
        ("publish", tmp_path, "upstream"),
    ]


def test_publish_redacts_token_from_boundary_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "unexpected-secret-value"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setattr(cli_module, "GitHubRestClient", lambda value: object())
    monkeypatch.setattr(
        cli_module,
        "build_pull_request_plan",
        lambda *_: (_ for _ in ()).throw(RuntimeError(f"failure included {token}")),
    )

    with pytest.raises(SystemExit) as captured:
        cli_module.main(
            [
                "publish",
                "--proposal",
                "proposal",
                "--benchmark",
                "benchmark",
                "--benchmark-pair",
                "benchmark-pair",
                "--confirm-publish",
            ]
        )

    assert token not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)
    assert capsys.readouterr().out == ""


def test_publish_check_reports_missing_token_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    plan = SimpleNamespace(
        proposal_id="proposal-" + "a" * 32,
        benchmark_id="benchmark-" + "b" * 32,
        benchmark_pair_id="benchmark-pair-" + "d" * 32,
        benchmark_ids=["benchmark-" + "b" * 32, "benchmark-" + "e" * 32],
        branch_name="kubefit/demo",
    )
    local = SimpleNamespace(
        repository_root=tmp_path,
        base_branch="main",
        base_commit_sha="c" * 40,
        file_path="deploy/demo.yaml",
        local_branch_state="absent",
        local_commit_sha=None,
    )

    class Remote:
        def repository(self, root, remote):
            return SimpleNamespace(owner="acme", name="workloads")

        def branch_sha(self, root, remote, branch):
            return None

    monkeypatch.setattr(cli_module, "build_pull_request_plan", lambda *_: plan)
    monkeypatch.setattr(cli_module, "inspect_repository_plan", lambda *_: local)
    monkeypatch.setattr(cli_module, "SubprocessGitRemote", Remote)
    monkeypatch.setattr(
        cli_module,
        "GitHubRestClient",
        lambda *_: pytest.fail("API must not be called without a token"),
    )
    monkeypatch.setattr(
        cli_module,
        "commit_pull_request_plan",
        lambda *_: pytest.fail("preflight must not create a commit"),
    )

    with pytest.raises(SystemExit) as captured:
        cli_module.main(
            [
                "publish-check",
                "--proposal",
                "proposal",
                "--benchmark",
                "benchmark",
                "--benchmark-pair",
                "benchmark-pair",
                "--repository-root",
                str(tmp_path),
            ]
        )

    output = json.loads(capsys.readouterr().out)
    assert captured.value.code == 2
    assert output["status"] == "blocked"
    assert output["mutation_performed"] is False
    assert output["checks"][-1] == {
        "name": "github_api",
        "status": "blocked",
        "token_env": "GITHUB_TOKEN",
        "token_present": False,
    }
    assert output["blockers"] == [
        "GitHub API token is missing from GITHUB_TOKEN"
    ]


def test_publish_check_reports_ready_without_claiming_write_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    plan = SimpleNamespace(
        proposal_id="proposal-" + "a" * 32,
        benchmark_id="benchmark-" + "b" * 32,
        benchmark_pair_id="benchmark-pair-" + "d" * 32,
        benchmark_ids=["benchmark-" + "b" * 32, "benchmark-" + "e" * 32],
        branch_name="kubefit/demo",
    )
    local = SimpleNamespace(
        repository_root=tmp_path,
        base_branch="main",
        base_commit_sha="c" * 40,
        file_path="deploy/demo.yaml",
        local_branch_state="reusable",
        local_commit_sha="d" * 40,
    )
    repository = SimpleNamespace(owner="acme", name="workloads")

    class Remote:
        def repository(self, root, remote):
            return repository

        def branch_sha(self, root, remote, branch):
            return "d" * 40

    class Client:
        def __init__(self, token):
            assert token == "test-token"

        def inspect_repository(self, value):
            assert value is repository
            return SimpleNamespace(
                default_branch="main",
                private=True,
                permissions_reported=True,
                enabled_permissions=["pull", "push"],
            )

    monkeypatch.setattr(cli_module, "build_pull_request_plan", lambda *_: plan)
    monkeypatch.setattr(cli_module, "inspect_repository_plan", lambda *_: local)
    monkeypatch.setattr(cli_module, "SubprocessGitRemote", Remote)
    monkeypatch.setattr(cli_module, "GitHubRestClient", Client)

    cli_module.main(
        [
            "publish-check",
            "--proposal",
            "proposal",
            "--benchmark",
            "benchmark",
            "--benchmark-pair",
            "benchmark-pair",
            "--repository-root",
            str(tmp_path),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ready"
    assert output["mutation_performed"] is False
    assert output["blockers"] == []
    assert output["checks"][2]["remote_branch_state"] == "reusable"
    assert output["checks"][3]["repository_readable"] is True
    assert output["warnings"] == [
        "read-only API access does not prove branch or pull-request write permission"
    ]


def test_publish_check_stops_after_artifact_failure_and_redacts_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "diagnostic-secret"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setattr(
        cli_module,
        "build_pull_request_plan",
        lambda *_: (_ for _ in ()).throw(RuntimeError(f"invalid {token}")),
    )
    monkeypatch.setattr(
        cli_module,
        "inspect_repository_plan",
        lambda *_: pytest.fail("local check must not follow invalid artifacts"),
    )

    with pytest.raises(SystemExit) as captured:
        cli_module.main(
            [
                "publish-check",
                "--proposal",
                "proposal",
                "--benchmark",
                "benchmark",
                "--benchmark-pair",
                "benchmark-pair",
            ]
        )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert captured.value.code == 2
    assert token not in output_text
    assert output["status"] == "blocked"
    assert output["checks"] == [
        {"name": "artifacts", "status": "blocked", "detail": "invalid [REDACTED]"}
    ]
