import hashlib
import json
import math
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from benchmarks.result import (
    BenchmarkMeasurement,
    K6RunSummary,
    LoadPhaseMetrics,
    MeasurementProvenance,
    RuntimeBenchmarkSignals,
)
from collector import KubectlDeploymentCollector
from gitops.bundle import LoadedProposalBundle
from gitops.manifest import ManifestTarget

RECOVERY_DURATION_SECONDS = 60
RECOVERY_WINDOW_SECONDS = 5
RECOVERY_MINIMUM_SAMPLES = 20
RECOVERY_LATENCY_FACTOR = 1.10


class BenchmarkMeasurementError(RuntimeError):
    """Raised when aligned benchmark evidence cannot be produced safely."""


class PodRuntimeCounters(BaseModel):
    restart_count: int = Field(ge=0)
    oom_killed: bool


class RuntimeCounterSnapshot(BaseModel):
    pods: dict[str, PodRuntimeCounters]

    @model_validator(mode="after")
    def contains_pods(self) -> "RuntimeCounterSnapshot":
        if not self.pods:
            raise ValueError("runtime snapshot must contain at least one Pod")
        return self


class TimedK6Result(BaseModel):
    summary: K6RunSummary
    started_at: datetime
    finished_at: datetime
    traffic_spike_recovery_seconds: float = Field(ge=0)
    traffic_spike_recovered: bool
    summary_content: bytes
    raw_content: bytes

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> "TimedK6Result":
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("k6 timestamps must include timezone information")
        if self.finished_at <= self.started_at:
            raise ValueError("k6 finish must be later than start")
        return self

    @property
    def summary_sha256(self) -> str:
        return hashlib.sha256(self.summary_content).hexdigest()

    @property
    def raw_sha256(self) -> str:
        return hashlib.sha256(self.raw_content).hexdigest()


class CollectedMeasurement(BaseModel):
    measurement: BenchmarkMeasurement
    k6_summary: bytes
    k6_raw: bytes

    @model_validator(mode="after")
    def evidence_matches_measurement(self) -> "CollectedMeasurement":
        try:
            summary = K6RunSummary.model_validate_json(self.k6_summary)
        except ValueError as exc:
            raise ValueError("collected k6 summary is invalid") from exc
        measured_summary = K6RunSummary.model_validate(self.measurement.model_dump())
        if summary != measured_summary:
            raise ValueError("collected k6 summary conflicts with measurement")
        if hashlib.sha256(self.k6_summary).hexdigest() != (
            self.measurement.provenance.k6_summary_sha256
        ):
            raise ValueError("collected k6 summary hash conflicts with measurement")
        if hashlib.sha256(self.k6_raw).hexdigest() != (
            self.measurement.provenance.k6_raw_sha256
        ):
            raise ValueError("collected k6 raw hash conflicts with measurement")
        return self


class K6Executor(Protocol):
    def run(
        self,
        proposal_id: str,
        variant: Literal["before", "after"],
    ) -> TimedK6Result: ...


class ThrottlingCollector(Protocol):
    def benchmark_cpu_throttling_p95(
        self,
        namespace: str,
        pods: list[str],
        container: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 5,
        rate_window_seconds: int = 30,
    ) -> float: ...


class RecoverySummary(Protocol):
    steady: LoadPhaseMetrics


SnapshotCollector = Callable[[ManifestTarget], RuntimeCounterSnapshot]
K6CommandRunner = Callable[[Sequence[str], int], str]
Clock = Callable[[], datetime]


class DeploymentRuntimeSnapshotter:
    def __init__(self, collector: KubectlDeploymentCollector) -> None:
        self._collector = collector

    def __call__(self, target: ManifestTarget) -> RuntimeCounterSnapshot:
        workload = self._collector.collect(
            target.namespace,
            target.deployment,
            target.container,
        )
        if len(workload.pod_runtime_statuses) != len(workload.pods):
            raise BenchmarkMeasurementError(
                "target container status coverage changed during benchmark"
            )
        return RuntimeCounterSnapshot(
            pods={
                status.pod: PodRuntimeCounters(
                    restart_count=status.restart_count,
                    oom_killed=status.oom_killed,
                )
                for status in workload.pod_runtime_statuses
            }
        )


class SubprocessK6Executor:
    def __init__(
        self,
        target_url: str,
        script_path: Path,
        runner: K6CommandRunner | None = None,
        clock: Clock | None = None,
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
        if not script_path.is_file():
            raise ValueError("k6 script path must be a file")
        if timeout_seconds < 1:
            raise ValueError("k6 timeout must be at least one second")
        self._target_url = target_url
        self._script_path = script_path
        self._runner = runner or _run_k6
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        proposal_id: str,
        variant: Literal["before", "after"],
    ) -> TimedK6Result:
        with tempfile.TemporaryDirectory(prefix="kubefit-k6-") as directory:
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
                f"KUBEFIT_PROPOSAL_ID={proposal_id}",
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
                raw_bytes = raw_path.read_bytes()
                summary = K6RunSummary.model_validate_json(summary_content)
                raw_content = raw_bytes.decode()
            except (OSError, ValueError) as exc:
                raise BenchmarkMeasurementError("k6 output is missing or invalid") from exc
            if summary.proposal_id != proposal_id or summary.variant != variant:
                raise BenchmarkMeasurementError("k6 output identity does not match invocation")
            recovery_seconds, recovered = recovery_from_k6_raw(raw_content, summary)
            return TimedK6Result(
                summary=summary,
                started_at=started_at,
                finished_at=finished_at,
                traffic_spike_recovery_seconds=recovery_seconds,
                traffic_spike_recovered=recovered,
                summary_content=summary_content,
                raw_content=raw_bytes,
            )


