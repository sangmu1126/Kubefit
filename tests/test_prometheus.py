from datetime import UTC, datetime, timedelta

import httpx
import pytest

from collector.prometheus import PrometheusClient, PrometheusError, percentile


def test_percentile_interpolates_samples() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2.5


def test_verifies_current_pod_uids_against_prometheus() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        queries.append(str(request.url.params["query"]))
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"namespace": "demo", "pod": "api-a", "uid": "uid-a"},
                            "value": [1, "1"],
                        },
                        {
                            "metric": {"namespace": "demo", "pod": "api-b", "uid": "uid-b"},
                            "value": [1, "1"],
                        },
                    ],
                },
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    PrometheusClient("http://prometheus", client=http).verify_pod_uids(
        "demo", {"api-a": "uid-a", "api-b": "uid-b"}
    )

    assert queries == ['kube_pod_info{namespace="demo",pod=~"(?:api-a|api-b)"}']


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ([], "missing or differs"),
        (
            [{"metric": {"namespace": "demo", "pod": "api-a", "uid": "old"}, "value": [1, "1"]}],
            "missing or differs",
        ),
        (
            [
                {
                    "metric": {"namespace": "demo", "pod": "api-a", "uid": "uid-a"},
                    "value": [1, "1"],
                },
                {"metric": {"namespace": "demo", "pod": "api-a", "uid": "old"}, "value": [1, "1"]},
            ],
            "missing or differs",
        ),
    ],
)
def test_rejects_missing_stale_or_conflicting_pod_identity(
    result: list[dict[str, object]], message: str
) -> None:
    http = httpx.Client(
        base_url="http://prometheus",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "vector", "result": result}},
            )
        ),
    )

    with pytest.raises(PrometheusError, match=message):
        PrometheusClient("http://prometheus", client=http).verify_pod_uids(
            "demo", {"api-a": "uid-a"}
        )


def test_rejects_missing_kubernetes_pod_uid_without_query() -> None:
    with pytest.raises(PrometheusError, match="Kubernetes did not return UIDs"):
        PrometheusClient("http://prometheus").verify_pod_uids("demo", {"api-a": ""})


def test_collects_benchmark_throttling_inside_aligned_window() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"pod": "api-a"},
                            "values": [[1, "1"], [2, "3"], [3, "2"]],
                        },
                        {
                            "metric": {"pod": "api-b"},
                            "values": [[1, "4"], [2, "5"], [3, "6"]],
                        },
                    ]
                },
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    start = datetime(2026, 8, 21, tzinfo=UTC)

    result = PrometheusClient("http://prometheus", client=http).benchmark_cpu_throttling_p95(
        namespace="demo",
        pods=["api-a", "api-b"],
        container="api",
        start=start,
        end=start + timedelta(seconds=160),
    )

    assert result == pytest.approx(5.9)
    params = requests[0].url.params
    assert datetime.fromisoformat(params["start"]) == start + timedelta(seconds=30)
    assert datetime.fromisoformat(params["end"]) == start + timedelta(seconds=160)
    assert params["step"] == "5"
    assert 'pod=~"(?:api-a|api-b)"' in params["query"]


def test_rejects_benchmark_window_shorter_than_rate_window() -> None:
    start = datetime(2026, 8, 21, tzinfo=UTC)

    with pytest.raises(ValueError, match="too short"):
        PrometheusClient("http://prometheus").benchmark_cpu_throttling_p95(
            "demo",
            ["api"],
            "api",
            start,
            start + timedelta(seconds=30),
        )


def test_strict_benchmark_throttling_rejects_missing_pod_series() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"pod": "api-a"}, "values": [[1, "0"], [2, "0"], [3, "0"]]}
                    ]
                },
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    start = datetime(2026, 8, 21, tzinfo=UTC)
    with pytest.raises(PrometheusError, match="every benchmark Pod"):
        PrometheusClient("http://prometheus", client=http).benchmark_cpu_throttling_p95(
            namespace="demo",
            pods=["api-a", "api-b"],
            container="api",
            start=start,
            end=start + timedelta(seconds=160),
            require_all_pods=True,
        )


