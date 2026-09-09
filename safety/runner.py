from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from gitops import ManifestTarget
from safety.bundle import load_change_bundle


class ChangeExecutionError(RuntimeError):
    """Raised when generic change execution or mandatory restoration fails."""

    def __init__(
        self,
        stage: str,
        cause: BaseException | None,
        restoration_error: Exception | None = None,
    ) -> None:
        self.stage = stage
        self.cause = cause
        self.restoration_error = restoration_error
        detail = f"change execution failed during {stage}"
        if cause is not None:
            detail += f": {cause}"
        if restoration_error is not None:
            detail += f"; base restoration also failed: {restoration_error}"
        super().__init__(detail)


class ChangeManifestController(Protocol):
    def apply(self, manifest: Path, target: ManifestTarget) -> None: ...

    def wait_for_rollout(self, target: ManifestTarget) -> None: ...


class ChangeExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    target: ManifestTarget
    status: Literal["pass"] = "pass"
    candidate_ready: Literal[True] = True
    restored: Literal[True] = True


def execute_change_bundle(
    bundle_path: Path,
    controller: ChangeManifestController,
    *,
    container: str,
) -> ChangeExecutionResult:
    """Apply exact bundle manifests and restore base on every post-apply exit path."""
    bundle = load_change_bundle(bundle_path)
    target = ManifestTarget(
        namespace=bundle.change.namespace,
        deployment=bundle.change.deployment,
        container=container,
    )
    restore_required = False
    primary_error: BaseException | None = None
    stage = "apply_base"

    try:
        restore_required = True
        controller.apply(bundle.base_manifest, target)
        stage = "wait_base_rollout"
        controller.wait_for_rollout(target)
        stage = "apply_candidate"
        controller.apply(bundle.candidate_manifest, target)
        stage = "wait_candidate_rollout"
        controller.wait_for_rollout(target)
    except BaseException as exc:
        primary_error = exc

    restoration_error: Exception | None = None
    if restore_required:
        try:
            controller.apply(bundle.base_manifest, target)
            controller.wait_for_rollout(target)
        except Exception as exc:
            restoration_error = exc

    if restoration_error is not None:
        failure_stage = stage if primary_error is not None else "restore_base"
        raise ChangeExecutionError(
            failure_stage, primary_error, restoration_error
        ) from restoration_error
    if primary_error is not None:
        if not isinstance(primary_error, Exception):
            raise primary_error
        raise ChangeExecutionError(stage, primary_error) from primary_error
    return ChangeExecutionResult(
        change_id=bundle.artifact_id,
        target=target,
    )
