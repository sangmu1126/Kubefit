import hashlib
import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from benchmarks.measurement import recovery_from_k6_raw
from benchmarks.result import LoadPhaseMetrics


class ChangeLoadError(RuntimeError):
    """Raised when fixed-load evidence for a generic change is unsafe or invalid."""


class ChangeK6RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    profile_version: str = Field(min_length=1)
    change_id: str = Field(pattern=r"^change-[0-9a-f]{32}$")
    variant: Literal["before", "after"]
    dropped_iterations: int = Field(ge=0)
    steady: LoadPhaseMetrics
    spike: LoadPhaseMetrics
    recovery: LoadPhaseMetrics


class ChangeTimedLoadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: ChangeK6RunSummary
    started_at: datetime
    finished_at: datetime
    traffic_spike_recovery_seconds: float = Field(ge=0)
    traffic_spike_recovered: bool
    summary_content: bytes
    raw_content: bytes

    @model_validator(mode="after")
    def evidence_is_consistent(self) -> "ChangeTimedLoadResult":
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("change load timestamps must include timezone information")
        if self.finished_at <= self.started_at:
            raise ValueError("change load finish must be later than start")
        try:
            persisted = ChangeK6RunSummary.model_validate_json(self.summary_content)
        except ValueError as exc:
            raise ValueError("change load summary content is invalid") from exc
        if persisted != self.summary:
            raise ValueError("change load summary content conflicts with parsed summary")
        return self

    @property
    def summary_sha256(self) -> str:
        return hashlib.sha256(self.summary_content).hexdigest()

    @property
    def raw_sha256(self) -> str:
        return hashlib.sha256(self.raw_content).hexdigest()


ChangeK6CommandRunner = Callable[[Sequence[str], int], str]
ChangeLoadClock = Callable[[], datetime]


class SubprocessChangeK6Executor:
    def __init__(
        self,
        target_url: str,
        script_path: Path,
        runner: ChangeK6CommandRunner | None = None,
        clock: ChangeLoadClock | None = None,
        timeout_seconds: int = 240,
    ) -> None:
        parsed_target = urlsplit(target_url)
        if (
            parsed_target.scheme not in {"http", "https"}
            or not parsed_target.netloc
            or parsed_target.username is not None
            or parsed_target.password is not None
            or parsed_target.query
            or parsed_target.fragment
        ):
            raise ValueError(
                "k6 target URL must be HTTP(S) without credentials, query, or fragment"
            )
        if script_path.is_symlink() or not script_path.is_file():
            raise ValueError("k6 script path must be a regular, non-symlinked file")
        if timeout_seconds < 1:
            raise ValueError("k6 timeout must be at least one second")
        self._target_url = target_url
        self._script_path = script_path
        self._runner = runner or _run_k6
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        change_id: str,
        variant: Literal["before", "after"],
    ) -> ChangeTimedLoadResult:
        if re.fullmatch(r"change-[0-9a-f]{32}", change_id) is None:
            raise ChangeLoadError("change load identity must be a change-* artifact ID")
        with tempfile.TemporaryDirectory(prefix="kubefit-change-k6-") as directory:
            temporary = Path(directory)
            summary_path = temporary / "summary.json"
            raw_path = temporary / "raw.json"
            command = [
                "k6",
                "run",
                "--quiet",
                "--no-color",
                "--out",
                f"json={raw_path}",
                "-e",
                f"KUBEFIT_TARGET_URL={self._target_url}",
                "-e",
                f"KUBEFIT_CHANGE_ID={change_id}",
                "-e",
                f"KUBEFIT_VARIANT={variant}",
                "-e",
                f"KUBEFIT_SUMMARY_PATH={summary_path}",
                str(self._script_path),
            ]
            started_at = self._clock()
            self._runner(command, self._timeout_seconds)
            finished_at = self._clock()
            try:
                summary_content = summary_path.read_bytes()
                raw_content = raw_path.read_bytes()
                summary = ChangeK6RunSummary.model_validate_json(summary_content)
                raw_text = raw_content.decode()
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ChangeLoadError("k6 change output is missing or invalid") from exc
            if summary.change_id != change_id or summary.variant != variant:
                raise ChangeLoadError("k6 change output identity does not match invocation")
            recovery_seconds, recovered = recovery_from_k6_raw(raw_text, summary)
            return ChangeTimedLoadResult(
                summary=summary,
                started_at=started_at,
                finished_at=finished_at,
                traffic_spike_recovery_seconds=recovery_seconds,
                traffic_spike_recovered=recovered,
                summary_content=summary_content,
                raw_content=raw_content,
            )


def _run_k6(command: Sequence[str], timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise ChangeLoadError(f"k6 failed: {detail.strip()}") from exc
    stderr = completed.stderr or ""
    if 'hint="script exception"' in stderr:
        raise ChangeLoadError(
            f"k6 reported a script exception despite exit code 0: {stderr.strip()}"
        )
    return completed.stdout
