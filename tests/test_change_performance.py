from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from safety import (
    ChangeExecutionError,
    ChangeK6RunSummary,
    ChangePerformancePolicy,
    ChangeTimedLoadResult,
    compare_change_performance,
    execute_change_performance,
    write_change_bundle,
)
from tests.test_benchmark import phase
from tests.test_deployment_change import BASE

CHANGE_ID = "change-0123456789abcdef0123456789abcdef"


def load_result(
    variant: str,
    *,
    change_id: str = CHANGE_ID,
    minute: int | None = None,
    recovered: bool = True,
    recovery_seconds: float = 10,
    steady: dict[str, object] | None = None,
    spike: dict[str, object] | None = None,
) -> ChangeTimedLoadResult:
    summary = ChangeK6RunSummary.model_validate(
        {
            "profile_version": "kubefit-load-v1",
            "change_id": change_id,
            "variant": variant,
            "dropped_iterations": 0,
            "steady": steady or phase(300, 100, 110),
            "spike": spike or phase(750, 200, 220),
            "recovery": phase(300, 120, 130),
        }
    )
    offset = (5 if variant == "after" else 0) if minute is None else minute
    started_at = datetime(2026, 9, 9, tzinfo=UTC) + timedelta(minutes=offset)
    return ChangeTimedLoadResult(
        summary=summary,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=160),
        traffic_spike_recovery_seconds=recovery_seconds,
        traffic_spike_recovered=recovered,
        summary_content=summary.model_dump_json().encode(),
        raw_content=f"raw:{variant}".encode(),
    )


def check_status(verdict, code: str) -> str:
    return next(check.status for check in verdict.checks if check.code == code)


def test_passes_comparable_fixed_load_without_cost_or_runtime_claims() -> None:
    verdict = compare_change_performance(
        load_result("before"),
        load_result(
            "after",
            steady=phase(300, 105, 115),
            spike=phase(750, 220, 240),
            recovery_seconds=12,
        ),
    )

    assert verdict.status == "pass"
    assert check_status(verdict, "measurement_order_bias") == "warning"
    assert not any("cost" in check.code for check in verdict.checks)
    assert not any("throttling" in check.code for check in verdict.checks)


def test_fails_latency_regression_and_missing_candidate_recovery() -> None:
    verdict = compare_change_performance(
        load_result("before"),
        load_result(
            "after",
            steady=phase(300, 120, 140),
            recovered=False,
        ),
    )

    assert verdict.status == "fail"
    assert check_status(verdict, "steady_latency_p95") == "fail"
    assert check_status(verdict, "traffic_spike_recovered") == "fail"


def test_explicit_policy_changes_the_replayable_boundary(tmp_path: Path) -> None:
    controller = RecordingController()
    policy = ChangePerformancePolicy(steady_latency_regression_percent=Decimal("25"))

    result = execute_change_performance(
        _bundle(tmp_path),
        controller,
        RecordingLoad(controller.events, steady_after=phase(300, 120, 130)),
        container="api",
        policy=policy,
    )

    assert result.policy == policy
    assert result.verdict.status == "pass"


@pytest.mark.parametrize(
    "after",
    [
        load_result("after", change_id="change-fedcba9876543210fedcba9876543210"),
        load_result("after", minute=1),
        load_result("after", steady=phase(300, 100, 110, completed=299)),
    ],
)
def test_invalidates_unbound_overlapping_or_incomplete_evidence(
    after: ChangeTimedLoadResult,
) -> None:
    verdict = compare_change_performance(load_result("before"), after)

    assert verdict.status == "invalid"
    assert verdict.invalid_reasons
    assert not verdict.failures


def _bundle(tmp_path: Path) -> Path:
    base = tmp_path / "base.yaml"
    candidate = tmp_path / "candidate.yaml"
    base.write_text(BASE)
    candidate.write_text(BASE.replace("replicas: 2", "replicas: 3"))
    return write_change_bundle(
        tmp_path / "changes",
        base,
        candidate,
        namespace="demo",
        deployment="api",
    ).path


class RecordingController:
    def __init__(self, restore_failure: bool = False) -> None:
        self.events: list[str] = []
        self.restore_failure = restore_failure

    def apply(self, manifest: Path, target) -> None:
        event = f"apply:{manifest.stem}"
        self.events.append(event)
        if self.restore_failure and self.events.count("apply:base") == 2:
            raise RuntimeError("restore failed")

    def wait_for_rollout(self, target) -> None:
        self.events.append(f"wait:{self.events[-1].split(':')[1]}")


class RecordingLoad:
    def __init__(
        self,
        events: list[str],
        interrupt: bool = False,
        steady_after: dict[str, object] | None = None,
    ) -> None:
        self.events = events
        self.interrupt = interrupt
        self.steady_after = steady_after
        self.measurement_count = 0

    def run(self, change_id, variant):
        self.events.append(f"measure:{variant}")
        if self.interrupt and variant == "after":
            raise KeyboardInterrupt
        result = load_result(
            variant,
            change_id=change_id,
            minute=self.measurement_count * 5,
            steady=self.steady_after if variant == "after" else None,
        )
        self.measurement_count += 1
        return result


def test_executes_both_loads_and_restores_before_returning(tmp_path: Path) -> None:
    controller = RecordingController()

    result = execute_change_performance(
        _bundle(tmp_path),
        controller,
        RecordingLoad(controller.events),
        container="api",
    )

    assert result.verdict.status == "pass"
    assert result.restored is True
    assert controller.events == [
        "apply:base",
        "wait:base",
        "measure:before",
        "apply:candidate",
        "wait:candidate",
        "measure:after",
        "apply:base",
        "wait:base",
    ]


def test_executes_reverse_order_and_still_restores_base(tmp_path: Path) -> None:
    controller = RecordingController()

    result = execute_change_performance(
        _bundle(tmp_path),
        controller,
        RecordingLoad(controller.events),
        container="api",
        execution_order="after-before",
    )

    assert result.execution_order == "after-before"
    assert result.verdict.status == "pass"
    assert "candidate was measured before baseline" in result.verdict.warnings[0]
    assert controller.events == [
        "apply:candidate",
        "wait:candidate",
        "measure:after",
        "apply:base",
        "wait:base",
        "measure:before",
        "apply:base",
        "wait:base",
    ]


def test_restores_before_propagating_interruption(tmp_path: Path) -> None:
    controller = RecordingController()

    with pytest.raises(KeyboardInterrupt):
        execute_change_performance(
            _bundle(tmp_path),
            controller,
            RecordingLoad(controller.events, interrupt=True),
            container="api",
        )

    assert controller.events[-2:] == ["apply:base", "wait:base"]


def test_restoration_failure_is_dominant(tmp_path: Path) -> None:
    controller = RecordingController(restore_failure=True)

    with pytest.raises(ChangeExecutionError) as raised:
        execute_change_performance(
            _bundle(tmp_path),
            controller,
            RecordingLoad(controller.events),
            container="api",
        )

    assert raised.value.stage == "restore_base"
    assert raised.value.restoration_error is not None
