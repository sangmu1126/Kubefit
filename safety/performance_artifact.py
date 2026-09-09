import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gitops import ManifestTarget
from safety.load import ChangeK6RunSummary, ChangeTimedLoadResult
from safety.performance import (
    ChangePerformancePolicy,
    ChangePerformanceRun,
    ChangePerformanceVerdict,
    change_measurement_order,
)


class ChangePerformanceArtifactError(RuntimeError):
    """Raised when generic performance evidence cannot be safely persisted."""


class ChangePerformanceFileMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ChangePerformanceIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^change-performance-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, ChangePerformanceFileMetadata]


class ChangeLoadRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: ChangeK6RunSummary
    started_at: datetime
    finished_at: datetime
    traffic_spike_recovery_seconds: float = Field(ge=0)
    traffic_spike_recovered: bool
    summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> "ChangeLoadRecord":
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("change load record timestamps must include timezone")
        if self.finished_at <= self.started_at:
            raise ValueError("change load record finish must be later than start")
        return self


class ChangePerformanceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^change-performance-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    status: Literal["pass", "fail", "invalid"]
    path: Path
    reused: bool
    files: list[str]


class LoadedChangePerformanceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(pattern=r"^change-performance-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    path: Path
    run: ChangePerformanceRun
    report_path: Path


PAYLOAD_PATHS = frozenset(
    {
        "target.json",
        "policy.json",
        "measurements/before.json",
        "measurements/after.json",
        "evidence/k6/before-summary.json",
        "evidence/k6/before-raw.json",
        "evidence/k6/after-summary.json",
        "evidence/k6/after-raw.json",
        "verdict.json",
        "report.md",
    }
)


def write_change_performance_artifact(
    output_root: Path,
    run: ChangePerformanceRun,
) -> ChangePerformanceArtifact:
    payloads = _payloads(run)
    digest = _content_digest(payloads)
    artifact_id = f"change-performance-{digest[:32]}"
    index = ChangePerformanceIndex(
        artifact_id=artifact_id,
        change_id=run.change_id,
        content_digest_sha256=digest,
        files={
            name: ChangePerformanceFileMetadata(
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            for name, content in sorted(payloads.items())
        },
    )
    payloads["result.json"] = _canonical_json(index.model_dump(mode="json"))
    _validate_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final_path = output_root / artifact_id
    lock_path = output_root / ".publish.lock"
    lock_fd = _acquire_lock(lock_path)
    staging: Path | None = None
    try:
        if os.path.lexists(final_path):
            loaded = load_change_performance_artifact(final_path)
            if loaded.run != run:
                raise ChangePerformanceArtifactError(
                    "existing performance artifact conflicts with this run"
                )
            return _artifact(index, final_path, run.verdict.status, True)
        staging = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=output_root))
        staging.chmod(0o700)
        for name, content in sorted(payloads.items()):
            _write_file(staging, name, content)
        _fsync_tree(staging)
        os.rename(staging, final_path)
        staging = None
        _fsync_directory(output_root)
        loaded = load_change_performance_artifact(final_path)
        if loaded.run != run:
            raise ChangePerformanceArtifactError("published performance run did not replay")
        return _artifact(index, final_path, run.verdict.status, False)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
        _fsync_directory(output_root)


def load_change_performance_artifact(path: Path) -> LoadedChangePerformanceArtifact:
    if path.is_symlink() or not path.is_dir():
        raise ChangePerformanceArtifactError("performance artifact must be a safe directory")
    index_path = path / "result.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise ChangePerformanceArtifactError("performance artifact is missing result.json")
    try:
        index_bytes = index_path.read_bytes()
        raw_index = json.loads(index_bytes)
        index = ChangePerformanceIndex.model_validate(raw_index)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ChangePerformanceArtifactError("performance artifact index is invalid") from exc
    if index_bytes != _canonical_json(raw_index):
        raise ChangePerformanceArtifactError("performance artifact index is not canonical")
    if path.name != index.artifact_id:
        raise ChangePerformanceArtifactError("performance directory does not match artifact ID")
    if index.content_digest_sha256[:32] != index.artifact_id.removeprefix(
        "change-performance-"
    ):
        raise ChangePerformanceArtifactError("performance artifact ID does not match digest")
    if set(index.files) != PAYLOAD_PATHS:
        raise ChangePerformanceArtifactError("performance artifact payload set is invalid")
    actual_files = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ChangePerformanceArtifactError("performance artifact contains a symlink")
        if item.is_file():
            actual_files.append(item.relative_to(path).as_posix())
    if set(actual_files) != PAYLOAD_PATHS | {"result.json"}:
        raise ChangePerformanceArtifactError("performance artifact file set is invalid")
    payloads: dict[str, bytes] = {}
    for name, metadata in index.files.items():
        content = (path / name).read_bytes()
        if len(content) != metadata.size_bytes:
            raise ChangePerformanceArtifactError(f"performance payload size changed: {name}")
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise ChangePerformanceArtifactError(f"performance payload digest changed: {name}")
        payloads[name] = content
    if _content_digest(payloads) != index.content_digest_sha256:
        raise ChangePerformanceArtifactError("performance content digest is invalid")
    try:
        target = ManifestTarget.model_validate_json(payloads["target.json"])
        policy = ChangePerformancePolicy.model_validate_json(payloads["policy.json"])
        verdict = ChangePerformanceVerdict.model_validate_json(payloads["verdict.json"])
        before = _restore_load("before", payloads)
        after = _restore_load("after", payloads)
        execution_order = change_measurement_order(before, after)
        if execution_order is None:
            raise ValueError("saved measurement intervals overlap")
        run = ChangePerformanceRun(
            change_id=index.change_id,
            target=target,
            execution_order=execution_order,
            before=before,
            after=after,
            policy=policy,
            verdict=verdict,
        )
    except ValueError as exc:
        raise ChangePerformanceArtifactError(
            "performance evidence or verdict does not replay"
        ) from exc
    for name in (
        "target.json",
        "policy.json",
        "measurements/before.json",
        "measurements/after.json",
        "verdict.json",
    ):
        try:
            raw = json.loads(payloads[name])
        except json.JSONDecodeError as exc:
            raise ChangePerformanceArtifactError(f"performance JSON is invalid: {name}") from exc
        if payloads[name] != _canonical_json(raw):
            raise ChangePerformanceArtifactError(f"performance JSON is not canonical: {name}")
    if payloads["report.md"] != _render_report(run).encode():
        raise ChangePerformanceArtifactError("performance report does not replay")
    return LoadedChangePerformanceArtifact(
        artifact_id=index.artifact_id,
        change_id=index.change_id,
        path=path,
        run=run,
        report_path=path / "report.md",
    )


