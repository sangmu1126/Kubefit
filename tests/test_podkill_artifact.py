from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gitops import ManifestTarget
from safety import (
    ChangePerformanceRun,
    HttpProbeObservation,
    PodKillArtifactError,
    PodKillCandidate,
    PodKillExperimentResult,
    PodKillPreflight,
    PodKillProbeSample,
    compare_change_performance,
    load_podkill_artifact,
    write_change_bundle,
    write_change_performance_artifact,
    write_change_performance_pair,
    write_podkill_artifact,
)
from tests.test_benchmark import phase
from tests.test_change_performance import load_result
from tests.test_deployment_change import BASE

TARGET = ManifestTarget(namespace="demo", deployment="api", container="api")


def prerequisites(tmp_path: Path, *, failed_pair: bool = False):
    base = tmp_path / "base.yaml"
    candidate = tmp_path / "candidate.yaml"
    base.write_text(BASE)
    candidate.write_text(BASE.replace("replicas: 2", "replicas: 3"))
    change = write_change_bundle(
        tmp_path / "changes",
        base,
        candidate,
        namespace="demo",
        deployment="api",
    )
    runs = []
    for order in ("before-after", "after-before"):
        reverse = order == "after-before"
        before = load_result(
            "before", change_id=change.artifact_id, minute=5 if reverse else 0
        )
        after = load_result(
            "after",
            change_id=change.artifact_id,
            minute=0 if reverse else 5,
            steady=(
                phase(300, 130, 150)
                if failed_pair and reverse
                else phase(300, 105, 115)
            ),
        )
        run = ChangePerformanceRun(
            change_id=change.artifact_id,
            target=TARGET,
            execution_order=order,
            before=before,
            after=after,
            verdict=compare_change_performance(before, after),
        )
        runs.append(write_change_performance_artifact(tmp_path / "results", run))
    pair = write_change_performance_pair(
        tmp_path / "pairs", runs[0].path, runs[1].path
    )
    return change, pair


def podkill_result(*, failed: bool = False) -> PodKillExperimentResult:
    started = datetime(2026, 9, 10, tzinfo=UTC)
    deleted = PodKillCandidate(
        pod="api-old",
        pod_uid="pod-old",
        replica_set="api-rs",
        replica_set_uid="rs-uid",
        created_at=started - timedelta(minutes=2),
    )
    remaining = deleted.model_copy(
        update={"pod": "api-newer", "pod_uid": "pod-newer"}
    )
    preflight = PodKillPreflight(
        context="kind-kubefit",
        target=TARGET,
        deployment_uid="deployment-uid",
        deployment_generation=3,
        desired_replicas=2,
        ready_replicas=2,
        candidates=[deleted, remaining],
        selected=deleted,
    )
    sample_times = [0.0, 10.0] if failed else [0.0, 1.0, 2.0]
    successes = [False, False] if failed else [False, True, True]
    samples = [
        PodKillProbeSample(
            observed_at=started + timedelta(seconds=elapsed),
            elapsed_seconds=elapsed,
            observation=HttpProbeObservation(
                success=success,
                status_code=200 if success else 503,
                latency_ms=5,
                error_type=None if success else "HTTPServerError",
            ),
        )
        for elapsed, success in zip(sample_times, successes, strict=True)
    ]
    replacement = None
    if not failed:
        replacement = deleted.model_copy(
            update={"pod": "api-replacement", "pod_uid": "pod-replacement"}
        )
    return PodKillExperimentResult(
        status="fail" if failed else "pass",
        preflight=preflight,
        deleted=deleted,
        replacement=replacement,
        injected_at=started,
        finished_at=started + timedelta(seconds=sample_times[-1]),
        service_recovery_seconds=None if failed else 1,
        replacement_ready_seconds=None if failed else 1,
        required_consecutive_successes=2,
        timeout_seconds=10,
        samples=samples,
        failure_reasons=(
            ["HTTP service did not recover", "replacement Pod was not observed"]
            if failed
            else []
        ),
    )


def test_persists_self_contained_podkill_evidence_and_reuses_it(
    tmp_path: Path,
) -> None:
    change, pair = prerequisites(tmp_path)
    result = podkill_result()

    first = write_podkill_artifact(
        tmp_path / "podkills", change.path, pair.path, result
    )
    second = write_podkill_artifact(
        tmp_path / "podkills", change.path, pair.path, result
    )
    loaded = load_podkill_artifact(first.path)

    assert first.status == "pass"
    assert first.reused is False
    assert second.artifact_id == first.artifact_id
    assert second.reused is True
    assert loaded.result == result
    assert loaded.change.artifact_id == change.artifact_id
    assert loaded.performance_pair.artifact_id == pair.artifact_id


def test_persists_timeout_fail_as_evidence(tmp_path: Path) -> None:
    change, pair = prerequisites(tmp_path)

    artifact = write_podkill_artifact(
        tmp_path / "podkills", change.path, pair.path, podkill_result(failed=True)
    )

    assert artifact.status == "fail"
    assert load_podkill_artifact(artifact.path).result.failure_reasons


def test_rejects_failed_performance_pair_before_creating_output(
    tmp_path: Path,
) -> None:
    change, pair = prerequisites(tmp_path, failed_pair=True)
    output = tmp_path / "podkills"

    with pytest.raises(PodKillArtifactError, match="passing performance Pair"):
        write_podkill_artifact(output, change.path, pair.path, podkill_result())

    assert not output.exists()


def test_rejects_target_not_bound_to_performance_pair(tmp_path: Path) -> None:
    change, pair = prerequisites(tmp_path)
    result = podkill_result()
    other = result.preflight.model_copy(
        update={
            "target": ManifestTarget(
                namespace="demo", deployment="other", container="api"
            )
        }
    )

    with pytest.raises(PodKillArtifactError, match="target does not match"):
        write_podkill_artifact(
            tmp_path / "podkills",
            change.path,
            pair.path,
            result.model_copy(update={"preflight": other}),
        )


def test_rejects_tampered_podkill_result(tmp_path: Path) -> None:
    change, pair = prerequisites(tmp_path)
    artifact = write_podkill_artifact(
        tmp_path / "podkills", change.path, pair.path, podkill_result()
    )
    result_path = artifact.path / "result.json"
    result_path.write_bytes(result_path.read_bytes() + b"\n")

    with pytest.raises(PodKillArtifactError, match="size changed"):
        load_podkill_artifact(artifact.path)
