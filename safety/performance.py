from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from benchmarks.result import (
    EXPECTED_ITERATIONS,
    MAX_SCHEDULER_BOUNDARY_OVERSHOOT,
    PROFILE_VERSION,
    BenchmarkCheck,
)
from gitops import ManifestTarget
from safety.bundle import load_change_bundle
from safety.load import ChangeTimedLoadResult
from safety.runner import ChangeExecutionError, ChangeManifestController

ChangeExecutionOrder = Literal["before-after", "after-before"]
CHANGE_EXECUTION_VARIANTS: dict[
    ChangeExecutionOrder, tuple[Literal["before", "after"], ...]
] = {
    "before-after": ("before", "after"),
    "after-before": ("after", "before"),
}


class ChangePerformancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    steady_latency_regression_percent: Decimal = Field(default=Decimal("10"), ge=0)
    spike_latency_regression_percent: Decimal = Field(default=Decimal("15"), ge=0)
    error_rate_after: Decimal = Field(default=Decimal("0.01"), ge=0, le=1)
    error_rate_increase: Decimal = Field(default=Decimal("0.005"), ge=0, le=1)
    recovery_regression_percent: Decimal = Field(default=Decimal("20"), ge=0)


class ChangePerformanceVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["pass", "fail", "invalid"]
    checks: list[BenchmarkCheck]
    failures: list[str]
    invalid_reasons: list[str]
    warnings: list[str]


class ChangeLoadExecutor(Protocol):
    def run(
        self,
        change_id: str,
        variant: Literal["before", "after"],
    ) -> ChangeTimedLoadResult: ...


class ChangePerformanceRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    target: ManifestTarget
    execution_order: ChangeExecutionOrder = "before-after"
    before: ChangeTimedLoadResult
    after: ChangeTimedLoadResult
    policy: ChangePerformancePolicy = Field(default_factory=ChangePerformancePolicy)
    verdict: ChangePerformanceVerdict
    restored: Literal[True] = True

    @model_validator(mode="after")
    def result_replays(self) -> "ChangePerformanceRun":
        if (
            self.before.summary.change_id != self.change_id
            or self.after.summary.change_id != self.change_id
        ):
            raise ValueError("change load results do not reference the executed change")
        if change_measurement_order(self.before, self.after) != self.execution_order:
            raise ValueError("change load timestamps conflict with execution order")
        replayed = compare_change_performance(self.before, self.after, self.policy)
        if self.verdict != replayed:
            raise ValueError("change performance verdict does not replay")
        return self


