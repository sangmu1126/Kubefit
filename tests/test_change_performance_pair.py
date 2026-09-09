from pathlib import Path

from safety import (
    assess_change_performance_pair,
    write_change_performance_artifact,
)
from tests.test_change_performance_artifact import performance_run


def published_pair(tmp_path: Path, *, reverse_failed: bool = False):
    results = tmp_path / "results"
    first = write_change_performance_artifact(
        results, performance_run(execution_order="before-after")
    )
    second = write_change_performance_artifact(
        results,
        performance_run(failed=reverse_failed, execution_order="after-before"),
    )
    return first, second


def test_passes_two_opposite_order_generic_artifacts_deterministically(
    tmp_path: Path,
) -> None:
    first, second = published_pair(tmp_path)

    assessment = assess_change_performance_pair(first.path, second.path)
    reversed_assessment = assess_change_performance_pair(second.path, first.path)

    assert assessment == reversed_assessment
    assert assessment.status == "pass"
    assert assessment.assessment_id.startswith("change-performance-pair-")
    assert {trial.execution_order for trial in assessment.trials} == {
        "before-after",
        "after-before",
    }
    assert not assessment.failures
    assert not assessment.invalid_reasons


def test_invalidates_duplicate_artifact_instead_of_claiming_pair(tmp_path: Path) -> None:
    first, _ = published_pair(tmp_path)

    assessment = assess_change_performance_pair(first.path, first.path)

    assert assessment.status == "invalid"
    assert {check.code for check in assessment.checks if check.status == "invalid"} == {
        "distinct_artifacts",
        "opposite_orders",
    }


def test_invalidates_two_distinct_artifacts_in_same_order(tmp_path: Path) -> None:
    results = tmp_path / "results"
    first = write_change_performance_artifact(results, performance_run())
    shifted = performance_run().model_copy(
        update={
            "before": performance_run().before.model_copy(
                update={
                    "started_at": performance_run().before.started_at.replace(hour=1),
                    "finished_at": performance_run().before.finished_at.replace(hour=1),
                }
            ),
            "after": performance_run().after.model_copy(
                update={
                    "started_at": performance_run().after.started_at.replace(hour=1),
                    "finished_at": performance_run().after.finished_at.replace(hour=1),
                }
            ),
        }
    )
    second = write_change_performance_artifact(results, shifted)

    assessment = assess_change_performance_pair(first.path, second.path)

    assert assessment.status == "invalid"
    assert next(
        check.status for check in assessment.checks if check.code == "opposite_orders"
    ) == "invalid"


def test_one_failed_trial_fails_pair_without_averaging_it_away(tmp_path: Path) -> None:
    first, second = published_pair(tmp_path, reverse_failed=True)

    assessment = assess_change_performance_pair(first.path, second.path)

    assert assessment.status == "fail"
    assert {check.code for check in assessment.checks if check.status == "fail"} == {
        "policy_check_agreement",
        "both_trials_pass",
    }
