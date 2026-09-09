import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from safety.change import (
    DeploymentChange,
    DeploymentChangeError,
    inspect_deployment_change,
)


class ChangeBundleError(RuntimeError):
    """Raised when an immutable Deployment change bundle is unsafe or invalid."""


class ChangeBundleFileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ChangeBundleIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, ChangeBundleFileMetadata]


class ChangeBundleArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    path: Path
    reused: bool
    files: list[str]


class LoadedChangeBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    path: Path
    change: DeploymentChange
    base_manifest: Path
    candidate_manifest: Path


PAYLOAD_PATHS = frozenset(
    {"change.json", "manifests/base.yaml", "manifests/candidate.yaml"}
)


def write_change_bundle(
    output_root: Path,
    base_path: Path,
    candidate_path: Path,
    *,
    namespace: str,
    deployment: str,
) -> ChangeBundleArtifact:
    base_content = _read_input(base_path)
    candidate_content = _read_input(candidate_path)
    try:
        change = inspect_deployment_change(
            base_path,
            candidate_path,
            namespace=namespace,
            deployment=deployment,
        )
    except DeploymentChangeError as exc:
        raise ChangeBundleError("could not inspect Deployment change") from exc
    if change.status != "supported":
        raise ChangeBundleError(f"Deployment change scope is {change.status}")
    if base_content != _read_input(base_path) or candidate_content != _read_input(
        candidate_path
    ):
        raise ChangeBundleError("manifest changed while the bundle was being prepared")
    payloads = {
        "change.json": _canonical_json(change.model_dump(mode="json")),
        "manifests/base.yaml": base_content,
        "manifests/candidate.yaml": candidate_content,
    }
    digest = _content_digest(payloads)
    artifact_id = f"change-{digest[:32]}"
    index = ChangeBundleIndex(
        artifact_id=artifact_id,
        content_digest_sha256=digest,
        files={
            name: ChangeBundleFileMetadata(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            for name, content in sorted(payloads.items())
        },
    )
    payloads["change-bundle.json"] = _canonical_json(index.model_dump(mode="json"))
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final_path = output_root / artifact_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            loaded = load_change_bundle(final_path)
            if loaded.artifact_id != artifact_id:
                raise ChangeBundleError("existing change bundle identity changed")
            return ChangeBundleArtifact(
                artifact_id=artifact_id,
                path=final_path,
                reused=True,
                files=sorted(payloads),
            )
        staging = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=output_root))
        staging.chmod(0o700)
        for relative_path, content in sorted(payloads.items()):
            destination = staging / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with destination.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            destination.chmod(0o600)
        _fsync_tree(staging)
        os.rename(staging, final_path)
        staging = None
        _fsync_directory(output_root)
        return ChangeBundleArtifact(
            artifact_id=artifact_id,
            path=final_path,
            reused=False,
            files=sorted(payloads),
        )
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
        _fsync_directory(output_root)


def load_change_bundle(path: Path) -> LoadedChangeBundle:
    if path.is_symlink() or not path.is_dir():
        raise ChangeBundleError("change bundle path must be a regular directory")
    index_path = path / "change-bundle.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise ChangeBundleError("change bundle is missing change-bundle.json")
    try:
        index_bytes = index_path.read_bytes()
        raw_index = json.loads(index_bytes)
        index = ChangeBundleIndex.model_validate(raw_index)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ChangeBundleError("change bundle index is invalid") from exc
    if index_bytes != _canonical_json(raw_index):
        raise ChangeBundleError("change bundle index is not canonical JSON")
    if path.name != index.artifact_id:
        raise ChangeBundleError("change bundle directory does not match its artifact ID")
    if index.content_digest_sha256[:32] != index.artifact_id.removeprefix("change-"):
        raise ChangeBundleError("change bundle ID does not match its content digest")
    if set(index.files) != PAYLOAD_PATHS:
        raise ChangeBundleError("change bundle payload set is invalid")
    actual_files = []
    payloads = {}
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ChangeBundleError("change bundle contains a symlink")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if set(actual_files) != PAYLOAD_PATHS | {"change-bundle.json"}:
        raise ChangeBundleError("change bundle file set is invalid")
    for name, metadata in index.files.items():
        content = (path / name).read_bytes()
        if len(content) != metadata.size_bytes:
            raise ChangeBundleError(f"change bundle payload size changed: {name}")
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise ChangeBundleError(f"change bundle payload digest changed: {name}")
        payloads[name] = content
    if _content_digest(payloads) != index.content_digest_sha256:
        raise ChangeBundleError("change bundle content digest is invalid")
    try:
        raw_change = json.loads(payloads["change.json"])
        persisted = DeploymentChange.model_validate(raw_change)
        if payloads["change.json"] != _canonical_json(raw_change):
            raise ChangeBundleError("change bundle decision is not canonical JSON")
        replayed = inspect_deployment_change(
            path / "manifests/base.yaml",
            path / "manifests/candidate.yaml",
            namespace=persisted.namespace,
            deployment=persisted.deployment,
        )
    except (DeploymentChangeError, json.JSONDecodeError, ValueError) as exc:
        raise ChangeBundleError("change bundle decision is invalid") from exc
    if persisted != replayed or replayed.status != "supported":
        raise ChangeBundleError("change bundle decision does not replay")
    return LoadedChangeBundle(
        artifact_id=index.artifact_id,
        path=path,
        change=replayed,
        base_manifest=path / "manifests/base.yaml",
        candidate_manifest=path / "manifests/candidate.yaml",
    )


def _read_input(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ChangeBundleError("manifest input must be a regular, non-symlinked file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ChangeBundleError("could not read manifest input") from exc


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _content_digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(payloads.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(str(len(content)).encode())
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _validate_output_root(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ChangeBundleError("change bundle output root must be a regular directory")


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ChangeBundleError("another change bundle publication holds the lock") from exc


def _fsync_tree(root: Path) -> None:
    directories = [item for item in root.rglob("*") if item.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
