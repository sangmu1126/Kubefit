import json
from datetime import UTC, datetime

import pytest

from collector.kubernetes import (
    KubectlDeploymentCollector,
    KubernetesCollectionError,
    _cpu_millicores,
    _memory_mib,
    compile_label_selector,
)

DEPLOYMENT = {
    "metadata": {
        "uid": "deployment-uid",
        "creationTimestamp": "2026-08-20T00:00:00Z",
    },
    "status": {"availableReplicas": 1},
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"app": "demo"}},
        "template": {
            "spec": {
                "containers": [
                    {
                        "name": "api",
                        "resources": {
                            "requests": {"cpu": "1", "memory": "2Gi"},
                            "limits": {"cpu": "2000m", "memory": "4096Mi"},
                        },
                    }
                ]
            }
        },
    }
}

REPLICA_SETS = {
    "items": [
        {
            "metadata": {
                "name": "demo-owned",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "Deployment",
                        "uid": "deployment-uid",
                    }
                ],
            }
        },
        {
            "metadata": {
                "name": "demo-foreign",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "Deployment",
                        "uid": "different-uid",
                    }
                ],
            }
        },
    ]
}

PODS = {
    "items": [
        {
            "metadata": {
                "name": "demo-abc",
                "uid": "pod-uid-abc",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "ReplicaSet",
                        "name": "demo-owned",
                    }
                ],
            },
            "status": {
                "containerStatuses": [
                    {
                        "name": "api",
                        "restartCount": 2,
                        "state": {"running": {}},
                        "lastState": {
                            "terminated": {"reason": "OOMKilled"}
                        },
                    }
                ]
            },
        },
        {
            "metadata": {
                "name": "foreign-pod",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "ReplicaSet",
                        "name": "demo-foreign",
                    }
                ],
            }
        },
    ]
}


def test_collects_deployment_resources_and_pods() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str]) -> str:
        commands.append(command)
        if "deployment" in command:
            return json.dumps(DEPLOYMENT)
        if "replicasets" in command:
            return json.dumps(REPLICA_SETS)
        return json.dumps(PODS)

    result = KubectlDeploymentCollector(runner=runner).collect("demo", "api")

    assert result.resources.cpu_request_millicores == 1000
    assert result.resources.memory_request_mib == 2048
    assert result.pods == ["demo-abc"]
    assert result.pod_uids == {"demo-abc": "pod-uid-abc"}
    assert result.replica_sets == ["demo-owned"]
    assert result.uid == "deployment-uid"
    assert result.created_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert result.desired_replicas == 1
    assert result.available_replicas == 1
    assert result.container_status_count == 1
    assert result.restart_count == 2
    assert result.oom_killed_count == 1
    assert result.pod_runtime_statuses[0].pod == "demo-abc"
    assert result.pod_runtime_statuses[0].restart_count == 2
    assert result.pod_runtime_statuses[0].oom_killed is True
    assert ["-l", "app=demo"] == commands[1][
        commands[1].index("-l") : commands[1].index("-l") + 2
    ]


def test_non_kind_context_collection_uses_only_reads() -> None:
    commands: list[list[str]] = []
    context = "arn:aws:eks:ap-northeast-2:123456789012:cluster/pilot"

    def runner(command: list[str]) -> str:
        commands.append(command)
        if "deployment" in command:
            return json.dumps(DEPLOYMENT)
        if "replicasets" in command:
            return json.dumps(REPLICA_SETS)
        return json.dumps(PODS)

    result = KubectlDeploymentCollector(runner=runner, context=context).collect(
        "demo", "api"
    )

    assert result.uid == "deployment-uid"
    assert [command[:4] for command in commands] == [
        ["kubectl", "--context", context, "get"]
    ] * 3
    assert [command[4] for command in commands] == ["deployment", "replicasets", "pods"]


def test_rejects_missing_resource_configuration() -> None:
    document = json.loads(json.dumps(DEPLOYMENT))
    del document["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]

    with pytest.raises(KubernetesCollectionError, match="must define"):
        KubectlDeploymentCollector(runner=lambda _: json.dumps(document)).collect("demo", "api")


def test_rejects_when_no_replicaset_matches_current_deployment_uid() -> None:
    def runner(command: list[str]) -> str:
        if "deployment" in command:
            return json.dumps(DEPLOYMENT)
        if "replicasets" in command:
            return json.dumps({"items": []})
        return json.dumps({"items": [{"metadata": {"name": "demo-abc"}}]})

    with pytest.raises(KubernetesCollectionError, match="current UID"):
        KubectlDeploymentCollector(runner=runner).collect("demo", "api")


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("1", 1000),
        (".5", 500),
        ("500m", 500),
        ("500u", 1),
        ("1e-3", 1),
        ("1.1e-3", 2),
    ],
)
def test_parses_cpu_quantities_and_rounds_up(
    quantity: str, expected: int
) -> None:
    assert _cpu_millicores(quantity) == expected


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("1.5Gi", 1536),
        ("100M", 96),
        ("1Ki", 1),
        ("1048577", 2),
        ("2e6", 2),
    ],
)
def test_parses_memory_quantities_and_rounds_up(
    quantity: str, expected: int
) -> None:
    assert _memory_mib(quantity) == expected


@pytest.mark.parametrize("quantity", ["", "1MB", "1K", "cpu", "1e"])
def test_rejects_invalid_kubernetes_quantities(quantity: str) -> None:
    with pytest.raises(KubernetesCollectionError, match="invalid Kubernetes quantity"):
        _cpu_millicores(quantity)


def test_compiles_match_labels_and_expressions_for_kubectl() -> None:
    document = json.loads(json.dumps(DEPLOYMENT))
    document["spec"]["selector"]["matchExpressions"] = [
        {"key": "tier", "operator": "Exists"},
        {"key": "environment", "operator": "In", "values": ["qa", "production"]},
        {"key": "version", "operator": "NotIn", "values": ["beta", "alpha"]},
        {"key": "debug", "operator": "DoesNotExist"},
    ]
    commands: list[list[str]] = []

    def runner(command: list[str]) -> str:
        commands.append(command)
        if "deployment" in command:
            return json.dumps(document)
        if "replicasets" in command:
            return json.dumps(REPLICA_SETS)
        return json.dumps(PODS)

    KubectlDeploymentCollector(runner=runner).collect("demo", "api")

    expected = compile_label_selector(document["spec"]["selector"])
    assert expected == (
        "app=demo,!debug,environment in (production,qa),tier,"
        "version notin (alpha,beta)"
    )
    assert [command[command.index("-l") + 1] for command in commands[1:]] == [
        expected,
        expected,
    ]


@pytest.mark.parametrize(
    "expression",
    [
        {"key": "environment", "operator": "In"},
        {"key": "debug", "operator": "Exists", "values": ["true"]},
        {"key": "tier", "operator": "Unknown"},
    ],
)
def test_rejects_invalid_selector_expressions(expression: dict[str, object]) -> None:
    document = json.loads(json.dumps(DEPLOYMENT))
    document["spec"]["selector"]["matchExpressions"] = [expression]

    with pytest.raises(KubernetesCollectionError, match="selector"):
        KubectlDeploymentCollector(runner=lambda _: json.dumps(document)).collect(
            "demo", "api"
        )
