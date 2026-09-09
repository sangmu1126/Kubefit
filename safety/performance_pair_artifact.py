import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from safety.performance_artifact import (
    LoadedChangePerformanceArtifact,
    load_change_performance_artifact,
)
from safety.performance_pair import (
    ChangePerformancePairAssessment,
    assess_change_performance_pair,
)


class ChangePerformancePairArtifactError(RuntimeError):
    """Raised when a generic counterbalanced Pair cannot be safely persisted."""


class ChangePerformancePairFileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ChangePerformancePairIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^change-performance-pair-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    status: Literal["pass", "fail"]
    trial_ids: list[str] = Field(min_length=2, max_length=2)
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, ChangePerformancePairFileMetadata]


class ChangePerformancePairArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^change-performance-pair-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    status: Literal["pass", "fail"]
    trial_ids: list[str] = Field(min_length=2, max_length=2)
    path: Path
    reused: bool
    files: list[str]


class LoadedChangePerformancePair(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^change-performance-pair-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    path: Path
    assessment: ChangePerformancePairAssessment
    before_after: LoadedChangePerformanceArtifact
    after_before: LoadedChangePerformanceArtifact
    report_path: Path


def write_change_performance_pair(
    output_root: Path,
    first_path: Path,
    second_path: Path,
) -> ChangePerformancePairArtifact:
    assessment = assess_change_performance_pair(first_path, second_path)
    if assessment.status == "invalid" or assessment.change_id is None:
        raise ChangePerformancePairArtifactError(
            "invalid generic performance inputs cannot be persisted as a Pair"
        )
    trials = [
        load_change_performance_artifact(first_path),
        load_change_performance_artifact(second_path),
    ]
    trials.sort(key=lambda item: item.artifact_id)
    payloads = _payloads(assessment, trials)
    digest = _content_digest(payloads)
    trial_ids = [trial.artifact_id for trial in trials]
    index = ChangePerformancePairIndex(
        artifact_id=assessment.assessment_id,
        change_id=assessment.change_id,
        status=assessment.status,
        trial_ids=trial_ids,
        content_digest_sha256=digest,
        files={
            name: ChangePerformancePairFileMetadata(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            for name, content in sorted(payloads.items())
        },
    )
    payloads["pair.json"] = _canonical_json(index.model_dump(mode="json"))
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final_path = output_root / index.artifact_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            loaded = load_change_performance_pair(final_path)
            if loaded.assessment != assessment:
                raise ChangePerformancePairArtifactError(
                    "existing Pair conflicts with this assessment"
                )
            return _artifact(index, final_path, True)
        staging = Path(tempfile.mkdtemp(prefix=f".{index.artifact_id}-", dir=output_root))
        staging.chmod(0o700)
        for name, content in sorted(payloads.items()):
            _write_file(staging, name, content)
        _fsync_tree(staging)
        os.rename(staging, final_path)
        staging = None
        _fsync_directory(output_root)
        loaded = load_change_performance_pair(final_path)
        if loaded.assessment != assessment:
            raise ChangePerformancePairArtifactError("published Pair did not replay")
        return _artifact(index, final_path, False)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
        _fsync_directory(output_root)


def load_change_performance_pair(path: Path) -> LoadedChangePerformancePair:
    if path.is_symlink() or not path.is_dir():
        raise ChangePerformancePairArtifactError("Pair path must be a safe directory")
    index_path = path / "pair.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise ChangePerformancePairArtifactError("Pair is missing pair.json")
    try:
        index_bytes = index_path.read_bytes()
        raw_index = json.loads(index_bytes)
        index = ChangePerformancePairIndex.model_validate(raw_index)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ChangePerformancePairArtifactError("Pair index is invalid") from exc
    if index_bytes != _canonical_json(raw_index):
        raise ChangePerformancePairArtifactError("Pair index is not canonical")
    if path.name != index.artifact_id:
        raise ChangePerformancePairArtifactError("Pair directory does not match artifact ID")
    actual_files = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ChangePerformancePairArtifactError("Pair contains a symlink")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if set(actual_files) != set(index.files) | {"pair.json"}:
        raise ChangePerformancePairArtifactError("Pair file set is invalid")
    payloads: dict[str, bytes] = {}
    for name, metadata in index.files.items():
        content = (path / name).read_bytes()
        if len(content) != metadata.size_bytes:
            raise ChangePerformancePairArtifactError(f"Pair payload size changed: {name}")
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise ChangePerformancePairArtifactError(f"Pair payload digest changed: {name}")
        payloads[name] = content
    if _content_digest(payloads) != index.content_digest_sha256:
        raise ChangePerformancePairArtifactError("Pair content digest is invalid")
    try:
        trials = [
            load_change_performance_artifact(path / "trials" / trial_id)
            for trial_id in index.trial_ids
        ]
        assessment = assess_change_performance_pair(trials[0].path, trials[1].path)
        persisted = ChangePerformancePairAssessment.model_validate_json(
            payloads["assessment.json"]
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise ChangePerformancePairArtifactError("embedded Pair evidence is invalid") from exc
    if assessment != persisted or assessment.assessment_id != index.artifact_id:
        raise ChangePerformancePairArtifactError("Pair assessment does not replay")
    if assessment.change_id != index.change_id or assessment.status != index.status:
        raise ChangePerformancePairArtifactError("Pair index conflicts with assessment")
    if payloads["assessment.json"] != _canonical_json(persisted.model_dump(mode="json")):
        raise ChangePerformancePairArtifactError("Pair assessment is not canonical")
    if payloads["report.md"] != _render_report(assessment).encode():
        raise ChangePerformancePairArtifactError("Pair report does not replay")
    by_order = {trial.run.execution_order: trial for trial in trials}
    return LoadedChangePerformancePair(
        artifact_id=index.artifact_id,
        change_id=index.change_id,
        path=path,
        assessment=assessment,
        before_after=by_order["before-after"],
        after_before=by_order["after-before"],
        report_path=path / "report.md",
    )


def _payloads(
    assessment: ChangePerformancePairAssessment,
    trials: list[LoadedChangePerformanceArtifact],
) -> dict[str, bytes]:
    payloads = {
        "assessment.json": _canonical_json(assessment.model_dump(mode="json")),
        "report.md": _render_report(assessment).encode(),
    }
    for trial in trials:
        for item in sorted(trial.path.rglob("*")):
            if item.is_file():
                relative = item.relative_to(trial.path).as_posix()
                payloads[f"trials/{trial.artifact_id}/{relative}"] = item.read_bytes()
    return payloads


def _render_report(assessment: ChangePerformancePairAssessment) -> str:
    lines = [
        "# Generic change counterbalanced performance Pair",
        "",
        f"- Pair: `{assessment.assessment_id}`",
        f"- Change: `{assessment.change_id}`",
        f"- Verdict: **{assessment.status.upper()}**",
        "",
        "| Trial | Order | Verdict |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| `{trial.artifact_id}` | `{trial.execution_order}` | {trial.verdict_status} |"
        for trial in assessment.trials
    )
    lines.extend(["", "## Checks", "", "| Code | Status | Reason |", "|---|---|---|"])
    lines.extend(
        f"| `{check.code}` | {check.status} | {check.reason.replace('|', '/')} |"
        for check in assessment.checks
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {warning}" for warning in assessment.warnings)
    return "\n".join(lines) + "\n"


def _artifact(
    index: ChangePerformancePairIndex,
    path: Path,
    reused: bool,
) -> ChangePerformancePairArtifact:
    return ChangePerformancePairArtifact(
        artifact_id=index.artifact_id,
        change_id=index.change_id,
        status=index.status,
        trial_ids=index.trial_ids,
        path=path,
        reused=reused,
        files=sorted(set(index.files) | {"pair.json"}),
    )


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


def _validate_output_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ChangePerformancePairArtifactError("Pair output root must be a directory")


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ChangePerformancePairArtifactError("another Pair publication is active") from exc


def _write_file(root: Path, name: str, content: bytes) -> None:
    destination = root / name
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with destination.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    destination.chmod(0o600)


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
