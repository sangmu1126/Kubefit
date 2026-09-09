import json
from pathlib import Path

import pytest

from gitops import ManifestTarget
from safety import (
    ChangePerformanceArtifactError,
    ChangePerformanceRun,
    compare_change_performance,
    load_change_performance_artifact,
    write_change_performance_artifact,
)
from tests.test_benchmark import phase
from tests.test_change_performance import CHANGE_ID, load_result


def performance_run(
    *, failed: bool = False, execution_order: str = "before-after"
) -> ChangePerformanceRun:
    reverse = execution_order == "after-before"
    before = load_result("before", minute=5 if reverse else 0)
    after = load_result(
        "after",
        minute=0 if reverse else 5,
        steady=phase(300, 130, 150) if failed else phase(300, 105, 115),
    )
    return ChangePerformanceRun(
        change_id=CHANGE_ID,
        target=ManifestTarget(namespace="demo", deployment="api", container="api"),
        execution_order=execution_order,
        before=before,
        after=after,
        verdict=compare_change_performance(before, after),
    )


def test_writes_loads_and_idempotently_reuses_complete_artifact(tmp_path: Path) -> None:
    run = performance_run()

    first = write_change_performance_artifact(tmp_path / "results", run)
    second = write_change_performance_artifact(tmp_path / "results", run)
    loaded = load_change_performance_artifact(first.path)

    assert first.artifact_id.startswith("change-performance-")
    assert first.reused is False
    assert second.artifact_id == first.artifact_id
    assert second.reused is True
    assert loaded.run == run
    assert loaded.report_path.read_text().startswith("# Generic change performance")


def test_persists_failed_verdict_as_replayable_evidence(tmp_path: Path) -> None:
    run = performance_run(failed=True)

    artifact = write_change_performance_artifact(tmp_path / "results", run)
    loaded = load_change_performance_artifact(artifact.path)

    assert artifact.status == "fail"
    assert loaded.run.verdict.status == "fail"


def test_reloads_reverse_execution_order_from_measurement_times(tmp_path: Path) -> None:
    run = performance_run(execution_order="after-before")

    artifact = write_change_performance_artifact(tmp_path / "results", run)

    assert load_change_performance_artifact(artifact.path).run.execution_order == (
        "after-before"
    )


@pytest.mark.parametrize(
    "relative_path",
    ["measurements/after.json", "evidence/k6/after-raw.json", "report.md"],
)
def test_rejects_tampered_payload(tmp_path: Path, relative_path: str) -> None:
    artifact = write_change_performance_artifact(
        tmp_path / "results", performance_run()
    )
    target = artifact.path / relative_path
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(ChangePerformanceArtifactError, match="changed"):
        load_change_performance_artifact(artifact.path)


def test_rejects_noncanonical_index_even_with_valid_json(tmp_path: Path) -> None:
    artifact = write_change_performance_artifact(
        tmp_path / "results", performance_run()
    )
    index_path = artifact.path / "result.json"
    raw = json.loads(index_path.read_bytes())
    index_path.write_text(json.dumps(raw))

    with pytest.raises(ChangePerformanceArtifactError, match="not canonical"):
        load_change_performance_artifact(artifact.path)