def test_strict_benchmark_throttling_rejects_sparse_pod_series() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"pod": "api-a"}, "values": [[1, "0"], [2, "0"], [3, "0"]]}
                    ]
                },
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    start = datetime(2026, 8, 21, tzinfo=UTC)
    with pytest.raises(PrometheusError, match="every benchmark Pod"):
        PrometheusClient("http://prometheus", client=http).benchmark_cpu_throttling_p95(
            namespace="demo",
            pods=["api-a"],
            container="api",
            start=start,
            end=start + timedelta(seconds=160),
            require_all_pods=True,
        )


def test_rejects_invalid_observation_window() -> None:
    with pytest.raises(ValueError, match="positive"):
        PrometheusClient("http://prometheus").workload_metrics(
            "demo",
            ["api-rs"],
            ["api-abc"],
            "api",
            datetime(2025, 1, 1, tzinfo=UTC),
            0,
        )


def test_accepts_one_hour_observation_window() -> None:
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(str(request.url.params["start"]))
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"pod": "api-current-pod"},
                            "values": [[1, "1"], [2, "2"]],
                        }
                    ]
                },
            },
        )

    now = datetime(2026, 8, 21, tzinfo=UTC)
    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))

    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["api-current"],
        ["api-current-pod"],
        "api",
        now - timedelta(days=1),
        observation_days=1 / 24,
        step_seconds=60,
        now=now,
    )

    assert starts == [(now - timedelta(hours=1)).isoformat()] * 3
    assert result.observation_days == pytest.approx(1 / 24)
    assert result.step_seconds == 60


def test_collects_workload_percentiles_and_units() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(str(request.url.params["query"]))
        values = [[1, "0.1"], [2, "0.2"], [3, "0.3"]]
        query = str(request.url.params["query"])
        if "memory" in query:
            values = [[1, str(100 * 1024 * 1024)], [2, str(200 * 1024 * 1024)]]
        elif "cfs_throttled" in query:
            values = [[1, "0.5"], [2, "1.5"], [3, "2.5"]]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"result": [{"metric": {"pod": "api-abc"}, "values": values}]},
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["overprovisioned-api-old", "overprovisioned-api-current"],
        ["api-abc", "api-def"],
        "api",
        datetime(2025, 1, 1, tzinfo=UTC),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result.cpu_p95_millicores == pytest.approx(195)
    assert result.memory_p99_mib == pytest.approx(199)
    assert result.cpu_max_millicores == pytest.approx(200)
    assert result.memory_max_mib == pytest.approx(200)
    assert result.step_seconds == 300
    assert result.sample_count == 2
    assert result.metric_pod_count == 1
    assert result.cpu_throttling_p95_percent == pytest.approx(2.4)
    assert result.cpu_throttling_max_percent == pytest.approx(2.5)
    assert result.cpu_throttling_sample_count == 3
    assert result.cpu_throttling_pod_count == 1
    assert 0 < result.cpu_throttling_observation_coverage < 0.01
    assert 0 < result.observation_coverage < 0.01
    assert all('namespace="demo"' in query for query in queries)
    assert all("kube_pod_owner" in query for query in queries)
    assert all("kube_replicaset_owner" not in query for query in queries)
    assert all("overprovisioned-api-old" in query for query in queries)
    assert all("overprovisioned-api-current" in query for query in queries)
    assert all('owner_is_controller="true"}}' not in query for query in queries)
    assert all("sum by (pod)" in query for query in queries)


def test_uses_the_busiest_pod_percentile_instead_of_summing_replicas() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "memory" in str(request.url.params["query"]):
            first = [[1, str(100 * 1024 * 1024)], [2, str(120 * 1024 * 1024)]]
            second = [[1, str(400 * 1024 * 1024)], [2, str(500 * 1024 * 1024)]]
        else:
            first = [[1, "0.1"], [2, "0.2"]]
            second = [[1, "0.4"], [2, "0.6"]]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"pod": "api-abc"}, "values": first},
                        {"metric": {"pod": "api-def"}, "values": second},
                    ]
                },
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["overprovisioned-api-old", "overprovisioned-api-current"],
        ["api-new"],
        "api",
        datetime(2025, 1, 1, tzinfo=UTC),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result.cpu_p95_millicores == pytest.approx(590)
    assert result.memory_p99_mib == pytest.approx(499)
    assert result.cpu_p95_millicores < 790  # The two Pods were not summed together.
    assert result.metric_pod_count == 0  # Only current Pod identities count for readiness.