class AlignedMeasurementCollector:
    def __init__(
        self,
        k6: K6Executor,
        snapshot: SnapshotCollector,
        prometheus: ThrottlingCollector,
    ) -> None:
        self._k6 = k6
        self._snapshot = snapshot
        self._prometheus = prometheus

    def __call__(
        self,
        proposal: LoadedProposalBundle,
        variant: Literal["before", "after"],
    ) -> CollectedMeasurement:
        before_runtime = self._snapshot(proposal.target)
        load = self._k6.run(proposal.artifact_id, variant)
        after_runtime = self._snapshot(proposal.target)
        restart_delta, oom_delta, pods = _runtime_deltas(before_runtime, after_runtime)
        throttling = self._prometheus.benchmark_cpu_throttling_p95(
            namespace=proposal.target.namespace,
            pods=pods,
            container=proposal.target.container,
            start=load.started_at,
            end=load.finished_at,
        )
        request_cost = (
            proposal.before_request_cost_usd
            if variant == "before"
            else proposal.after_request_cost_usd
        )
        measurement = BenchmarkMeasurement(
            **load.summary.model_dump(),
            runtime=RuntimeBenchmarkSignals(
                cpu_throttling_p95_percent=throttling,
                oom_killed_count=oom_delta,
                restart_count=restart_delta,
                traffic_spike_recovery_seconds=(load.traffic_spike_recovery_seconds),
                traffic_spike_recovered=load.traffic_spike_recovered,
            ),
            provenance=MeasurementProvenance(
                run_started_at=load.started_at,
                run_finished_at=load.finished_at,
                pods=pods,
                k6_summary_sha256=load.summary_sha256,
                k6_raw_sha256=load.raw_sha256,
                prometheus_rate_window_seconds=30,
            ),
            request_cost_usd=request_cost,
        )
        return CollectedMeasurement(
            measurement=measurement,
            k6_summary=load.summary_content,
            k6_raw=load.raw_content,
        )


def recovery_from_k6_raw(raw_content: str, summary: RecoverySummary) -> tuple[float, bool]:
    recovery_starts: list[datetime] = []
    durations: list[tuple[datetime, float]] = []
    for line_number, line in enumerate(raw_content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkMeasurementError(
                f"k6 raw output line {line_number} is invalid JSON"
            ) from exc
        if item.get("type") != "Point":
            continue
        metric = item.get("metric")
        data = item.get("data", {})
        if not isinstance(data, dict):
            continue
        tags = data.get("tags", {})
        if not isinstance(tags, dict):
            continue
        if tags.get("kubefit_phase") != "recovery":
            continue
        timestamp = _parse_k6_timestamp(data.get("time"), line_number)
        if metric == "kubefit_recovery_start":
            recovery_starts.append(timestamp)
        elif metric == "http_req_duration":
            value = data.get("value")
            if not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
                raise BenchmarkMeasurementError(f"k6 raw duration on line {line_number} is invalid")
            durations.append((timestamp, float(value)))

    if not recovery_starts or not durations:
        raise BenchmarkMeasurementError("k6 raw output is missing recovery timing samples")
    recovery_start = min(recovery_starts)
    threshold = summary.steady.latency_p95_ms * RECOVERY_LATENCY_FACTOR
    for window_start_seconds in range(0, RECOVERY_DURATION_SECONDS, RECOVERY_WINDOW_SECONDS):
        window_start = recovery_start + timedelta(seconds=window_start_seconds)
        window_end = window_start + timedelta(seconds=RECOVERY_WINDOW_SECONDS)
        samples = [
            value for timestamp, value in durations if window_start <= timestamp < window_end
        ]
        if len(samples) < RECOVERY_MINIMUM_SAMPLES:
            continue
        if _percentile(samples, 0.95) <= threshold:
            return float(window_start_seconds + RECOVERY_WINDOW_SECONDS), True
    return float(RECOVERY_DURATION_SECONDS), False


def _runtime_deltas(
    before: RuntimeCounterSnapshot,
    after: RuntimeCounterSnapshot,
) -> tuple[int, int, list[str]]:
    if before.pods.keys() != after.pods.keys():
        raise BenchmarkMeasurementError(
            "Pod identity changed during benchmark; runtime deltas are not comparable"
        )
    restart_delta = 0
    oom_delta = 0
    for pod in sorted(before.pods):
        baseline = before.pods[pod]
        candidate = after.pods[pod]
        delta = candidate.restart_count - baseline.restart_count
        if delta < 0:
            raise BenchmarkMeasurementError(f"restart counter decreased for Pod {pod}")
        restart_delta += delta
        if delta > 0 and candidate.oom_killed:
            oom_delta += delta
    return restart_delta, oom_delta, sorted(before.pods)


def _parse_k6_timestamp(value: object, line_number: int) -> datetime:
    if not isinstance(value, str):
        raise BenchmarkMeasurementError(f"k6 raw timestamp on line {line_number} is missing")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BenchmarkMeasurementError(
            f"k6 raw timestamp on line {line_number} is invalid"
        ) from exc
    if timestamp.tzinfo is None:
        raise BenchmarkMeasurementError(f"k6 raw timestamp on line {line_number} lacks timezone")
    return timestamp


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * quantile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


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
        raise BenchmarkMeasurementError(f"k6 failed: {detail.strip()}") from exc
    stderr = completed.stderr or ""
    if 'hint="script exception"' in stderr:
        raise BenchmarkMeasurementError(
            f"k6 reported a script exception despite exit code 0: {stderr.strip()}"
        )
    return completed.stdout
