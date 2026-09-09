import json
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from collector import compile_label_selector
from collector.kubernetes import KubernetesCollectionError
from gitops import ManifestTarget


class PodKillPreflightError(RuntimeError):
    """Raised when a Deployment is not safe for a controlled PodKill experiment."""


class PodKillCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    pod: str = Field(min_length=1)
    pod_uid: str = Field(min_length=1)
    replica_set: str = Field(min_length=1)
    replica_set_uid: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def timestamp_has_timezone(self) -> "PodKillCandidate":
        if self.created_at.tzinfo is None:
            raise ValueError("Pod creation timestamp must include timezone")
        return self


class PodKillPreflight(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    context: str = Field(pattern=r"^kind-.+")
    target: ManifestTarget
    deployment_uid: str = Field(min_length=1)
    deployment_generation: int = Field(ge=1)
    desired_replicas: int = Field(ge=2)
    ready_replicas: int = Field(ge=2)
    candidates: list[PodKillCandidate] = Field(min_length=2)
    selected: PodKillCandidate

    @model_validator(mode="after")
    def selected_candidate_is_owned_and_ready(self) -> "PodKillPreflight":
        if self.ready_replicas != self.desired_replicas:
            raise ValueError("all desired replicas must be ready")
        if len(self.candidates) != self.desired_replicas:
            raise ValueError("ready owned Pod count must equal desired replicas")
        if self.selected not in self.candidates:
            raise ValueError("selected Pod must be one of the verified candidates")
        return self


CommandRunner = Callable[[Sequence[str]], str]


class KubectlPodKillPreflight:
    def __init__(self, context: str, runner: CommandRunner | None = None) -> None:
        if not context.startswith("kind-"):
            raise ValueError("PodKill preflight is restricted to an explicit kind-* context")
        self._context = context
        self._runner = runner or _run_command

    def _command(self, *arguments: str) -> list[str]:
        return ["kubectl", "--context", self._context, *arguments]

    def inspect(self, target: ManifestTarget) -> PodKillPreflight:
        deployment = self._json(
            self._command(
                "get",
                "deployment",
                target.deployment,
                "--namespace",
                target.namespace,
                "--output",
                "json",
            ),
            "Deployment",
        )
        metadata = _mapping(deployment.get("metadata"), "Deployment metadata")
        spec = _mapping(deployment.get("spec"), "Deployment spec")
        status = _mapping(deployment.get("status"), "Deployment status")
        deployment_uid = _text(metadata.get("uid"), "Deployment UID")
        generation = _positive_integer(metadata.get("generation"), "Deployment generation")
        desired = _integer(spec.get("replicas", 1), "desired replicas")
        if desired < 2:
            raise PodKillPreflightError(
                "controlled PodKill requires at least two desired replicas"
            )
        observed = _integer(status.get("observedGeneration"), "observed generation")
        if observed < generation:
            raise PodKillPreflightError("Deployment controller has not observed this generation")
        for field in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"):
            if _integer(status.get(field, 0), field) != desired:
                raise PodKillPreflightError(
                    f"Deployment {field} must equal desired replicas before PodKill"
                )
        try:
            selector = compile_label_selector(_mapping(spec.get("selector"), "selector"))
        except KubernetesCollectionError as exc:
            raise PodKillPreflightError(f"Deployment selector is invalid: {exc}") from exc
        replica_sets = self._owned_replica_sets(target, selector, deployment_uid)
        pods = self._json(
            self._command(
                "get",
                "pods",
                "--namespace",
                target.namespace,
                "--selector",
                selector,
                "--output",
                "json",
            ),
            "Pod list",
        )
        items = pods.get("items")
        if not isinstance(items, list):
            raise PodKillPreflightError("Pod list items must be an array")
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                raise PodKillPreflightError("Pod list contains a non-object item")
            owner = _controller_owner(item, "ReplicaSet")
            if owner is None or owner[1] not in replica_sets:
                continue
            candidates.append(
                _ready_candidate(item, target.container, owner[0], owner[1])
            )
        candidates.sort(key=lambda item: (item.created_at, item.pod))
        if len(candidates) != desired:
            raise PodKillPreflightError(
                f"expected {desired} ready owned Pods, found {len(candidates)}"
            )
        return PodKillPreflight(
            context=self._context,
            target=target,
            deployment_uid=deployment_uid,
            deployment_generation=generation,
            desired_replicas=desired,
            ready_replicas=len(candidates),
            candidates=candidates,
            selected=candidates[0],
        )

    def _owned_replica_sets(
        self,
        target: ManifestTarget,
        selector: str,
        deployment_uid: str,
    ) -> dict[str, str]:
        document = self._json(
            self._command(
                "get",
                "replicasets",
                "--namespace",
                target.namespace,
                "--selector",
                selector,
                "--output",
                "json",
            ),
            "ReplicaSet list",
        )
        items = document.get("items")
        if not isinstance(items, list):
            raise PodKillPreflightError("ReplicaSet list items must be an array")
        owned: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                raise PodKillPreflightError("ReplicaSet list contains a non-object item")
            owner = _controller_owner(item, "Deployment")
            if owner is None or owner[1] != deployment_uid:
                continue
            metadata = _mapping(item.get("metadata"), "ReplicaSet metadata")
            owned[_text(metadata.get("uid"), "ReplicaSet UID")] = _text(
                metadata.get("name"), "ReplicaSet name"
            )
        if not owned:
            raise PodKillPreflightError("Deployment has no ReplicaSets owned by its UID")
        return owned

    def _json(self, command: Sequence[str], label: str) -> dict[str, object]:
        try:
            value = json.loads(self._runner(command))
        except (json.JSONDecodeError, TypeError) as exc:
            raise PodKillPreflightError(f"{label} response is invalid JSON") from exc
        if not isinstance(value, dict):
            raise PodKillPreflightError(f"{label} response must be an object")
        return value


def _ready_candidate(
    pod: dict[str, object],
    container: str,
    replica_set: str,
    replica_set_uid: str,
) -> PodKillCandidate:
    metadata = _mapping(pod.get("metadata"), "Pod metadata")
    status = _mapping(pod.get("status"), "Pod status")
    name = _text(metadata.get("name"), "Pod name")
    if metadata.get("deletionTimestamp") is not None:
        raise PodKillPreflightError(f"Pod {name} is terminating")
    if status.get("phase") != "Running":
        raise PodKillPreflightError(f"Pod {name} is not Running")
    conditions = status.get("conditions", [])
    if not isinstance(conditions, list) or not any(
        isinstance(item, dict)
        and item.get("type") == "Ready"
        and item.get("status") == "True"
        for item in conditions
    ):
        raise PodKillPreflightError(f"Pod {name} is not Ready")
    statuses = status.get("containerStatuses", [])
    if not isinstance(statuses, list):
        raise PodKillPreflightError(f"Pod {name} container statuses are invalid")
    selected = next(
        (
            item
            for item in statuses
            if isinstance(item, dict) and item.get("name") == container
        ),
        None,
    )
    if selected is None or selected.get("ready") is not True:
        raise PodKillPreflightError(f"container {container} in Pod {name} is not ready")
    created_at = _timestamp(metadata.get("creationTimestamp"), f"Pod {name} creation time")
    return PodKillCandidate(
        pod=name,
        pod_uid=_text(metadata.get("uid"), f"Pod {name} UID"),
        replica_set=replica_set,
        replica_set_uid=replica_set_uid,
        created_at=created_at,
    )


def _controller_owner(
    document: dict[str, object],
    kind: str,
) -> tuple[str, str] | None:
    metadata = _mapping(document.get("metadata"), "object metadata")
    owners = metadata.get("ownerReferences", [])
    if not isinstance(owners, list):
        raise PodKillPreflightError("ownerReferences must be an array")
    matches = [
        item
        for item in owners
        if isinstance(item, dict)
        and item.get("controller") is True
        and item.get("kind") == kind
    ]
    if len(matches) > 1:
        raise PodKillPreflightError(f"object has multiple controlling {kind} owners")
    if not matches:
        return None
    return (
        _text(matches[0].get("name"), f"{kind} owner name"),
        _text(matches[0].get("uid"), f"{kind} owner UID"),
    )


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PodKillPreflightError(f"{label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PodKillPreflightError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PodKillPreflightError(f"{label} must be an integer")
    return value


def _positive_integer(value: object, label: str) -> int:
    parsed = _integer(value, label)
    if parsed < 1:
        raise PodKillPreflightError(f"{label} must be positive")
    return parsed


def _timestamp(value: object, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PodKillPreflightError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise PodKillPreflightError(f"{label} must include timezone")
    return parsed


def _run_command(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise PodKillPreflightError(f"kubectl failed: {detail.strip()}") from exc
    return completed.stdout
