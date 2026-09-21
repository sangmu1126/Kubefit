from pathlib import Path

import pytest

from gitops import ManifestTarget
from safety import (
    ChangePerformancePairArtifactError,
    ChangePerformancePolicy,
    ChangePerformanceRun,
    compare_change_performance,
    load_change_performance_pair,
    write_change_performance_artifact,
    write_change_performance_pair,
)
from tests.test_change_performance import CHANGE_ID, load_result
from tests.test_change_performance_pair import published_pair


def test_persists_self_contained_pass_pair_and_reuses_it(tmp_path: Path) -> None:
    first, second = published_pair(tmp_path)

    initial = write_change_performance_pair(tmp_path / "pairs", first.path, second.path)
    repeated = write_change_performance_pair(tmp_path / "pairs", second.path, first.path)
    loaded = load_change_performance_pair(initial.path)

    assert initial.status == "pass"
    assert initial.reused is False
    assert repeated.artifact_id == initial.artifact_id
    assert repeated.reused is True
    assert loaded.assessment.status == "pass"
    assert loaded.before_after.run.execution_order == "before-after"
    assert loaded.after_before.run.execution_order == "after-before"


def test_persists_failed_pair_instead_of_hiding_failed_trial(tmp_path: Path) -> None:
    first, second = published_pair(tmp_path, reverse_failed=True)

    artifact = write_change_performance_pair(tmp_path / "pairs", first.path, second.path)
    loaded = load_change_performance_pair(artifact.path)

    assert artifact.status == "fail"
    assert loaded.assessment.status == "fail"
    assert loaded.assessment.failures


def test_persists_review_required_pair_without_promoting_it_to_pass(tmp_path: Path) -> None:
    trials = []
    policy = ChangePerformancePolicy(require_throttling=True)
    for reverse in (False, True):
        before = load_result("before", minute=5 if reverse else 0)
        after = load_result("after", minute=0 if reverse else 5)
        run = ChangePerformanceRun(
            change_id=CHANGE_ID,
            target=ManifestTarget(namespace="demo", deployment="api", container="api"),
            execution_order="after-before" if reverse else "before-after",
            before=before,
            after=after,
            policy=policy,
            verdict=compare_change_performance(before, after, policy),
        )
        trials.append(write_change_performance_artifact(tmp_path / "results", run))

    pair = write_change_performance_pair(tmp_path / "pairs", trials[0].path, trials[1].path)
    loaded = load_change_performance_pair(pair.path)

    assert pair.status == "review_required"
    assert loaded.assessment.status == "review_required"


def test_rejects_invalid_pair_without_creating_output(tmp_path: Path) -> None:
    first, _ = published_pair(tmp_path)
    output = tmp_path / "pairs"

    with pytest.raises(ChangePerformancePairArtifactError, match="invalid"):
        write_change_performance_pair(output, first.path, first.path)

    assert not output.exists()


def test_rejects_tampered_embedded_trial(tmp_path: Path) -> None:
    first, second = published_pair(tmp_path)
    artifact = write_change_performance_pair(tmp_path / "pairs", first.path, second.path)
    target = artifact.path / "trials" / artifact.trial_ids[0] / "verdict.json"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(ChangePerformancePairArtifactError, match="size changed"):
        load_change_performance_pair(artifact.path)
