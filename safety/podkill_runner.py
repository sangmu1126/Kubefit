import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

from safety.podkill import (
    KubectlPodKillPreflight,
    PodKillCandidate,
    PodKillPreflight,
    PodKillPreflightError,
)


class PodKillExperimentError(RuntimeError):
    """Raised when a PodKill cannot be safely started or recorded."""


class HttpProbeObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float = Field(ge=0)
    error_type: str | None = None

    @model_validator(mode="after")
    def fields_match_outcome(self) -> "HttpProbeObservation":
        expected_success = self.status_code is not None and self.status_code < 500
        if self.success != expected_success:
            raise ValueError("probe success must mean an HTTP status below 500")
        if self.success and self.error_type is not None:
            raise ValueError("successful probe cannot contain an error type")
        if not self.success and self.error_type is None:
            raise ValueError("failed probe must contain an error type")
        return self


class PodKillProbeSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: datetime
    elapsed_seconds: float = Field(ge=0)
    observation: HttpProbeObservation

    @model_validator(mode="after")
    def timestamp_has_timezone(self) -> "PodKillProbeSample":
        if self.observed_at.tzinfo is None:
            raise ValueError("probe timestamp must include timezone")
        return self


class PodKillExperimentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["pass", "fail"]
    preflight: PodKillPreflight
    deleted: PodKillCandidate
    replacement: PodKillCandidate | None
    injected_at: datetime
    finished_at: datetime
    service_recovery_seconds: float | None = Field(default=None, ge=0)
    replacement_ready_seconds: float | None = Field(default=None, ge=0)
    required_consecutive_successes: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    samples: list[PodKillProbeSample] = Field(min_length=1)
    failure_reasons: list[str]

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "PodKillExperimentResult":
        if self.injected_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("experiment timestamps must include timezone")
        if self.finished_at < self.injected_at:
            raise ValueError("experiment finish cannot precede injection")
        complete = (
            self.replacement is not None
            and self.service_recovery_seconds is not None
            and self.replacement_ready_seconds is not None
        )
        if self.status == "pass" and (not complete or self.failure_reasons):
            raise ValueError("passing experiment requires complete recovery without failures")
        if self.status == "fail" and not self.failure_reasons:
            raise ValueError("failed experiment requires at least one reason")
        if self.replacement is not None and self.replacement.pod_uid == self.deleted.pod_uid:
            raise ValueError("replacement Pod UID must differ from deleted Pod UID")
        if self.deleted != self.preflight.selected:
            raise ValueError("deleted Pod must match the refreshed preflight selection")
        if (self.replacement is None) != (self.replacement_ready_seconds is None):
            raise ValueError("replacement Pod and readiness duration must appear together")
        elapsed = [sample.elapsed_seconds for sample in self.samples]
        if elapsed != sorted(elapsed):
            raise ValueError("probe elapsed times must be ordered")
        streak = 0
        streak_start: float | None = None
        replayed_recovery: float | None = None
        for sample in self.samples:
            if sample.observation.success:
                if streak == 0:
                    streak_start = sample.elapsed_seconds
                streak += 1
                if streak >= self.required_consecutive_successes:
                    replayed_recovery = streak_start
            else:
                streak = 0
                streak_start = None
                replayed_recovery = None
        if self.service_recovery_seconds != replayed_recovery:
            raise ValueError("service recovery does not replay from probe samples")
        if self.status == "fail" and self.samples[-1].elapsed_seconds < self.timeout_seconds:
            raise ValueError("failed experiment must reach its bounded timeout")
        return self


class PodKillInspector(Protocol):
    def inspect(self, target) -> PodKillPreflight: ...


HttpProbe = Callable[[str, float], HttpProbeObservation]
CommandRunner = Callable[[Sequence[str]], str]
MonotonicClock = Callable[[], float]
UtcClock = Callable[[], datetime]
Sleeper = Callable[[float], None]


