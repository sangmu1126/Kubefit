import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from safety.performance_artifact import (
    ChangePerformanceArtifactError,
    LoadedChangePerformanceArtifact,
    load_change_performance_artifact,
)


class ChangePerformancePairError(RuntimeError):
    """Raised when generic performance artifacts cannot be assessed as a pair."""


class ChangePerformancePairTrial(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^change-performance-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    execution_order: Literal["before-after", "after-before"]
    verdict_status: Literal["pass", "fail", "invalid"]
    policy_check_statuses: dict[str, Literal["pass", "fail", "invalid", "warning"]]


class ChangePerformancePairCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    status: Literal["pass", "fail", "invalid", "warning"]
    reason: str


class ChangePerformancePairAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    assessment_id: str = Field(pattern=r"^change-performance-pair-[0-9a-f]{32}$")
    change_id: str | None = Field(default=None, pattern=r"^change-[0-9a-f]{32}$")
    status: Literal["pass", "fail", "invalid"]
    trials: list[ChangePerformancePairTrial] = Field(min_length=2, max_length=2)
    checks: list[ChangePerformancePairCheck]
    failures: list[str]
    invalid_reasons: list[str]
    warnings: list[str]


def assess_change_performance_pair(
    first_path: Path,
    second_path: Path,
) -> ChangePerformancePairAssessment:
    try:
        loaded = [
            load_change_performance_artifact(first_path),
            load_change_performance_artifact(second_path),
        ]
    except (ChangePerformanceArtifactError, OSError) as exc:
        raise ChangePerformancePairError(
            "pair assessment requires two valid generic performance artifacts"
        ) from exc
    return assess_loaded_change_performance_pair(*loaded)


def assess_loaded_change_performance_pair(
    first: LoadedChangePerformanceArtifact,
    second: LoadedChangePerformanceArtifact,
) -> ChangePerformancePairAssessment:
    results = sorted((first, second), key=lambda item: item.artifact_id)
    trials = [_trial(item) for item in results]
    checks = _input_checks(results, trials)
    invalid_reasons = [check.reason for check in checks if check.status == "invalid"]
    change_ids = {trial.change_id for trial in trials}
    change_id = next(iter(change_ids)) if len(change_ids) == 1 else None
    warnings = [
        (
            "two opposite-order trials reduce directional order bias but do not "
            "estimate run-to-run variance or establish statistical significance"
        )
    ]
    if invalid_reasons:
        return _assessment(
            change_id=change_id,
            status="invalid",
            trials=trials,
            checks=checks,
            failures=[],
            invalid_reasons=invalid_reasons,
            warnings=warnings,
        )

    policy_agrees = trials[0].policy_check_statuses == trials[1].policy_check_statuses
    checks.append(
        ChangePerformancePairCheck(
            code="policy_check_agreement",
            status="pass" if policy_agrees else "fail",
            reason=(
                "both orders produced identical non-order policy check statuses"
                if policy_agrees
                else "opposite orders produced different non-order policy check statuses"
            ),
        )
    )
    both_pass = all(trial.verdict_status == "pass" for trial in trials)
    checks.append(
        ChangePerformancePairCheck(
            code="both_trials_pass",
            status="pass" if both_pass else "fail",
            reason=(
                "both opposite-order generic performance verdicts passed"
                if both_pass
                else "both opposite-order generic performance verdicts must pass"
            ),
        )
    )
    failures = [check.reason for check in checks if check.status == "fail"]
    return _assessment(
        change_id=change_id,
        status="fail" if failures else "pass",
        trials=trials,
        checks=checks,
        failures=failures,
        invalid_reasons=[],
        warnings=warnings,
    )


def _trial(result: LoadedChangePerformanceArtifact) -> ChangePerformancePairTrial:
    return ChangePerformancePairTrial(
        artifact_id=result.artifact_id,
        change_id=result.change_id,
        execution_order=result.run.execution_order,
        verdict_status=result.run.verdict.status,
        policy_check_statuses={
            check.code: check.status
            for check in result.run.verdict.checks
            if check.code != "measurement_order_bias"
        },
    )


def _input_checks(
    results: list[LoadedChangePerformanceArtifact],
    trials: list[ChangePerformancePairTrial],
) -> list[ChangePerformancePairCheck]:
    values = (
        (
            "distinct_artifacts",
            trials[0].artifact_id != trials[1].artifact_id,
            "counterbalanced trials must be distinct performance artifacts",
        ),
        (
            "change_binding",
            trials[0].change_id == trials[1].change_id,
            "counterbalanced trials must reference the same generic change",
        ),
        (
            "target_binding",
            results[0].run.target == results[1].run.target,
            "counterbalanced trials must reference the same target",
        ),
        (
            "opposite_orders",
            {trial.execution_order for trial in trials}
            == {"before-after", "after-before"},
            "counterbalanced trials must contain exactly one run in each order",
        ),
        (
            "profile_binding",
            all(
                result.run.before.summary.profile_version
                == results[0].run.before.summary.profile_version
                and result.run.after.summary.profile_version
                == results[0].run.after.summary.profile_version
                for result in results
            ),
            "counterbalanced trials must use the same load profile",
        ),
        (
            "policy_binding",
            results[0].run.policy == results[1].run.policy,
            "counterbalanced trials must use the same performance policy",
        ),
    )
    return [
        ChangePerformancePairCheck(
            code=code,
            status="pass" if valid else "invalid",
            reason=f"{code} is valid" if valid else invalid_reason,
        )
        for code, valid, invalid_reason in values
    ]


def _assessment(
    *,
    change_id: str | None,
    status: Literal["pass", "fail", "invalid"],
    trials: list[ChangePerformancePairTrial],
    checks: list[ChangePerformancePairCheck],
    failures: list[str],
    invalid_reasons: list[str],
    warnings: list[str],
) -> ChangePerformancePairAssessment:
    content = {
        "schema_version": 1,
        "change_id": change_id,
        "status": status,
        "trials": [trial.model_dump(mode="json") for trial in trials],
        "checks": [check.model_dump(mode="json") for check in checks],
        "failures": failures,
        "invalid_reasons": invalid_reasons,
        "warnings": warnings,
    }
    digest = hashlib.sha256(_canonical_json(content)).hexdigest()
    return ChangePerformancePairAssessment(
        assessment_id=f"change-performance-pair-{digest[:32]}",
        **content,
    )


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
