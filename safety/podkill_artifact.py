import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gitops import ManifestTarget
from safety.bundle import LoadedChangeBundle, load_change_bundle
from safety.performance_pair_artifact import (
    LoadedChangePerformancePair,
    load_change_performance_pair,
)
from safety.podkill_runner import PodKillExperimentResult


class PodKillArtifactError(RuntimeError):
    """Raised when fault evidence or its prerequisites are unsafe."""


class PodKillFileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class PodKillIndex(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^podkill-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    performance_pair_id: str = Field(
        pattern=r"^change-performance-pair-[0-9a-f]{32}$"
    )
    status: Literal["pass", "fail"]
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, PodKillFileMetadata]


class PodKillArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)
    artifact_id: str = Field(pattern=r"^podkill-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    performance_pair_id: str = Field(
        pattern=r"^change-performance-pair-[0-9a-f]{32}$"
    )
    status: Literal["pass", "fail"]
    path: Path
    reused: bool


class LoadedPodKillArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)
    artifact_id: str = Field(pattern=r"^podkill-[0-9a-f]{32}$")
    path: Path
    result: PodKillExperimentResult
    change: LoadedChangeBundle
    performance_pair: LoadedChangePerformancePair
    report_path: Path


def validate_podkill_prerequisites(
    change_path: Path,
    pair_path: Path,
    target: ManifestTarget,
) -> tuple[LoadedChangeBundle, LoadedChangePerformancePair]:
    try:
        change = load_change_bundle(change_path)
        pair = load_change_performance_pair(pair_path)
    except RuntimeError as exc:
        raise PodKillArtifactError("PodKill prerequisites are invalid") from exc
    if pair.assessment.status != "pass":
        raise PodKillArtifactError("PodKill requires a passing performance Pair")
    if pair.change_id != change.artifact_id:
        raise PodKillArtifactError("change and performance Pair identities do not match")
    expected = pair.before_after.run.target
    if pair.after_before.run.target != expected or target != expected:
        raise PodKillArtifactError("PodKill target does not match performance Pair target")
    if (
        target.namespace != change.change.namespace
        or target.deployment != change.change.deployment
    ):
        raise PodKillArtifactError("PodKill target does not match change bundle")
    return change, pair


def write_podkill_artifact(
    output_root: Path,
    change_path: Path,
    pair_path: Path,
    result: PodKillExperimentResult,
) -> PodKillArtifact:
    change, pair = validate_podkill_prerequisites(
        change_path, pair_path, result.preflight.target
    )
    payloads = {
        "result.json": _canonical(result.model_dump(mode="json")),
        "report.md": _report(result, change.artifact_id, pair.artifact_id).encode(),
    }
    _embed(payloads, "inputs/change", change.path)
    _embed(payloads, "inputs/performance-pair", pair.path)
    digest = _digest(payloads)
    index = PodKillIndex(
        artifact_id=f"podkill-{digest[:32]}",
        change_id=change.artifact_id,
        performance_pair_id=pair.artifact_id,
        status=result.status,
        content_digest_sha256=digest,
        files={
            name: PodKillFileMetadata(
                sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)
            )
            for name, content in sorted(payloads.items())
        },
    )
    payloads["podkill.json"] = _canonical(index.model_dump(mode="json"))
    if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
        raise PodKillArtifactError("PodKill output root must be a directory")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = output_root / index.artifact_id
    lock = output_root / ".publish.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PodKillArtifactError("another PodKill publication is active") from exc
    staging: Path | None = None
    try:
        if os.path.lexists(final):
            loaded = load_podkill_artifact(final)
            if loaded.result != result:
                raise PodKillArtifactError("existing PodKill artifact conflicts")
            return _artifact(index, final, True)
        staging = Path(tempfile.mkdtemp(prefix=f".{index.artifact_id}-", dir=output_root))
        staging.chmod(0o700)
        for name, content in sorted(payloads.items()):
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with destination.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            destination.chmod(0o600)
        _fsync_tree(staging)
        os.rename(staging, final)
        staging = None
        _fsync(final.parent)
        loaded = load_podkill_artifact(final)
        if loaded.result != result:
            raise PodKillArtifactError("published PodKill result does not replay")
        return _artifact(index, final, False)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(descriptor)
        lock.unlink(missing_ok=True)
        _fsync(output_root)


