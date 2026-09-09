import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gitops import ManifestTarget
from safety.podkill_artifact import validate_podkill_prerequisites


class PodKillCampaignError(RuntimeError):
    """Raised when a PodKill campaign plan is unsafe or inconsistent."""


class PodKillCampaignPlan(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    campaign_id: str = Field(pattern=r"^podkill-campaign-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    performance_pair_id: str = Field(
        pattern=r"^change-performance-pair-[0-9a-f]{32}$"
    )
    context: str = Field(pattern=r"^kind-[A-Za-z0-9._-]+$")
    target: ManifestTarget
    planned_trials: int = Field(ge=3, le=100)
    allowed_failed_trials: int = Field(ge=0)
    service_recovery_limit_seconds: float = Field(gt=0)
    replacement_ready_limit_seconds: float = Field(gt=0)
    stopping_rule: Literal["complete_all_planned_trials"] = (
        "complete_all_planned_trials"
    )
    limitations: list[str]

    @model_validator(mode="after")
    def failure_budget_is_bounded(self) -> "PodKillCampaignPlan":
        if self.allowed_failed_trials >= self.planned_trials:
            raise ValueError("allowed failed trials must be less than planned trials")
        return self


class PodKillCampaignArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    campaign_id: str = Field(pattern=r"^podkill-campaign-[0-9a-f]{32}$")
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    performance_pair_id: str = Field(
        pattern=r"^change-performance-pair-[0-9a-f]{32}$"
    )
    path: Path
    reused: bool


_LIMITATIONS = [
    (
        "a small repeated campaign describes only the tested disposable cluster and "
        "does not establish production reliability"
    ),
    (
        "trial timing and host conditions are not randomized, so recovery variation "
        "can include uncontrolled environmental effects"
    ),
    (
        "the fixed stopping rule prevents outcome-based early stopping but does not "
        "by itself establish statistical significance"
    ),
]


def create_podkill_campaign_plan(
    change_id: str,
    performance_pair_id: str,
    context: str,
    target: ManifestTarget,
    planned_trials: int,
    allowed_failed_trials: int,
    service_recovery_limit_seconds: float,
    replacement_ready_limit_seconds: float,
) -> PodKillCampaignPlan:
    identity = {
        "schema_version": 1,
        "change_id": change_id,
        "performance_pair_id": performance_pair_id,
        "context": context,
        "target": target.model_dump(mode="json"),
        "planned_trials": planned_trials,
        "allowed_failed_trials": allowed_failed_trials,
        "service_recovery_limit_seconds": service_recovery_limit_seconds,
        "replacement_ready_limit_seconds": replacement_ready_limit_seconds,
        "stopping_rule": "complete_all_planned_trials",
        "limitations": _LIMITATIONS,
    }
    try:
        provisional = PodKillCampaignPlan(
            campaign_id="podkill-campaign-" + "0" * 32,
            **identity,
        )
    except ValueError as exc:
        raise PodKillCampaignError("PodKill campaign policy is invalid") from exc
    digest = hashlib.sha256(
        _canonical(provisional.model_dump(mode="json", exclude={"campaign_id"}))
    ).hexdigest()
    return provisional.model_copy(
        update={"campaign_id": f"podkill-campaign-{digest[:32]}"}
    )


def write_podkill_campaign_plan(
    output_root: Path,
    change_path: Path,
    pair_path: Path,
    context: str,
    planned_trials: int,
    allowed_failed_trials: int,
    service_recovery_limit_seconds: float,
    replacement_ready_limit_seconds: float,
) -> PodKillCampaignArtifact:
    try:
        change, pair = validate_podkill_prerequisites(
            change_path, pair_path, pair_target(pair_path)
        )
    except RuntimeError as exc:
        raise PodKillCampaignError("PodKill campaign prerequisites are invalid") from exc
    plan = create_podkill_campaign_plan(
        change.artifact_id,
        pair.artifact_id,
        context,
        pair.before_after.run.target,
        planned_trials,
        allowed_failed_trials,
        service_recovery_limit_seconds,
        replacement_ready_limit_seconds,
    )
    payloads = {
        "campaign.json": _canonical(plan.model_dump(mode="json")),
        "report.md": _report(plan).encode(),
    }
    if output_root.is_symlink() or (output_root.exists() and not output_root.is_dir()):
        raise PodKillCampaignError("PodKill campaign output root must be a directory")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = output_root / plan.campaign_id
    lock = output_root / ".publish.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PodKillCampaignError("another campaign publication is active") from exc
    staging: Path | None = None
    try:
        if os.path.lexists(final):
            loaded = load_podkill_campaign_plan(final)
            if loaded != plan:
                raise PodKillCampaignError("existing PodKill campaign conflicts")
            return _artifact(plan, final, True)
        staging = Path(tempfile.mkdtemp(prefix=f".{plan.campaign_id}-", dir=output_root))
        staging.chmod(0o700)
        for name, content in payloads.items():
            with (staging / name).open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            (staging / name).chmod(0o600)
        _fsync(staging)
        os.rename(staging, final)
        staging = None
        _fsync(output_root)
        if load_podkill_campaign_plan(final) != plan:
            raise PodKillCampaignError("published PodKill campaign does not replay")
        return _artifact(plan, final, False)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        os.close(descriptor)
        lock.unlink(missing_ok=True)
        _fsync(output_root)


def pair_target(pair_path: Path) -> ManifestTarget:
    from safety.performance_pair_artifact import load_change_performance_pair

    try:
        return load_change_performance_pair(pair_path).before_after.run.target
    except RuntimeError as exc:
        raise PodKillCampaignError("performance Pair is invalid") from exc


def load_podkill_campaign_plan(path: Path) -> PodKillCampaignPlan:
    if path.is_symlink() or not path.is_dir():
        raise PodKillCampaignError("PodKill campaign must be a safe directory")
    children = list(path.iterdir())
    if sorted(item.name for item in children) != ["campaign.json", "report.md"] or any(
        item.is_symlink() or not item.is_file() for item in children
    ):
        raise PodKillCampaignError("PodKill campaign file set is invalid")
    try:
        content = (path / "campaign.json").read_bytes()
        raw = json.loads(content)
        plan = PodKillCampaignPlan.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PodKillCampaignError("PodKill campaign plan is invalid") from exc
    if content != _canonical(raw):
        raise PodKillCampaignError("PodKill campaign plan is non-canonical")
    if path.name != plan.campaign_id:
        raise PodKillCampaignError("PodKill campaign directory does not match its ID")
    identity = plan.model_dump(mode="json", exclude={"campaign_id"})
    digest = hashlib.sha256(_canonical(identity)).hexdigest()
    if plan.campaign_id != f"podkill-campaign-{digest[:32]}":
        raise PodKillCampaignError("PodKill campaign identity does not replay")
    if (path / "report.md").read_text() != _report(plan):
        raise PodKillCampaignError("PodKill campaign report does not replay")
    return plan


def _report(plan: PodKillCampaignPlan) -> str:
    lines = [
        "# Preregistered PodKill campaign",
        "",
        f"- Campaign: `{plan.campaign_id}`",
        f"- Change: `{plan.change_id}`",
        f"- Performance Pair: `{plan.performance_pair_id}`",
        f"- Context: `{plan.context}`",
        f"- Target: `{plan.target.namespace}/{plan.target.deployment}:{plan.target.container}`",
        f"- Planned trials: `{plan.planned_trials}`",
        f"- Allowed failed trials: `{plan.allowed_failed_trials}`",
        f"- HTTP recovery limit: `{plan.service_recovery_limit_seconds}` seconds",
        f"- Replacement readiness limit: `{plan.replacement_ready_limit_seconds}` seconds",
        f"- Stopping rule: `{plan.stopping_rule}`",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in plan.limitations)
    return "\n".join(lines) + "\n"


def _artifact(
    plan: PodKillCampaignPlan, path: Path, reused: bool
) -> PodKillCampaignArtifact:
    return PodKillCampaignArtifact(
        campaign_id=plan.campaign_id,
        change_id=plan.change_id,
        performance_pair_id=plan.performance_pair_id,
        path=path,
        reused=reused,
    )


def _canonical(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
