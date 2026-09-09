import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from safety.podkill_artifact import LoadedPodKillArtifact, load_podkill_artifact
from safety.podkill_campaign import PodKillCampaignPlan, load_podkill_campaign_plan
from safety.podkill_campaign_assessment import (
    PodKillCampaignAssessment,
    assess_podkill_campaign,
)


class PodKillCampaignEvidenceError(RuntimeError):
    """Raised when completed PodKill campaign evidence is unsafe."""


class PodKillCampaignEvidenceFile(BaseModel):
    model_config = ConfigDict(frozen=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class PodKillCampaignEvidenceIndex(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^podkill-campaign-evidence-[0-9a-f]{32}$")
    campaign_id: str = Field(pattern=r"^podkill-campaign-[0-9a-f]{32}$")
    status: Literal["pass", "fail"]
    trial_ids: list[
        Annotated[str, Field(pattern=r"^podkill-[0-9a-f]{32}$")]
    ] = Field(min_length=3, max_length=100)
    content_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, PodKillCampaignEvidenceFile]


class PodKillCampaignEvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)
    artifact_id: str = Field(pattern=r"^podkill-campaign-evidence-[0-9a-f]{32}$")
    campaign_id: str = Field(pattern=r"^podkill-campaign-[0-9a-f]{32}$")
    status: Literal["pass", "fail"]
    path: Path
    reused: bool


class LoadedPodKillCampaignEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)
    artifact_id: str = Field(pattern=r"^podkill-campaign-evidence-[0-9a-f]{32}$")
    path: Path
    plan: PodKillCampaignPlan
    assessment: PodKillCampaignAssessment
    trials: list[LoadedPodKillArtifact]
    report_path: Path


def write_podkill_campaign_evidence(
    output_root: Path, plan_path: Path, trial_paths: list[Path]
) -> PodKillCampaignEvidenceArtifact:
    assessment = assess_podkill_campaign(plan_path, trial_paths)
    if assessment.status not in {"pass", "fail"}:
        raise PodKillCampaignEvidenceError(
            f"only a complete valid campaign can be persisted, got {assessment.status}"
        )
    plan = load_podkill_campaign_plan(plan_path)
    trials_by_id = {
        trial.artifact_id: trial
        for trial in (load_podkill_artifact(path) for path in trial_paths)
    }
    trials = [trials_by_id[trial_id] for trial_id in assessment.trial_ids]
    payloads = {
        "assessment.json": _canonical(assessment.model_dump(mode="json")),
    }
    _embed(payloads, "campaign", plan_path)
    for trial in trials:
        _embed(payloads, "trials", trial.path)
    identity_digest = _digest(payloads)
    artifact_id = f"podkill-campaign-evidence-{identity_digest[:32]}"
    payloads["report.md"] = _report(artifact_id, assessment).encode()
    content_digest = _digest(payloads)
    index = PodKillCampaignEvidenceIndex(
        artifact_id=artifact_id,
        campaign_id=plan.campaign_id,
        status=assessment.status,
        trial_ids=assessment.trial_ids,
        content_digest_sha256=content_digest,
        files={
            name: PodKillCampaignEvidenceFile(
                sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content)
            )
            for name, content in sorted(payloads.items())
        },
    )
    payloads["evidence.json"] = _canonical(index.model_dump(mode="json"))
    if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
        raise PodKillCampaignEvidenceError("campaign evidence root must be a directory")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = output_root / artifact_id
    lock = output_root / ".publish.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PodKillCampaignEvidenceError("another evidence publication is active") from exc
    staging: Path | None = None
    try:
        if os.path.lexists(final):
            loaded = load_podkill_campaign_evidence(final)
            if loaded.assessment != assessment:
                raise PodKillCampaignEvidenceError("existing campaign evidence conflicts")
            return _artifact(index, final, True)
        staging = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=output_root))
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
        if load_podkill_campaign_evidence(final).assessment != assessment:
            raise PodKillCampaignEvidenceError("published campaign evidence does not replay")
        return _artifact(index, final, False)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(descriptor)
        lock.unlink(missing_ok=True)
        _fsync(output_root)


