import json
from pathlib import Path

import pytest

from gitops import ManifestTarget
from safety.podkill import KubectlPodKillPreflight, PodKillPreflightError


def deployment(replicas: int = 2) -> dict[str, object]:
    return {
        "metadata": {"uid": "deployment-uid", "generation": 3},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": "api"}},
        },
        "status": {
            "observedGeneration": 3,
            "replicas": replicas,
            "updatedReplicas": replicas,
            "readyReplicas": replicas,
            "availableReplicas": replicas,
        },
    }


def replica_sets() -> dict[str, object]:
    return {
        "items": [
            {
                "metadata": {
                    "name": "api-owned",
                    "uid": "rs-owned-uid",
                    "ownerReferences": [
                        {
                            "controller": True,
                            "kind": "Deployment",
                            "name": "api",
                            "uid": "deployment-uid",
                        }
                    ],
                }
            },
            {
                "metadata": {
                    "name": "other-rs",
                    "uid": "rs-other-uid",
                    "ownerReferences": [
                        {
                            "controller": True,
                            "kind": "Deployment",
                            "name": "other",
                            "uid": "other-deployment-uid",
                        }
                    ],
                }
            },
        ]
    }


def pod(name: str, uid: str, created: str, *, ready: bool = True, owned: bool = True):
    owner_name = "api-owned" if owned else "other-rs"
    owner_uid = "rs-owned-uid" if owned else "rs-other-uid"
    return {
        "metadata": {
            "name": name,
            "uid": uid,
            "creationTimestamp": created,
            "ownerReferences": [
                {
                    "controller": True,
                    "kind": "ReplicaSet",
                    "name": owner_name,
                    "uid": owner_uid,
                }
            ],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [{"name": "api", "ready": ready}],
        },
    }


def runner_for(
    deployment_document: dict[str, object],
    pod_items: list[dict[str, object]] | None = None,
):
    calls: list[list[str]] = []

    def run(command):
        calls.append(list(command))
        if "deployment" in command:
            return json.dumps(deployment_document)
        if "replicasets" in command:
            return json.dumps(replica_sets())
        if "pods" in command:
            return json.dumps(
                {
                    "items": pod_items
                    or [
                        pod("api-new", "pod-new", "2026-09-10T00:01:00Z"),
                        pod("api-old", "pod-old", "2026-09-10T00:00:00Z"),
                        pod(
                            "other-pod",
                            "other-pod-uid",
                            "2026-09-10T00:00:00Z",
                            owned=False,
                        ),
                    ]
                }
            )
        raise AssertionError(command)

    return run, calls


def test_selects_oldest_ready_pod_owned_through_deployment_replicaset() -> None:
    runner, calls = runner_for(deployment())
    inspector = KubectlPodKillPreflight("kind-kubefit", runner=runner)

    result = inspector.inspect(
        ManifestTarget(namespace="demo", deployment="api", container="api")
    )

    assert result.desired_replicas == result.ready_replicas == 2
    assert [item.pod for item in result.candidates] == ["api-old", "api-new"]
    assert result.selected.pod == "api-old"
    assert result.selected.pod_uid == "pod-old"
    assert len(calls) == 3
    assert all(command[:3] == ["kubectl", "--context", "kind-kubefit"] for command in calls)
    assert "app=api" in calls[1]


def test_rejects_single_replica_before_listing_pods() -> None:
    runner, calls = runner_for(deployment(replicas=1))
    inspector = KubectlPodKillPreflight("kind-kubefit", runner=runner)

    with pytest.raises(PodKillPreflightError, match="at least two"):
        inspector.inspect(
            ManifestTarget(namespace="demo", deployment="api", container="api")
        )

    assert len(calls) == 1


def test_rejects_unready_owned_pod() -> None:
    runner, _ = runner_for(
        deployment(),
        [
            pod("api-one", "pod-one", "2026-09-10T00:00:00Z"),
            pod("api-two", "pod-two", "2026-09-10T00:01:00Z", ready=False),
        ],
    )
    inspector = KubectlPodKillPreflight("kind-kubefit", runner=runner)

    with pytest.raises(PodKillPreflightError, match="not Ready"):
        inspector.inspect(
            ManifestTarget(namespace="demo", deployment="api", container="api")
        )


def test_rejects_non_disposable_context_at_constructor_boundary() -> None:
    with pytest.raises(ValueError, match=r"kind-\*"):
        KubectlPodKillPreflight("production")


def test_module_does_not_contain_a_delete_command_yet() -> None:
    source = (Path(__file__).parents[1] / "safety" / "podkill.py").read_text()

    assert '"delete"' not in source
