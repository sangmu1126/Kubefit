from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from gitops import ManifestTarget
from safety import (
    HttpProbeObservation,
    PodKillCandidate,
    PodKillExperimentError,
    PodKillExperimentResult,
    PodKillExperimentRunner,
    PodKillPreflight,
    PodKillPreflightError,
)


def candidate(name: str, uid: str, minute: int) -> PodKillCandidate:
    return PodKillCandidate(
        pod=name,
        pod_uid=uid,
        replica_set="api-rs",
        replica_set_uid="rs-uid",
        created_at=datetime(2026, 9, 10, tzinfo=UTC) + timedelta(minutes=minute),
    )


def preflight(*, replacement: bool = False, generation: int = 3) -> PodKillPreflight:
    remaining = candidate("api-newer", "pod-newer", 1)
    candidates = (
        [remaining, candidate("api-replacement", "pod-replacement", 2)]
        if replacement
        else [candidate("api-old", "pod-old", 0), remaining]
    )
    return PodKillPreflight(
        context="kind-kubefit",
        target=ManifestTarget(namespace="demo", deployment="api", container="api"),
        deployment_uid="deployment-uid",
        deployment_generation=generation,
        desired_replicas=2,
        ready_replicas=2,
        candidates=candidates,
        selected=candidates[0],
    )


class SequenceInspector:
    def __init__(self, values) -> None:
        self.values = iter(values)

    def inspect(self, target):
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


class FakeTime:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.started = datetime(2026, 9, 10, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.elapsed

    def utc(self) -> datetime:
        return self.started + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds


def observation(success: bool) -> HttpProbeObservation:
    return HttpProbeObservation(
        success=success,
        status_code=200 if success else 503,
        latency_ms=5,
        error_type=None if success else "HTTPServerError",
    )


def test_revalidates_deletes_once_and_measures_both_recoveries() -> None:
    approved = preflight()
    inspector = SequenceInspector(
        [
            approved,
            PodKillPreflightError("replacement pending"),
            preflight(replacement=True),
            preflight(replacement=True),
        ]
    )
    clock = FakeTime()
    commands: list[list[str]] = []
    probes = iter([observation(False), observation(True), observation(True)])
    runner = PodKillExperimentRunner(
        "kind-kubefit",
        inspector=inspector,
        command_runner=lambda command: commands.append(list(command)) or "",
        probe=lambda url, timeout: next(probes),
        monotonic=clock.monotonic,
        utc_clock=clock.utc,
        sleeper=clock.sleep,
        timeout_seconds=10,
        probe_interval_seconds=1,
        required_consecutive_successes=2,
    )

    result = runner.run(approved, "http://127.0.0.1:8080/")

    assert result.status == "pass"
    assert result.deleted.pod_uid == "pod-old"
    assert result.replacement is not None
    assert result.replacement.pod_uid == "pod-replacement"
    assert result.service_recovery_seconds == 1
    assert result.replacement_ready_seconds == 1
    assert [sample.observation.success for sample in result.samples] == [False, True, True]
    assert commands == [
        [
            "kubectl",
            "--context",
            "kind-kubefit",
            "delete",
            "pod",
            "api-old",
            "--namespace",
            "demo",
            "--grace-period=1",
            "--wait=false",
        ]
    ]


def test_refuses_mutation_when_preflight_identity_changed() -> None:
    approved = preflight()
    commands: list[list[str]] = []
    runner = PodKillExperimentRunner(
        "kind-kubefit",
        inspector=SequenceInspector([preflight(generation=4)]),
        command_runner=lambda command: commands.append(list(command)) or "",
    )

    with pytest.raises(PodKillExperimentError, match="generation changed"):
        runner.run(approved, "http://127.0.0.1:8080")

    assert commands == []


def test_returns_failed_evidence_after_bounded_timeout() -> None:
    approved = preflight()
    clock = FakeTime()
    runner = PodKillExperimentRunner(
        "kind-kubefit",
        inspector=SequenceInspector(
            [
                approved,
                PodKillPreflightError("pending"),
                PodKillPreflightError("pending"),
            ]
        ),
        command_runner=lambda command: "",
        probe=lambda url, timeout: observation(False),
        monotonic=clock.monotonic,
        utc_clock=clock.utc,
        sleeper=clock.sleep,
        timeout_seconds=1,
        probe_interval_seconds=1,
    )

    result = runner.run(approved, "http://127.0.0.1:8080")

    assert result.status == "fail"
    assert result.replacement is None
    assert result.service_recovery_seconds is None
    assert len(result.samples) == 2
    assert len(result.failure_reasons) == 2


def test_rejects_unsafe_probe_url_before_revalidation_or_delete() -> None:
    runner = PodKillExperimentRunner(
        "kind-kubefit",
        inspector=SequenceInspector([]),
        command_runner=lambda command: pytest.fail("must not delete"),
    )

    with pytest.raises(ValueError, match="without credentials"):
        runner.run(preflight(), "http://user:secret@127.0.0.1/")


def test_rejects_non_kind_context_at_runner_boundary() -> None:
    with pytest.raises(ValueError, match=r"kind-\*"):
        PodKillExperimentRunner("production")


def test_result_rejects_recovery_value_not_replayed_by_samples() -> None:
    approved = preflight()
    values = {
        "status": "pass",
        "preflight": approved,
        "deleted": approved.selected,
        "replacement": candidate("api-replacement", "replacement-uid", 2),
        "injected_at": datetime(2026, 9, 10, tzinfo=UTC),
        "finished_at": datetime(2026, 9, 10, tzinfo=UTC) + timedelta(seconds=2),
        "service_recovery_seconds": 0,
        "replacement_ready_seconds": 2,
        "required_consecutive_successes": 2,
        "timeout_seconds": 10,
        "samples": [
            {
                "observed_at": datetime(2026, 9, 10, tzinfo=UTC),
                "elapsed_seconds": 0,
                "observation": observation(False),
            },
            {
                "observed_at": datetime(2026, 9, 10, tzinfo=UTC)
                + timedelta(seconds=1),
                "elapsed_seconds": 1,
                "observation": observation(True),
            },
            {
                "observed_at": datetime(2026, 9, 10, tzinfo=UTC)
                + timedelta(seconds=2),
                "elapsed_seconds": 2,
                "observation": observation(True),
            },
        ],
        "failure_reasons": [],
    }

    with pytest.raises(ValidationError, match="does not replay"):
        PodKillExperimentResult.model_validate(values)