def test_clips_query_at_current_workload_creation_but_keeps_requested_coverage() -> None:
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(str(request.url.params["start"]))
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"pod": "api-current-pod"},
                            "values": [[1, "1"], [2, "2"]],
                        }
                    ]
                },
            },
        )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    created_at = now - timedelta(hours=12)
    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["api-current"],
        ["api-current-pod"],
        "api",
        created_at,
        observation_days=7,
        now=now,
    )

    assert starts == [created_at.isoformat()] * 3
    assert result.query_start == created_at
    assert result.requested_start == now - timedelta(days=7)
    assert result.history_clipped is True
    assert result.observation_coverage < 0.01


def test_rejects_workload_creation_timestamp_in_the_future() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(PrometheusError, match="creation timestamp is in the future"):
        PrometheusClient("http://prometheus").workload_metrics(
            "demo",
            ["api-current"],
            ["api-current-pod"],
            "api",
            now + timedelta(seconds=1),
            now=now,
        )


def test_rejects_workload_when_prometheus_returns_no_samples() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))

    with pytest.raises(PrometheusError, match="no samples"):
        PrometheusClient("http://prometheus", client=http).workload_metrics(
            "demo",
            ["api-current"],
            ["api-current-pod"],
            "api",
            datetime(2025, 1, 1, tzinfo=UTC),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_marks_cpu_throttling_unavailable_without_losing_usage_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = str(request.url.params["query"])
        result = (
            []
            if "cfs_throttled" in query
            else [{"metric": {"pod": "api-current-pod"}, "values": [[1, "1"]]}]
        )
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["api-current"],
        ["api-current-pod"],
        "api",
        datetime(2025, 1, 1, tzinfo=UTC),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result.cpu_p95_millicores == 1000
    assert result.cpu_throttling_p95_percent is None
    assert result.cpu_throttling_sample_count == 0
    assert result.cpu_throttling_pod_count == 0
    assert result.cpu_throttling_observation_coverage == 0


def test_rejects_disjoint_cpu_and_memory_pod_identities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = str(request.url.params["query"])
        pods = ["memory-a", "memory-b"] if "memory" in query else ["cpu-a", "cpu-b"]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"pod": pod}, "values": [[1, "1"], [2, "2"]]} for pod in pods
                    ]
                },
            },
        )

    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))

    with pytest.raises(PrometheusError, match="no matching Pod identities"):
        PrometheusClient("http://prometheus", client=http).workload_metrics(
            "demo",
            ["api-current"],
            ["api-a", "api-b"],
            "api",
            datetime(2025, 1, 1, tzinfo=UTC),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_reports_least_observed_current_pod_instead_of_hiding_skew() -> None:
    dense = [[timestamp, "1"] for timestamp in range(121)]
    sparse = [[0, "1"]]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"pod": "api-a"}, "values": dense},
                        {"metric": {"pod": "api-b"}, "values": sparse},
                    ]
                },
            },
        )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    http = httpx.Client(base_url="http://prometheus", transport=httpx.MockTransport(handler))
    result = PrometheusClient("http://prometheus", client=http).workload_metrics(
        "demo",
        ["api-current"],
        ["api-a", "api-b"],
        "api",
        now - timedelta(days=1),
        observation_days=1 / 24,
        step_seconds=60,
        now=now,
    )

    assert result.sample_count == 122
    assert result.observation_coverage == 1
    assert result.metric_pod_count == 2
    assert result.minimum_current_pod_sample_count == 1
    assert result.minimum_current_pod_throttling_sample_count == 1
