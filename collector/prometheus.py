import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx


class PrometheusError(RuntimeError):
    """Raised when Prometheus cannot return usable metric data."""


@dataclass(frozen=True)
class WorkloadMetrics:
    cpu_p95_millicores: float
    memory_p99_mib: float
    cpu_max_millicores: float
    memory_max_mib: float
    observation_days: int | float
    step_seconds: int
    sample_count: int
    observation_coverage: float
    metric_pod_count: int
    minimum_current_pod_sample_count: int
    cpu_throttling_p95_percent: float | None
    cpu_throttling_max_percent: float | None
    cpu_throttling_sample_count: int
    cpu_throttling_pod_count: int
    cpu_throttling_observation_coverage: float
    minimum_current_pod_throttling_sample_count: int
    requested_start: datetime
    query_start: datetime
    history_clipped: bool


@dataclass(frozen=True)
class MetricSeries:
    metric: dict[str, str]
    samples: tuple[tuple[float, float], ...]

    @property
    def values(self) -> list[float]:
        return [value for _, value in self.samples]


def percentile(values: list[float], quantile: float) -> float:
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    if not values:
        raise PrometheusError("Prometheus returned no samples for the workload")
    if any(not math.isfinite(value) for value in values):
        raise PrometheusError("Prometheus returned a non-finite sample")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * quantile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _promql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _promql_regex(values: list[str]) -> str:
    if not values:
        raise ValueError("at least one ReplicaSet is required")
    escaped = [re.escape(value).replace(r"\-", "-") for value in values]
    return _promql_string("(?:" + "|".join(escaped) + ")")


class PrometheusClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=20)

    def query_range_series(
        self, query: str, start: datetime, end: datetime, step_seconds: int = 300
    ) -> list[MetricSeries]:
        response = self._client.get(
            "/api/v1/query_range",
            params={
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": step_seconds,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise PrometheusError(payload.get("error", "Prometheus query failed"))
        metric_series: list[MetricSeries] = []
        for series in payload.get("data", {}).get("result", []):
            samples = tuple(
                (float(timestamp), float(value)) for timestamp, value in series.get("values", [])
            )
            if samples:
                metric_series.append(
                    MetricSeries(metric=dict(series.get("metric", {})), samples=samples)
                )
        return metric_series

    def verify_pod_uids(self, namespace: str, pod_uids: dict[str, str]) -> None:
        """Fail closed when this Prometheus cannot identify the current Kubernetes Pods."""
        if not pod_uids or any(not uid for uid in pod_uids.values()):
            raise PrometheusError("Kubernetes did not return UIDs for every current Pod")
        query = (
            f'kube_pod_info{{namespace="{_promql_string(namespace)}",'
            f'pod=~"{_promql_regex(sorted(pod_uids))}"}}'
        )
        response = self._client.get("/api/v1/query", params={"query": query})
        response.raise_for_status()
        payload = response.json()
        if (
            payload.get("status") != "success"
            or payload.get("data", {}).get("resultType") != "vector"
        ):
            raise PrometheusError("Prometheus did not return a valid Pod identity vector")

        result = payload["data"].get("result")
        if not isinstance(result, list):
            raise PrometheusError("Prometheus returned invalid Pod identity data")
        found: dict[str, set[str]] = {}
        for series in result:
            if not isinstance(series, dict):
                raise PrometheusError("Prometheus returned invalid Pod identity data")
            labels = series.get("metric", {})
            if not isinstance(labels, dict):
                raise PrometheusError("Prometheus returned invalid Pod identity data")
            pod = labels.get("pod")
            uid = labels.get("uid")
            value = series.get("value", [])
            if (
                labels.get("namespace") != namespace
                or not isinstance(pod, str)
                or pod not in pod_uids
                or not isinstance(uid, str)
                or not uid
                or not isinstance(value, list)
                or len(value) != 2
            ):
                raise PrometheusError("Prometheus returned invalid Pod identity data")
            try:
                gauge = float(value[1])
            except (TypeError, ValueError) as exc:
                raise PrometheusError("Prometheus returned invalid Pod identity data") from exc
            if not math.isfinite(gauge) or gauge != 1:
                raise PrometheusError("Prometheus returned invalid Pod identity data")
            found.setdefault(pod, set()).add(uid)

        for pod, expected_uid in sorted(pod_uids.items()):
            if found.get(pod) != {expected_uid}:
                raise PrometheusError(
                    f"Prometheus Pod UID is missing or differs from Kubernetes for {pod!r}"
                )

    def query_range(
        self, query: str, start: datetime, end: datetime, step_seconds: int = 300
    ) -> list[float]:
        return [
            value
            for series in self.query_range_series(query, start, end, step_seconds)
            for value in series.values
        ]

    def benchmark_cpu_throttling_p95(
        self,
        namespace: str,
        pods: list[str],
        container: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 5,
        rate_window_seconds: int = 30,
    ) -> float:
        if not pods:
            raise ValueError("at least one benchmark Pod is required")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("benchmark timestamps must include timezone information")
        if end <= start:
            raise ValueError("benchmark end must be later than start")
        if step_seconds < 1 or rate_window_seconds < 1:
            raise ValueError("benchmark query intervals must be positive")
        query_start = start + timedelta(seconds=rate_window_seconds)
        if query_start >= end:
            raise ValueError("benchmark window is too short for the Prometheus rate window")

        namespace_value = _promql_string(namespace)
        container_value = _promql_string(container)
        pod_pattern = _promql_regex(pods)
        labels = f'namespace="{namespace_value}",container="{container_value}",pod=~"{pod_pattern}"'
        throttled = (
            "sum by (pod) ("
            "rate(container_cpu_cfs_throttled_periods_total"
            f"{{{labels}}}[{rate_window_seconds}s]))"
        )
        periods = (
            "sum by (pod) ("
            "rate(container_cpu_cfs_periods_total"
            f"{{{labels}}}[{rate_window_seconds}s]))"
        )
        query = f"clamp_max(100 * ({throttled}) / clamp_min(({periods}), 1e-9), 100)"
        series = self.query_range_series(query, query_start, end, step_seconds)
        if not series:
            raise PrometheusError("Prometheus returned no throttling samples for benchmark")
        series_by_pod = _series_by_pod(series, "benchmark CPU throttling")
        return max(percentile(item.values, 0.95) for item in series_by_pod.values())

    def workload_metrics(
        self,
        namespace: str,
        replica_sets: list[str],
        pods: list[str],
        container: str,
        workload_created_at: datetime,
        observation_days: int | float = 7,
        step_seconds: int = 300,
        now: datetime | None = None,
    ) -> WorkloadMetrics:
        if observation_days <= 0:
            raise ValueError("observation_days must be positive")
        if not pods:
            raise ValueError("at least one current pod is required")
        if step_seconds < 1:
            raise ValueError("step_seconds must be at least 1")
        end = now or datetime.now(UTC)
        if end.tzinfo is None or workload_created_at.tzinfo is None:
            raise ValueError("metric timestamps must include timezone information")
        requested_start = end - timedelta(days=observation_days)
        created_at = workload_created_at.astimezone(UTC)
        if created_at > end:
            raise PrometheusError("workload creation timestamp is in the future")
        query_start = max(requested_start, created_at)
        namespace_value = _promql_string(namespace)
        replica_set_pattern = _promql_regex(replica_sets)
        container_value = _promql_string(container)
        container_labels = f'namespace="{namespace_value}",container="{container_value}"'
        ownership = (
            "max by(namespace,pod) ("
            f'kube_pod_owner{{namespace="{namespace_value}",owner_kind="ReplicaSet",'
            f'owner_name=~"{replica_set_pattern}",'
            'owner_is_controller="true"})'
        )
        cpu_query = (
            "sum by (pod) ("
            f"rate(container_cpu_usage_seconds_total{{{container_labels}}}[5m]) "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        memory_query = (
            "sum by (pod) ("
            f"container_memory_working_set_bytes{{{container_labels}}} "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        throttled_periods = (
            "sum by (pod) ("
            f"rate(container_cpu_cfs_throttled_periods_total{{{container_labels}}}[5m]) "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        total_periods = (
            "sum by (pod) ("
            f"rate(container_cpu_cfs_periods_total{{{container_labels}}}[5m]) "
            f"* on(namespace,pod) group_left() ({ownership}))"
        )
        throttling_query = (
            f"clamp_max(100 * ({throttled_periods}) / clamp_min(({total_periods}), 1e-9), 100)"
        )
        cpu_series = self.query_range_series(cpu_query, query_start, end, step_seconds)
        memory_series = self.query_range_series(memory_query, query_start, end, step_seconds)
        throttling_series = self.query_range_series(
            throttling_query, query_start, end, step_seconds
        )
        if not cpu_series or not memory_series:
            raise PrometheusError("Prometheus returned no samples for the workload")

        cpu_by_pod = _series_by_pod(cpu_series, "CPU usage")
        memory_by_pod = _series_by_pod(memory_series, "memory usage")
        common_pods = cpu_by_pod.keys() & memory_by_pod.keys()
        if not common_pods:
            raise PrometheusError("CPU and memory metrics have no matching Pod identities")
        paired_by_pod = {
            pod: _paired_samples(cpu_by_pod[pod], memory_by_pod[pod]) for pod in common_pods
        }
        paired_by_pod = {pod: samples for pod, samples in paired_by_pod.items() if samples}
        if not paired_by_pod:
            raise PrometheusError("CPU and memory metrics have no matching Pod timestamps")
        current_pods = set(pods)
        current_usage_pods = current_pods & paired_by_pod.keys()
        throttling_by_pod = (
            _series_by_pod(throttling_series, "CPU throttling") if throttling_series else {}
        )
        current_throttling_pods = current_pods & throttling_by_pod.keys()

        # Resources are applied per Pod, so retain the busiest replica rather than
        # averaging it away or applying a Deployment-wide sum to every replica.
        cpu_p95_cores = max(
            percentile([cpu for cpu, _ in samples], 0.95) for samples in paired_by_pod.values()
        )
        memory_p99_bytes = max(
            percentile([memory for _, memory in samples], 0.99)
            for samples in paired_by_pod.values()
        )
        expected_per_pod = math.floor((end - requested_start).total_seconds() / step_seconds) + 1
        expected_total = expected_per_pod * len(pods)
        observed_samples = sum(len(samples) for samples in paired_by_pod.values())
        return WorkloadMetrics(
            cpu_p95_millicores=cpu_p95_cores * 1000,
            memory_p99_mib=memory_p99_bytes / (1024 * 1024),
            cpu_max_millicores=max(cpu for samples in paired_by_pod.values() for cpu, _ in samples)
            * 1000,
            memory_max_mib=max(
                memory for samples in paired_by_pod.values() for _, memory in samples
            )
            / (1024 * 1024),
            observation_days=observation_days,
            step_seconds=step_seconds,
            sample_count=observed_samples,
            observation_coverage=min(1.0, observed_samples / expected_total),
            metric_pod_count=len(current_usage_pods),
            minimum_current_pod_sample_count=min(
                (len(paired_by_pod[pod]) for pod in current_pods), default=0
            )
            if current_pods <= paired_by_pod.keys()
            else 0,
            cpu_throttling_p95_percent=(
                max(percentile(series.values, 0.95) for series in throttling_by_pod.values())
                if throttling_by_pod
                else None
            ),
            cpu_throttling_max_percent=(
                max(max(series.values) for series in throttling_by_pod.values())
                if throttling_by_pod
                else None
            ),
            cpu_throttling_sample_count=sum(
                len(series.samples) for series in throttling_by_pod.values()
            ),
            cpu_throttling_pod_count=len(current_throttling_pods),
            cpu_throttling_observation_coverage=min(
                1.0,
                sum(len(series.samples) for series in throttling_by_pod.values()) / expected_total,
            ),
            minimum_current_pod_throttling_sample_count=min(
                (len(throttling_by_pod[pod].samples) for pod in current_pods),
                default=0,
            )
            if current_pods <= throttling_by_pod.keys()
            else 0,
            requested_start=requested_start,
            query_start=query_start,
            history_clipped=query_start > requested_start,
        )


def _series_by_pod(series: list[MetricSeries], metric_name: str) -> dict[str, MetricSeries]:
    result: dict[str, MetricSeries] = {}
    for item in series:
        pod = item.metric.get("pod")
        if not pod:
            raise PrometheusError(f"{metric_name} series is missing the Pod label")
        if pod in result:
            raise PrometheusError(f"{metric_name} returned duplicate series for Pod {pod}")
        result[pod] = item
    return result


def _paired_samples(cpu: MetricSeries, memory: MetricSeries) -> list[tuple[float, float]]:
    memory_by_timestamp = dict(memory.samples)
    return [
        (cpu_value, memory_by_timestamp[timestamp])
        for timestamp, cpu_value in cpu.samples
        if timestamp in memory_by_timestamp
    ]