def _record(load: ChangeTimedLoadResult) -> ChangeLoadRecord:
    return ChangeLoadRecord(
        summary=load.summary,
        started_at=load.started_at,
        finished_at=load.finished_at,
        traffic_spike_recovery_seconds=load.traffic_spike_recovery_seconds,
        traffic_spike_recovered=load.traffic_spike_recovered,
        summary_sha256=load.summary_sha256,
        raw_sha256=load.raw_sha256,
    )


def _restore_load(variant: Literal["before", "after"], payloads: dict[str, bytes]):
    record = ChangeLoadRecord.model_validate_json(payloads[f"measurements/{variant}.json"])
    summary_content = payloads[f"evidence/k6/{variant}-summary.json"]
    raw_content = payloads[f"evidence/k6/{variant}-raw.json"]
    if hashlib.sha256(summary_content).hexdigest() != record.summary_sha256:
        raise ValueError("saved k6 summary does not match its measurement record")
    if hashlib.sha256(raw_content).hexdigest() != record.raw_sha256:
        raise ValueError("saved k6 raw output does not match its measurement record")
    return ChangeTimedLoadResult(
        summary=record.summary,
        started_at=record.started_at,
        finished_at=record.finished_at,
        traffic_spike_recovery_seconds=record.traffic_spike_recovery_seconds,
        traffic_spike_recovered=record.traffic_spike_recovered,
        summary_content=summary_content,
        raw_content=raw_content,
    )


def _payloads(run: ChangePerformanceRun) -> dict[str, bytes]:
    return {
        "target.json": _canonical_json(run.target.model_dump(mode="json")),
        "policy.json": _canonical_json(run.policy.model_dump(mode="json")),
        "measurements/before.json": _canonical_json(
            _record(run.before).model_dump(mode="json")
        ),
        "measurements/after.json": _canonical_json(
            _record(run.after).model_dump(mode="json")
        ),
        "evidence/k6/before-summary.json": run.before.summary_content,
        "evidence/k6/before-raw.json": run.before.raw_content,
        "evidence/k6/after-summary.json": run.after.summary_content,
        "evidence/k6/after-raw.json": run.after.raw_content,
        "verdict.json": _canonical_json(run.verdict.model_dump(mode="json")),
        "report.md": _render_report(run).encode(),
    }


def _render_report(run: ChangePerformanceRun) -> str:
    before = run.before
    after = run.after
    return "\n".join(
        [
            "# Generic change performance result",
            "",
            f"- Change: `{run.change_id}`",
            f"- Target: `{run.target.namespace}/{run.target.deployment}:{run.target.container}`",
            f"- Verdict: **{run.verdict.status.upper()}**",
            "- Base restored: **yes**",
            "",
            "| Metric | Base | Candidate |",
            "|---|---:|---:|",
            _report_row(
                "Steady P95 ms",
                before.summary.steady.latency_p95_ms,
                after.summary.steady.latency_p95_ms,
            ),
            _report_row(
                "Steady P99 ms",
                before.summary.steady.latency_p99_ms,
                after.summary.steady.latency_p99_ms,
            ),
            _report_row(
                "Spike P95 ms",
                before.summary.spike.latency_p95_ms,
                after.summary.spike.latency_p95_ms,
            ),
            _report_row(
                "Spike P99 ms",
                before.summary.spike.latency_p99_ms,
                after.summary.spike.latency_p99_ms,
            ),
            _report_row(
                "Recovery seconds",
                before.traffic_spike_recovery_seconds,
                after.traffic_spike_recovery_seconds,
            ),
            "",
            "This verdict excludes cost, Prometheus throttling, OOM, and fault injection.",
            "",
        ]
    )


def _report_row(label: str, before: object, after: object) -> str:
    return f"| {label} | {before} | {after} |"


def _artifact(
    index: ChangePerformanceIndex,
    path: Path,
    status: Literal["pass", "fail", "invalid"],
    reused: bool,
) -> ChangePerformanceArtifact:
    return ChangePerformanceArtifact(
        artifact_id=index.artifact_id,
        change_id=index.change_id,
        status=status,
        path=path,
        reused=reused,
        files=sorted(PAYLOAD_PATHS | {"result.json"}),
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
    if root.is_symlink():
        raise ChangePerformanceArtifactError("performance output root must not be a symlink")
    if root.exists() and not root.is_dir():
        raise ChangePerformanceArtifactError("performance output root must be a directory")


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ChangePerformanceArtifactError("another performance publication is active") from exc


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
