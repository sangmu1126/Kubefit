from datetime import timedelta
from pathlib import Path

from safety import (
    PodKillExperimentResult,
    assess_podkill_campaign,
    write_podkill_artifact,
    write_podkill_campaign_plan,
)
from tests.test_podkill_artifact import podkill_result, prerequisites


def campaign_inputs(tmp_path: Path, *, allowed_failed: int = 0):
    change, pair = prerequisites(tmp_path)
    campaign = write_podkill_campaign_plan(
        tmp_path / "campaigns",
        change.path,
        pair.path,
        "kind-kubefit",
        3,
        allowed_failed,
        3,
        30,
    )
    return change, pair, campaign


def distinct_result(index: int, *, failed: bool = False) -> PodKillExperimentResult:
    result = podkill_result(failed=failed)
    shift = timedelta(minutes=index)
    deleted = result.deleted.model_copy(
        update={"pod": f"api-old-{index}", "pod_uid": f"pod-old-{index}"}
    )
    remaining = result.preflight.candidates[1].model_copy(
        update={"pod": f"api-newer-{index}", "pod_uid": f"pod-newer-{index}"}
    )
    preflight = result.preflight.model_copy(
        update={"candidates": [deleted, remaining], "selected": deleted}
    )
    replacement = result.replacement
    if replacement is not None:
        replacement = replacement.model_copy(
            update={
                "pod": f"api-replacement-{index}",
                "pod_uid": f"pod-replacement-{index}",
            }
        )
    samples = [
        sample.model_copy(update={"observed_at": sample.observed_at + shift})
        for sample in result.samples
    ]
    return PodKillExperimentResult.model_validate(
        result.model_dump()
        | {
            "preflight": preflight,
            "deleted": deleted,
            "replacement": replacement,
            "injected_at": result.injected_at + shift,
            "finished_at": result.finished_at + shift,
            "samples": samples,
        }
    )


def publish_trials(tmp_path: Path, change, pair, outcomes=(False, False, False)):
    return [
        write_podkill_artifact(
            tmp_path / "podkills",
            change.path,
            pair.path,
            distinct_result(index, failed=failed),
        )
        for index, failed in enumerate(outcomes)
    ]


def test_passes_complete_unique_chronological_campaign_and_reports_distribution(
    tmp_path: Path,
) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair)

    assessment = assess_podkill_campaign(
        campaign.path, [trial.path for trial in reversed(trials)]
    )

    assert assessment.status == "pass"
    assert assessment.completed_trials == 3
    assert assessment.remaining_trials == 0
    assert assessment.service_recovery_p50_seconds == 1
    assert assessment.service_recovery_p95_seconds == 1
    assert assessment.trial_ids == [trial.artifact_id for trial in trials]


def test_remains_incomplete_until_every_preregistered_trial_exists(
    tmp_path: Path,
) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair)

    assessment = assess_podkill_campaign(
        campaign.path, [trial.path for trial in trials[:2]]
    )

    assert assessment.status == "incomplete"
    assert assessment.remaining_trials == 1


def test_fails_complete_campaign_when_failure_budget_is_exceeded(
    tmp_path: Path,
) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair, (False, True, False))

    assessment = assess_podkill_campaign(
        campaign.path, [trial.path for trial in trials]
    )

    assert assessment.status == "fail"
    assert assessment.failed_trials == 1
    assert any(check.code == "failure_budget" for check in assessment.checks)


def test_fails_complete_campaign_when_preregistered_recovery_limit_is_exceeded(
    tmp_path: Path,
) -> None:
    change, pair = prerequisites(tmp_path)
    campaign = write_podkill_campaign_plan(
        tmp_path / "campaigns",
        change.path,
        pair.path,
        "kind-kubefit",
        3,
        0,
        0.5,
        30,
    )
    trials = publish_trials(tmp_path, change, pair)

    assessment = assess_podkill_campaign(
        campaign.path, [trial.path for trial in trials]
    )

    assert assessment.status == "fail"
    assert any(
        check.code == "service_recovery_limit" and check.status == "fail"
        for check in assessment.checks
    )


def test_invalidates_duplicate_trial_instead_of_counting_it_twice(
    tmp_path: Path,
) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    trials = publish_trials(tmp_path, change, pair)

    assessment = assess_podkill_campaign(
        campaign.path, [trials[0].path, trials[0].path, trials[1].path]
    )

    assert assessment.status == "invalid"
    assert any(
        check.code == "unique_trials" and check.status == "invalid"
        for check in assessment.checks
    )


def test_invalidates_mixed_context_even_when_change_and_pair_match(
    tmp_path: Path,
) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    results = [distinct_result(index) for index in range(3)]
    foreign_preflight = results[2].preflight.model_copy(
        update={"context": "kind-other"}
    )
    results[2] = results[2].model_copy(update={"preflight": foreign_preflight})
    trials = [
        write_podkill_artifact(
            tmp_path / "podkills", change.path, pair.path, result
        )
        for result in results
    ]

    assessment = assess_podkill_campaign(
        campaign.path, [trial.path for trial in trials]
    )

    assert assessment.status == "invalid"
    assert any(
        check.code == "campaign_binding" and check.status == "invalid"
        for check in assessment.checks
    )


def test_invalidates_overlapping_trial_intervals(tmp_path: Path) -> None:
    change, pair, campaign = campaign_inputs(tmp_path)
    first = distinct_result(0)
    overlapping = distinct_result(1).model_copy(
        update={
            "injected_at": first.injected_at,
            "finished_at": first.finished_at,
            "samples": first.samples,
        }
    )
    results = [first, overlapping, distinct_result(2)]
    trials = [
        write_podkill_artifact(
            tmp_path / "podkills", change.path, pair.path, result
        )
        for result in results
    ]

    assessment = assess_podkill_campaign(
        campaign.path, [trial.path for trial in trials]
    )

    assert assessment.status == "invalid"
    assert any(
        check.code == "chronological_trials" and check.status == "invalid"
        for check in assessment.checks
    )