def compare_change_performance(
    before: ChangeTimedLoadResult,
    after: ChangeTimedLoadResult,
    policy: ChangePerformancePolicy | None = None,
) -> ChangePerformanceVerdict:
    policy = policy or ChangePerformancePolicy()
    checks = _validity_checks(before, after)
    invalid_reasons = [check.reason for check in checks if check.status == "invalid"]
    if invalid_reasons:
        return ChangePerformanceVerdict(
            status="invalid",
            checks=checks,
            failures=[],
            invalid_reasons=invalid_reasons,
            warnings=[],
        )

    order = change_measurement_order(before, after)
    assert order is not None
    first = "baseline" if order == "before-after" else "candidate"
    second = "candidate" if order == "before-after" else "baseline"
    checks.append(
        BenchmarkCheck(
            code="measurement_order_bias",
            status="warning",
            reason=(
                f"{first} was measured before {second}; one sequential trial cannot "
                "separate change effects from warm-up or time drift"
            ),
        )
    )
    for phase, maximum in (
        ("steady", policy.steady_latency_regression_percent),
        ("spike", policy.spike_latency_regression_percent),
    ):
        baseline = getattr(before.summary, phase)
        candidate = getattr(after.summary, phase)
        for percentile in ("p95", "p99"):
            checks.append(
                _regression_check(
                    f"{phase}_latency_{percentile}",
                    f"{phase} latency {percentile.upper()}",
                    getattr(baseline, f"latency_{percentile}_ms"),
                    getattr(candidate, f"latency_{percentile}_ms"),
                    maximum,
                )
            )
    for phase in EXPECTED_ITERATIONS:
        before_error = Decimal(str(getattr(before.summary, phase).error_rate))
        after_error = Decimal(str(getattr(after.summary, phase).error_rate))
        checks.append(
            _maximum_check(
                f"{phase}_error_rate_after",
                f"candidate {phase} error rate",
                after_error,
                policy.error_rate_after,
            )
        )
        checks.append(
            _maximum_check(
                f"{phase}_error_rate_increase",
                f"{phase} error-rate increase",
                after_error - before_error,
                policy.error_rate_increase,
            )
        )
    checks.append(
        BenchmarkCheck(
            code="traffic_spike_recovered",
            status="pass" if after.traffic_spike_recovered else "fail",
            reason=(
                "candidate recovered during the fixed recovery phase"
                if after.traffic_spike_recovered
                else "candidate did not recover during the fixed recovery phase"
            ),
        )
    )
    checks.append(
        _regression_check(
            "traffic_spike_recovery",
            "traffic-spike recovery time",
            before.traffic_spike_recovery_seconds,
            after.traffic_spike_recovery_seconds,
            policy.recovery_regression_percent,
        )
    )
    failures = [check.reason for check in checks if check.status == "fail"]
    warnings = [check.reason for check in checks if check.status == "warning"]
    return ChangePerformanceVerdict(
        status="fail" if failures else "pass",
        checks=checks,
        failures=failures,
        invalid_reasons=[],
        warnings=warnings,
    )


def execute_change_performance(
    bundle_path: Path,
    controller: ChangeManifestController,
    load: ChangeLoadExecutor,
    *,
    container: str,
    policy: ChangePerformancePolicy | None = None,
    execution_order: ChangeExecutionOrder = "before-after",
) -> ChangePerformanceRun:
    if execution_order not in CHANGE_EXECUTION_VARIANTS:
        raise ValueError("execution order must be before-after or after-before")
    bundle = load_change_bundle(bundle_path)
    target = ManifestTarget(
        namespace=bundle.change.namespace,
        deployment=bundle.change.deployment,
        container=container,
    )
    restore_required = False
    primary_error: BaseException | None = None
    stage = "apply_base"
    measurements: dict[str, ChangeTimedLoadResult] = {}

    try:
        restore_required = True
        manifests = {
            "before": bundle.base_manifest,
            "after": bundle.candidate_manifest,
        }
        labels = {"before": "base", "after": "candidate"}
        for variant in CHANGE_EXECUTION_VARIANTS[execution_order]:
            label = labels[variant]
            stage = f"apply_{label}"
            controller.apply(manifests[variant], target)
            stage = f"wait_{label}_rollout"
            controller.wait_for_rollout(target)
            stage = f"measure_{label}"
            measurements[variant] = load.run(bundle.artifact_id, variant)
    except BaseException as exc:
        primary_error = exc

    restoration_error: Exception | None = None
    if restore_required:
        try:
            controller.apply(bundle.base_manifest, target)
            controller.wait_for_rollout(target)
        except Exception as exc:
            restoration_error = exc

    if restoration_error is not None:
        failure_stage = stage if primary_error is not None else "restore_base"
        raise ChangeExecutionError(
            failure_stage, primary_error, restoration_error
        ) from restoration_error
    if primary_error is not None:
        if not isinstance(primary_error, Exception):
            raise primary_error
        raise ChangeExecutionError(stage, primary_error) from primary_error
    before = measurements.get("before")
    after = measurements.get("after")
    assert before is not None and after is not None
    selected_policy = policy or ChangePerformancePolicy()
    return ChangePerformanceRun(
        change_id=bundle.artifact_id,
        target=target,
        execution_order=execution_order,
        before=before,
        after=after,
        policy=selected_policy,
        verdict=compare_change_performance(before, after, selected_policy),
    )