class PodKillExperimentRunner:
    def __init__(
        self,
        context: str,
        inspector: PodKillInspector | None = None,
        command_runner: CommandRunner | None = None,
        probe: HttpProbe | None = None,
        monotonic: MonotonicClock = time.monotonic,
        utc_clock: UtcClock | None = None,
        sleeper: Sleeper = time.sleep,
        timeout_seconds: float = 120,
        probe_interval_seconds: float = 0.5,
        probe_timeout_seconds: float = 2,
        required_consecutive_successes: int = 3,
    ) -> None:
        if not context.startswith("kind-"):
            raise ValueError("PodKill experiment is restricted to an explicit kind-* context")
        if timeout_seconds <= 0 or probe_interval_seconds <= 0 or probe_timeout_seconds <= 0:
            raise ValueError("PodKill timeout and probe intervals must be positive")
        if required_consecutive_successes < 1:
            raise ValueError("required consecutive successes must be positive")
        self._context = context
        self._inspector = inspector or KubectlPodKillPreflight(context)
        self._command_runner = command_runner or _run_command
        self._probe = probe or _probe_http
        self._monotonic = monotonic
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._timeout_seconds = timeout_seconds
        self._probe_interval_seconds = probe_interval_seconds
        self._probe_timeout_seconds = probe_timeout_seconds
        self._required_consecutive_successes = required_consecutive_successes

    def run(self, approved: PodKillPreflight, target_url: str) -> PodKillExperimentResult:
        _validate_target_url(target_url)
        refreshed = self._inspector.inspect(approved.target)
        _require_unchanged_preflight(approved, refreshed)
        injected_at = self._utc_clock()
        started = self._monotonic()
        self._command_runner(
            [
                "kubectl",
                "--context",
                self._context,
                "delete",
                "pod",
                refreshed.selected.pod,
                "--namespace",
                refreshed.target.namespace,
                "--grace-period=1",
                "--wait=false",
            ]
        )
        original_uids = {item.pod_uid for item in refreshed.candidates}
        samples: list[PodKillProbeSample] = []
        streak_start: float | None = None
        consecutive = 0
        service_recovery: float | None = None
        replacement_ready: float | None = None
        replacement: PodKillCandidate | None = None

        while True:
            elapsed = max(0.0, self._monotonic() - started)
            observation = self._probe(target_url, self._probe_timeout_seconds)
            samples.append(
                PodKillProbeSample(
                    observed_at=self._utc_clock(),
                    elapsed_seconds=elapsed,
                    observation=observation,
                )
            )
            if observation.success:
                if consecutive == 0:
                    streak_start = elapsed
                consecutive += 1
                if consecutive >= self._required_consecutive_successes:
                    service_recovery = streak_start
            else:
                consecutive = 0
                streak_start = None
                service_recovery = None

            try:
                current = self._inspector.inspect(approved.target)
            except PodKillPreflightError:
                current = None
            if current is not None:
                new_candidates = [
                    item for item in current.candidates if item.pod_uid not in original_uids
                ]
                current_uids = {item.pod_uid for item in current.candidates}
                if (
                    replacement is None
                    and refreshed.selected.pod_uid not in current_uids
                    and len(new_candidates) == 1
                ):
                    replacement = new_candidates[0]
                    replacement_ready = elapsed

            if service_recovery is not None and replacement is not None:
                return PodKillExperimentResult(
                    status="pass",
                    preflight=refreshed,
                    deleted=refreshed.selected,
                    replacement=replacement,
                    injected_at=injected_at,
                    finished_at=self._utc_clock(),
                    service_recovery_seconds=service_recovery,
                    replacement_ready_seconds=replacement_ready,
                    required_consecutive_successes=self._required_consecutive_successes,
                    timeout_seconds=self._timeout_seconds,
                    samples=samples,
                    failure_reasons=[],
                )
            if elapsed >= self._timeout_seconds:
                reasons = []
                if service_recovery is None:
                    reasons.append("HTTP service did not achieve the required success streak")
                if replacement is None:
                    reasons.append("a new fully ready replacement Pod was not observed")
                return PodKillExperimentResult(
                    status="fail",
                    preflight=refreshed,
                    deleted=refreshed.selected,
                    replacement=replacement,
                    injected_at=injected_at,
                    finished_at=self._utc_clock(),
                    service_recovery_seconds=service_recovery,
                    replacement_ready_seconds=replacement_ready,
                    required_consecutive_successes=self._required_consecutive_successes,
                    timeout_seconds=self._timeout_seconds,
                    samples=samples,
                    failure_reasons=reasons,
                )
            self._sleeper(self._probe_interval_seconds)


def _require_unchanged_preflight(
    approved: PodKillPreflight,
    refreshed: PodKillPreflight,
) -> None:
    if approved.context != refreshed.context or approved.target != refreshed.target:
        raise PodKillExperimentError("PodKill context or target changed after approval")
    if (
        approved.deployment_uid != refreshed.deployment_uid
        or approved.deployment_generation != refreshed.deployment_generation
        or approved.desired_replicas != refreshed.desired_replicas
    ):
        raise PodKillExperimentError("Deployment identity or generation changed after approval")
    approved_uids = {item.pod_uid for item in approved.candidates}
    refreshed_uids = {item.pod_uid for item in refreshed.candidates}
    if approved_uids != refreshed_uids or approved.selected != refreshed.selected:
        raise PodKillExperimentError("eligible Pod set changed after approval; run preflight again")


def _validate_target_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "probe URL must be HTTP(S) without credentials, query, or fragment"
        )


def _probe_http(target_url: str, timeout_seconds: float) -> HttpProbeObservation:
    started = time.monotonic()
    request = Request(target_url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            status = response.status
        return HttpProbeObservation(
            success=status < 500,
            status_code=status,
            latency_ms=(time.monotonic() - started) * 1000,
            error_type=None if status < 500 else "HTTPServerError",
        )
    except HTTPError as exc:
        return HttpProbeObservation(
            success=exc.code < 500,
            status_code=exc.code,
            latency_ms=(time.monotonic() - started) * 1000,
            error_type=None if exc.code < 500 else "HTTPServerError",
        )
    except (URLError, TimeoutError, OSError) as exc:
        return HttpProbeObservation(
            success=False,
            status_code=None,
            latency_ms=(time.monotonic() - started) * 1000,
            error_type=type(exc).__name__,
        )


def _run_command(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise PodKillExperimentError(f"kubectl failed: {detail.strip()}") from exc
    return completed.stdout
