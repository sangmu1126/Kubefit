from pathlib import Path

import pytest

from safety import (
    ChangePerformancePairArtifactError,
    load_change_performance_pair,
    write_change_performance_pair,
)
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