def change_measurement_order(
    before: ChangeTimedLoadResult,
    after: ChangeTimedLoadResult,
) -> ChangeExecutionOrder | None:
    if before.finished_at <= after.started_at:
        return "before-after"
    if after.finished_at <= before.started_at:
        return "after-before"
    return None


def _validity_checks(
    before: ChangeTimedLoadResult,
    after: ChangeTimedLoadResult,
) -> list[BenchmarkCheck]:
    checks = []
    comparisons = (
        (
            "variant_pair",
            before.summary.variant == "before" and after.summary.variant == "after",
            "results must be ordered as before then after",
        ),
        (
            "change_id_match",
            before.summary.change_id == after.summary.change_id,
            "before and after change IDs must match",
        ),
        (
            "profile_version_match",
            before.summary.profile_version == after.summary.profile_version == PROFILE_VERSION,
            f"both results must use {PROFILE_VERSION}",
        ),
        (
            "dropped_iterations",
            before.summary.dropped_iterations == after.summary.dropped_iterations == 0,
            "before and after runs must not drop offered iterations",
        ),
        (
            "baseline_recovered",
            before.traffic_spike_recovered,
            "baseline must recover during the fixed recovery phase",
        ),
        (
            "measurement_intervals",
            change_measurement_order(before, after) is not None,
            "base and candidate measurement intervals must not overlap",
        ),
    )
    for code, valid, failure_reason in comparisons:
        checks.append(
            BenchmarkCheck(
                code=code,
                status="pass" if valid else "invalid",
                reason=f"{code} is valid" if valid else failure_reason,
            )
        )
    for phase, expected in EXPECTED_ITERATIONS.items():
        baseline = getattr(before.summary, phase)
        candidate = getattr(after.summary, phase)
        valid = (
            baseline.expected_iterations == candidate.expected_iterations == expected
            and expected
            <= baseline.completed_iterations
            <= expected + MAX_SCHEDULER_BOUNDARY_OVERSHOOT
            and expected
            <= candidate.completed_iterations
            <= expected + MAX_SCHEDULER_BOUNDARY_OVERSHOOT
            and baseline.requests >= baseline.completed_iterations
            and candidate.requests >= candidate.completed_iterations
        )
        checks.append(
            BenchmarkCheck(
                code=f"{phase}_offered_load",
                status="pass" if valid else "invalid",
                reason=(
                    f"{phase} completed the fixed {expected}-iteration load"
                    if valid
                    else (
                        f"{phase} must complete {expected} to "
                        f"{expected + MAX_SCHEDULER_BOUNDARY_OVERSHOOT} iterations "
                        "in both runs"
                    )
                ),
            )
        )
    return checks


def _regression_check(
    code: str,
    label: str,
    before: float,
    after: float,
    allowed_percent: Decimal,
) -> BenchmarkCheck:
    baseline = Decimal(str(before))
    candidate = Decimal(str(after))
    change = (
        Decimal("0")
        if baseline == candidate == 0
        else None
        if baseline == 0
        else (candidate - baseline) / baseline * Decimal("100")
    )
    failed = change is None or change > allowed_percent
    reason = (
        f"{label} rose from a zero baseline to {after}"
        if change is None
        else (
            f"{label} changed by {change.quantize(Decimal('0.001'))}% "
            f"(allowed regression: {allowed_percent}%)"
        )
    )
    return BenchmarkCheck(code=code, status="fail" if failed else "pass", reason=reason)


def _maximum_check(
    code: str,
    label: str,
    value: Decimal,
    maximum: Decimal,
) -> BenchmarkCheck:
    return BenchmarkCheck(
        code=code,
        status="fail" if value > maximum else "pass",
        reason=f"{label} is {value} (maximum: {maximum})",
    )
