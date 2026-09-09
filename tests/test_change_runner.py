from pathlib import Path

import pytest

from safety import ChangeExecutionError, execute_change_bundle, write_change_bundle
from tests.test_deployment_change import BASE


class RecordingController:
    def __init__(self, failure: str | None = None) -> None:
        self.events: list[str] = []
        self.failure = failure

    def apply(self, manifest: Path, target) -> None:
        variant = manifest.stem
        event = f"apply:{variant}"
        self.events.append(event)
        if self.failure == event:
            raise RuntimeError(event)

    def wait_for_rollout(self, target) -> None:
        event = f"wait:{self.events[-1].split(':')[1]}"
        self.events.append(event)
        if self.failure == event:
            raise RuntimeError(event)


def _bundle(tmp_path: Path) -> Path:
    base = tmp_path / "base-input.yaml"
    candidate = tmp_path / "candidate-input.yaml"
    base.write_text(BASE)
    candidate.write_text(BASE.replace("replicas: 2", "replicas: 3"))
    return write_change_bundle(
        tmp_path / "changes",
        base,
        candidate,
        namespace="demo",
        deployment="api",
    ).path


def test_executes_base_candidate_and_mandatory_base_restoration(tmp_path: Path) -> None:
    controller = RecordingController()

    result = execute_change_bundle(_bundle(tmp_path), controller, container="api")

    assert result.status == "pass"
    assert result.candidate_ready is True
    assert result.restored is True
    assert result.target.model_dump() == {
        "namespace": "demo",
        "deployment": "api",
        "container": "api",
    }
    assert controller.events == [
        "apply:base",
        "wait:base",
        "apply:candidate",
        "wait:candidate",
        "apply:base",
        "wait:base",
    ]


def test_restores_base_after_candidate_rollout_failure(tmp_path: Path) -> None:
    controller = RecordingController(failure="wait:candidate")

    with pytest.raises(ChangeExecutionError) as raised:
        execute_change_bundle(_bundle(tmp_path), controller, container="api")

    assert raised.value.stage == "wait_candidate_rollout"
    assert raised.value.restoration_error is None
    assert controller.events[-2:] == ["apply:base", "wait:base"]


def test_reports_restoration_failure_as_the_dominant_safety_error(tmp_path: Path) -> None:
    class RestoreFailureController(RecordingController):
        def apply(self, manifest: Path, target) -> None:
            super().apply(manifest, target)
            if self.events.count("apply:base") == 2:
                raise RuntimeError("restore failed")

    controller = RestoreFailureController()

    with pytest.raises(ChangeExecutionError) as raised:
        execute_change_bundle(_bundle(tmp_path), controller, container="api")

    assert raised.value.stage == "restore_base"
    assert raised.value.restoration_error is not None
    assert "base restoration also failed" in str(raised.value)


def test_restores_base_before_propagating_forced_interruption(tmp_path: Path) -> None:
    class InterruptController(RecordingController):
        def wait_for_rollout(self, target) -> None:
            super().wait_for_rollout(target)
            if self.events[-1] == "wait:candidate":
                raise KeyboardInterrupt

    controller = InterruptController()

    with pytest.raises(KeyboardInterrupt):
        execute_change_bundle(_bundle(tmp_path), controller, container="api")

    assert controller.events[-2:] == ["apply:base", "wait:base"]
