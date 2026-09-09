import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from safety.podkill_artifact import LoadedPodKillArtifact, load_podkill_artifact
from safety.podkill_campaign import (
    PodKillCampaignError,
    PodKillCampaignPlan,
    load_podkill_campaign_plan,
)


class PodKillCampaignCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    status: Literal["pass", "fail", "incomplete", "invalid"]
    reason: str


class PodKillCampaignAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    campaign_id: str = Field(pattern=r"^podkill-campaign-[0-9a-f]{32}$")
    status: Literal["pass", "fail", "incomplete", "invalid"]
    planned_trials: int = Field(ge=3, le=100)
    completed_trials: int = Field(ge=0, le=100)
    failed_trials: int = Field(ge=0, le=100)
    remaining_trials: int = Field(ge=0, le=100)
    trial_ids: list[str]
    service_recovery_p50_seconds: float | None = Field(default=None, ge=0)
    service_recovery_p95_seconds: float | None = Field(default=None, ge=0)
    replacement_ready_p50_seconds: float | None = Field(default=None, ge=0)
    replacement_ready_p95_seconds: float | None = Field(default=None, ge=0)
    checks: list[PodKillCampaignCheck]
    failures: list[str]
    invalid_reasons: list[str]
    limitations: list[str]


def assess_podkill_campaign(
    plan_path: Path, trial_paths: list[Path]
) -> PodKillCampaignAssessment:
    plan = load_podkill_campaign_plan(plan_path)
    try:
        trials = [load_podkill_artifact(path) for path in trial_paths]
    except (OSError, RuntimeError, ValueError) as exc:
        raise PodKillCampaignError("campaign requires valid PodKill artifacts") from exc
    trials.sort(key=lambda item: item.result.injected_at)
    checks: list[PodKillCampaignCheck] = []

    count = len(trials)
    count_status: Literal["pass", "incomplete", "invalid"] = (
        "pass"
        if count == plan.planned_trials
        else "incomplete"
        if count < plan.planned_trials
        else "invalid"
    )
    checks.append(
        PodKillCampaignCheck(
            code="fixed_trial_count",
            status=count_status,
            reason=f"received {count} of {plan.planned_trials} preregistered trials",
        )
    )
    artifact_ids = [trial.artifact_id for trial in trials]
    deleted_uids = [trial.result.deleted.pod_uid for trial in trials]
    _identity_check(
        checks,
        "unique_trials",
        len(artifact_ids) == len(set(artifact_ids))
        and len(deleted_uids) == len(set(deleted_uids)),
        "every artifact and deleted Pod UID must be unique",
    )
    _identity_check(
        checks,
        "campaign_binding",
        all(_matches_plan(plan, trial) for trial in trials),
        "every trial must match the preregistered change, Pair, context, and target",
    )
    chronological = all(
        current.result.finished_at <= following.result.injected_at
        for current, following in zip(trials, trials[1:], strict=False)
    )
    _identity_check(
        checks,
        "chronological_trials",
        chronological,
        "PodKill trial intervals must not overlap",
    )

    failed = sum(trial.result.status == "fail" for trial in trials)
    _policy_check(
        checks,
        "failure_budget",
        failed <= plan.allowed_failed_trials,
        f"observed {failed} failed trials; allowed {plan.allowed_failed_trials}",
    )
    service_values = _values(trials, "service_recovery_seconds")
    replacement_values = _values(trials, "replacement_ready_seconds")
    _policy_check(
        checks,
        "service_recovery_limit",
        all(value <= plan.service_recovery_limit_seconds for value in service_values),
        f"every observed HTTP recovery must be <= {plan.service_recovery_limit_seconds}s",
    )
    _policy_check(
        checks,
        "replacement_ready_limit",
        all(
            value <= plan.replacement_ready_limit_seconds
            for value in replacement_values
        ),
        (
            "every observed replacement readiness must be <= "
            f"{plan.replacement_ready_limit_seconds}s"
        ),
    )

    invalid_reasons = [check.reason for check in checks if check.status == "invalid"]
    failures = [check.reason for check in checks if check.status == "fail"]
    status: Literal["pass", "fail", "incomplete", "invalid"] = (
        "invalid"
        if invalid_reasons
        else "incomplete"
        if count < plan.planned_trials
        else "fail"
        if failures
        else "pass"
    )
    return PodKillCampaignAssessment(
        campaign_id=plan.campaign_id,
        status=status,
        planned_trials=plan.planned_trials,
        completed_trials=count,
        failed_trials=failed,
        remaining_trials=max(0, plan.planned_trials - count),
        trial_ids=artifact_ids,
        service_recovery_p50_seconds=_nearest_rank(service_values, 0.50),
        service_recovery_p95_seconds=_nearest_rank(service_values, 0.95),
        replacement_ready_p50_seconds=_nearest_rank(replacement_values, 0.50),
        replacement_ready_p95_seconds=_nearest_rank(replacement_values, 0.95),
        checks=checks,
        failures=failures,
        invalid_reasons=invalid_reasons,
        limitations=plan.limitations,
    )


def _matches_plan(plan: PodKillCampaignPlan, trial: LoadedPodKillArtifact) -> bool:
    result = trial.result
    return (
        trial.change.artifact_id == plan.change_id
        and trial.performance_pair.artifact_id == plan.performance_pair_id
        and result.preflight.context == plan.context
        and result.preflight.target == plan.target
    )


def _values(trials: list[LoadedPodKillArtifact], field: str) -> list[float]:
    values = [getattr(trial.result, field) for trial in trials]
    return [value for value in values if value is not None]


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _identity_check(
    checks: list[PodKillCampaignCheck], code: str, valid: bool, reason: str
) -> None:
    checks.append(
        PodKillCampaignCheck(
            code=code,
            status="pass" if valid else "invalid",
            reason=f"{code} is valid" if valid else reason,
        )
    )


def _policy_check(
    checks: list[PodKillCampaignCheck], code: str, passed: bool, reason: str
) -> None:
    checks.append(
        PodKillCampaignCheck(
            code=code,
            status="pass" if passed else "fail",
            reason=f"{code} passed" if passed else reason,
        )
    )
