from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from safety.eks_pilot_preflight import (
    EksPilotPreflightError,
    _check_clean_terraform_inputs,
    validate_rendered_manifests,
)


def _documents() -> list[dict]:
    return [
        {
            "kind": "Prometheus",
            "spec": {
                "storage": {"emptyDir": {"sizeLimit": "2Gi"}},
                "retention": "6h",
                "retentionSize": "1GB",
            },
        },
        {"kind": "Service", "spec": {"type": "ClusterIP"}},
        {
            "kind": "ServiceMonitor",
            "metadata": {"name": "monitoring-kube-prometheus-kubelet"},
            "spec": {"endpoints": [{"path": "/metrics/cadvisor"}]},
        },
        {
            "kind": "ServiceMonitor",
            "metadata": {"name": "monitoring-kube-state-metrics"},
            "spec": {"endpoints": [{}]},
        },
    ]


def _render(documents: list[dict]) -> str:
    return yaml.safe_dump_all(documents)


def test_accepts_ephemeral_internal_monitoring() -> None:
    validate_rendered_manifests(_render(_documents()))


@pytest.mark.parametrize("kind", ["PersistentVolume", "PersistentVolumeClaim", "Ingress"])
def test_rejects_unexpected_billable_or_exposed_object(kind: str) -> None:
    documents = _documents() + [{"kind": kind}]
    with pytest.raises(EksPilotPreflightError, match=kind):
        validate_rendered_manifests(_render(documents))


def test_rejects_load_balancer_service() -> None:
    documents = _documents()
    documents[1]["spec"]["type"] = "LoadBalancer"
    with pytest.raises(EksPilotPreflightError, match="ClusterIP"):
        validate_rendered_manifests(_render(documents))


def test_rejects_prometheus_pvc_even_without_separate_pvc_object() -> None:
    documents = _documents()
    documents[0]["spec"]["storage"]["volumeClaimTemplate"] = {"spec": {}}
    with pytest.raises(EksPilotPreflightError, match="persistent storage"):
        validate_rendered_manifests(_render(documents))


def test_rejects_statefulset_pvc_template() -> None:
    documents = _documents() + [
        {"kind": "StatefulSet", "spec": {"volumeClaimTemplates": [{"metadata": {"name": "data"}}]}}
    ]
    with pytest.raises(EksPilotPreflightError, match="StatefulSet"):
        validate_rendered_manifests(_render(documents))


@pytest.mark.parametrize("index", [2, 3])
def test_rejects_missing_required_scrape_monitor(index: int) -> None:
    documents = deepcopy(_documents())
    del documents[index]
    with pytest.raises(EksPilotPreflightError, match="ServiceMonitor"):
        validate_rendered_manifests(_render(documents))


def test_refuses_environment_variable_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TF_VAR_enable_experiment", "true")
    with pytest.raises(EksPilotPreflightError, match="TF_VAR"):
        _check_clean_terraform_inputs(tmp_path)


@pytest.mark.parametrize("filename", ["terraform.auto.tfvars", "terraform.tfstate"])
def test_refuses_auto_input_or_existing_state(tmp_path: Path, filename: str) -> None:
    (tmp_path / filename).touch()
    with pytest.raises(EksPilotPreflightError, match="tfvars|state"):
        _check_clean_terraform_inputs(tmp_path)