def load_podkill_artifact(path: Path) -> LoadedPodKillArtifact:
    if path.is_symlink() or not path.is_dir():
        raise PodKillArtifactError("PodKill artifact must be a safe directory")
    index_path = path / "podkill.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise PodKillArtifactError("PodKill index is unsafe or missing")
    try:
        index_bytes = index_path.read_bytes()
        raw = json.loads(index_bytes)
        index = PodKillIndex.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PodKillArtifactError("PodKill index is invalid") from exc
    if index_bytes != _canonical(raw):
        raise PodKillArtifactError("PodKill index is non-canonical")
    if path.name != index.artifact_id:
        raise PodKillArtifactError("PodKill directory does not match artifact ID")
    actual = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise PodKillArtifactError("PodKill artifact contains a symlink")
        if item.is_file():
            actual.append(item.relative_to(path).as_posix())
    if set(actual) != set(index.files) | {"podkill.json"}:
        raise PodKillArtifactError("PodKill artifact file set is invalid")
    payloads = {}
    for name, metadata in index.files.items():
        content = (path / name).read_bytes()
        if len(content) != metadata.size_bytes:
            raise PodKillArtifactError(f"PodKill payload size changed: {name}")
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise PodKillArtifactError(f"PodKill payload digest changed: {name}")
        payloads[name] = content
    if _digest(payloads) != index.content_digest_sha256:
        raise PodKillArtifactError("PodKill aggregate digest changed")
    try:
        result = PodKillExperimentResult.model_validate_json(payloads["result.json"])
        change = load_change_bundle(path / "inputs/change" / index.change_id)
        pair = load_change_performance_pair(
            path / "inputs/performance-pair" / index.performance_pair_id
        )
        validate_podkill_prerequisites(change.path, pair.path, result.preflight.target)
    except (ValueError, RuntimeError) as exc:
        raise PodKillArtifactError("embedded PodKill evidence is invalid") from exc
    if index.status != result.status or payloads["result.json"] != _canonical(
        result.model_dump(mode="json")
    ):
        raise PodKillArtifactError("PodKill result conflicts with index")
    if payloads["report.md"] != _report(result, change.artifact_id, pair.artifact_id).encode():
        raise PodKillArtifactError("PodKill report does not replay")
    return LoadedPodKillArtifact(
        artifact_id=index.artifact_id,
        path=path,
        result=result,
        change=change,
        performance_pair=pair,
        report_path=path / "report.md",
    )


def _embed(payloads: dict[str, bytes], prefix: str, source: Path) -> None:
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise PodKillArtifactError("PodKill prerequisite contains a symlink")
        if item.is_file():
            relative = item.relative_to(source).as_posix()
            payloads[f"{prefix}/{source.name}/{relative}"] = item.read_bytes()


def _report(result: PodKillExperimentResult, change_id: str, pair_id: str) -> str:
    replacement_uid = (
        result.replacement.pod_uid if result.replacement else "not observed"
    )
    return "\n".join(
        [
            "# Controlled PodKill result",
            "",
            f"- Change: `{change_id}`",
            f"- Performance Pair: `{pair_id}`",
            f"- Verdict: **{result.status.upper()}**",
            f"- Deleted Pod UID: `{result.deleted.pod_uid}`",
            f"- Replacement Pod UID: `{replacement_uid}`",
            f"- HTTP recovery seconds: `{result.service_recovery_seconds}`",
            f"- Replacement ready seconds: `{result.replacement_ready_seconds}`",
            "",
        ]
    )


def _artifact(index: PodKillIndex, path: Path, reused: bool) -> PodKillArtifact:
    return PodKillArtifact(
        artifact_id=index.artifact_id,
        change_id=index.change_id,
        performance_pair_id=index.performance_pair_id,
        status=index.status,
        path=path,
        reused=reused,
    )


def _canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(payloads.items()):
        digest.update(name.encode() + b"\0" + str(len(content)).encode() + b"\0" + content)
    return digest.hexdigest()


def _fsync_tree(root: Path) -> None:
    for directory in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        _fsync(directory)
    _fsync(root)


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