def load_podkill_campaign_evidence(path: Path) -> LoadedPodKillCampaignEvidence:
    if path.is_symlink() or not path.is_dir():
        raise PodKillCampaignEvidenceError("campaign evidence must be a safe directory")
    index_path = path / "evidence.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise PodKillCampaignEvidenceError("campaign evidence index is unsafe or missing")
    try:
        index_bytes = index_path.read_bytes()
        raw = json.loads(index_bytes)
        index = PodKillCampaignEvidenceIndex.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PodKillCampaignEvidenceError("campaign evidence index is invalid") from exc
    if index_bytes != _canonical(raw):
        raise PodKillCampaignEvidenceError("campaign evidence index is non-canonical")
    if path.name != index.artifact_id:
        raise PodKillCampaignEvidenceError("campaign evidence directory ID is invalid")
    actual = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise PodKillCampaignEvidenceError("campaign evidence contains a symlink")
        if item.is_file():
            actual.append(item.relative_to(path).as_posix())
    if set(actual) != set(index.files) | {"evidence.json"}:
        raise PodKillCampaignEvidenceError("campaign evidence file set is invalid")
    payloads = {}
    for name, metadata in index.files.items():
        content = (path / name).read_bytes()
        if len(content) != metadata.size_bytes:
            raise PodKillCampaignEvidenceError(f"evidence payload size changed: {name}")
        if hashlib.sha256(content).hexdigest() != metadata.sha256:
            raise PodKillCampaignEvidenceError(f"evidence payload digest changed: {name}")
        payloads[name] = content
    if _digest(payloads) != index.content_digest_sha256:
        raise PodKillCampaignEvidenceError("campaign evidence digest changed")
    try:
        plan_path = path / "campaign" / index.campaign_id
        plan = load_podkill_campaign_plan(plan_path)
        trials = [
            load_podkill_artifact(path / "trials" / trial_id)
            for trial_id in index.trial_ids
        ]
        replayed = assess_podkill_campaign(plan_path, [trial.path for trial in trials])
        persisted = PodKillCampaignAssessment.model_validate_json(
            payloads["assessment.json"]
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise PodKillCampaignEvidenceError("embedded campaign evidence is invalid") from exc
    if replayed != persisted or replayed.status != index.status:
        raise PodKillCampaignEvidenceError("campaign assessment does not replay")
    identity_payloads = {
        name: content for name, content in payloads.items() if name != "report.md"
    }
    expected_id = f"podkill-campaign-evidence-{_digest(identity_payloads)[:32]}"
    if expected_id != index.artifact_id:
        raise PodKillCampaignEvidenceError("campaign evidence identity does not replay")
    if payloads["report.md"] != _report(index.artifact_id, replayed).encode():
        raise PodKillCampaignEvidenceError("campaign evidence report does not replay")
    return LoadedPodKillCampaignEvidence(
        artifact_id=index.artifact_id,
        path=path,
        plan=plan,
        assessment=replayed,
        trials=trials,
        report_path=path / "report.md",
    )


def _embed(payloads: dict[str, bytes], prefix: str, source: Path) -> None:
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise PodKillCampaignEvidenceError("campaign input contains a symlink")
        if item.is_file():
            relative = item.relative_to(source).as_posix()
            payloads[f"{prefix}/{source.name}/{relative}"] = item.read_bytes()


def _report(artifact_id: str, assessment: PodKillCampaignAssessment) -> str:
    return "\n".join(
        [
            "# Completed PodKill campaign evidence",
            "",
            f"- Evidence: `{artifact_id}`",
            f"- Campaign: `{assessment.campaign_id}`",
            f"- Verdict: **{assessment.status.upper()}**",
            f"- Trials: `{assessment.completed_trials}/{assessment.planned_trials}`",
            f"- Failed trials: `{assessment.failed_trials}`",
            (
                "- HTTP recovery P50/P95: "
                f"`{assessment.service_recovery_p50_seconds}` / "
                f"`{assessment.service_recovery_p95_seconds}` seconds"
            ),
            (
                "- Replacement readiness P50/P95: "
                f"`{assessment.replacement_ready_p50_seconds}` / "
                f"`{assessment.replacement_ready_p95_seconds}` seconds"
            ),
            "",
        ]
    )


def _artifact(index, path, reused) -> PodKillCampaignEvidenceArtifact:
    return PodKillCampaignEvidenceArtifact(
        artifact_id=index.artifact_id,
        campaign_id=index.campaign_id,
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
    directories = [item for item in root.rglob("*") if item.is_dir()]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync(directory)
    _fsync(root)


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
