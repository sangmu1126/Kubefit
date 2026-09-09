from pathlib import Path

import pytest

from safety import DeploymentChangeError, inspect_deployment_change

BASE = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: demo
spec:
  replicas: 2
  selector:
    matchLabels:
      app: api
  template:
    metadata:
      labels:
        app: api
    spec:
      containers:
        - name: api
          image: example/api:v1
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: 1000m
              memory: 1Gi
"""


def _manifests(tmp_path: Path, candidate: str) -> tuple[Path, Path]:
    base_path = tmp_path / "base.yaml"
    candidate_path = tmp_path / "candidate.yaml"
    base_path.write_text(BASE)
    candidate_path.write_text(candidate)
    return base_path, candidate_path


def test_extracts_image_replica_and_resource_changes(tmp_path: Path) -> None:
    candidate = (
        BASE.replace("replicas: 2", "replicas: 3")
        .replace("example/api:v1", "example/api:v2")
        .replace("cpu: 500m", "cpu: 300m")
    )
    base_path, candidate_path = _manifests(tmp_path, candidate)

    change = inspect_deployment_change(
        base_path, candidate_path, namespace="demo", deployment="api"
    )

    assert change.status == "supported"
    assert {item.path for item in change.supported_changes} == {
        "/spec/replicas",
        "/spec/template/spec/containers/0/image",
        "/spec/template/spec/containers/0/resources/requests/cpu",
    }
    assert change.unsupported_changes == []


def test_blocks_unreviewed_environment_changes(tmp_path: Path) -> None:
    candidate = BASE.replace(
        "          image: example/api:v1",
        "          image: example/api:v1\n"
        "          env:\n"
        "            - name: MODE\n"
        "              value: unsafe",
    )
    base_path, candidate_path = _manifests(tmp_path, candidate)

    change = inspect_deployment_change(
        base_path, candidate_path, namespace="demo", deployment="api"
    )

    assert change.status == "unsupported"
    assert change.unsupported_changes[0].path == "/spec/template/spec/containers/0/env"
    assert change.unsupported_changes[0].model_dump() == {
        "path": "/spec/template/spec/containers/0/env"
    }


def test_reports_semantically_unchanged_yaml(tmp_path: Path) -> None:
    base_path, candidate_path = _manifests(tmp_path, "---\n" + BASE)

    change = inspect_deployment_change(
        base_path, candidate_path, namespace="demo", deployment="api"
    )

    assert change.status == "unchanged"
    assert change.supported_changes == []


def test_rejects_ambiguous_target_documents(tmp_path: Path) -> None:
    base_path, candidate_path = _manifests(tmp_path, BASE + "---\n" + BASE)

    with pytest.raises(DeploymentChangeError, match="exactly one"):
        inspect_deployment_change(
            base_path, candidate_path, namespace="demo", deployment="api"
        )


def test_rejects_duplicate_yaml_keys_instead_of_silently_overwriting(
    tmp_path: Path,
) -> None:
    candidate = BASE.replace("  replicas: 2", "  replicas: 2\n  replicas: 3")
    base_path, candidate_path = _manifests(tmp_path, candidate)

    with pytest.raises(DeploymentChangeError, match="could not parse"):
        inspect_deployment_change(
            base_path, candidate_path, namespace="demo", deployment="api"
        )


def test_rejects_yaml_alias_expansion_in_untrusted_pr_input(tmp_path: Path) -> None:
    candidate = BASE.replace("metadata:\n", "metadata: &metadata\n", 1)
    base_path, candidate_path = _manifests(tmp_path, candidate)

    with pytest.raises(DeploymentChangeError, match="anchors and aliases"):
        inspect_deployment_change(
            base_path, candidate_path, namespace="demo", deployment="api"
        )
