"""Read-only local preflight for the disabled, disposable EKS pilot."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


class EksPilotPreflightError(ValueError):
    """A local plan or rendered manifest violates the pilot boundary."""


def validate_rendered_manifests(rendered: str) -> None:
    """Reject persistence or external exposure and require KubeFit metric sources."""
    try:
        documents = [item for item in yaml.safe_load_all(rendered) if isinstance(item, dict)]
    except yaml.YAMLError as exc:
        raise EksPilotPreflightError("Helm output is not valid YAML") from exc

    if not documents:
        raise EksPilotPreflightError("Helm rendered no Kubernetes objects")

    prometheus_objects: list[dict[str, Any]] = []
    monitors: list[dict[str, Any]] = []
    for item in documents:
        kind = item.get("kind")
        if kind in {"PersistentVolume", "PersistentVolumeClaim", "Ingress"}:
            raise EksPilotPreflightError(f"unexpected {kind} in monitoring render")
        if kind == "StatefulSet" and item.get("spec", {}).get("volumeClaimTemplates"):
            raise EksPilotPreflightError("monitoring StatefulSet must not create PVCs")
        if kind == "Service" and item.get("spec", {}).get("type", "ClusterIP") != "ClusterIP":
            raise EksPilotPreflightError("monitoring Service must remain ClusterIP")
        if kind == "Prometheus":
            prometheus_objects.append(item)
        if kind == "ServiceMonitor":
            monitors.append(item)

    if len(prometheus_objects) != 1:
        raise EksPilotPreflightError("expected exactly one Prometheus resource")

    spec = prometheus_objects[0].get("spec", {})
    storage = spec.get("storage", {})
    if storage.get("emptyDir", {}).get("sizeLimit") != "2Gi":
        raise EksPilotPreflightError("Prometheus must use a 2Gi emptyDir")
    if "volumeClaimTemplate" in storage or "ephemeral" in storage:
        raise EksPilotPreflightError("Prometheus must not request persistent storage")
    if spec.get("retention") != "6h" or spec.get("retentionSize") != "1GB":
        raise EksPilotPreflightError("Prometheus retention must match the short pilot")

    if not any(
        endpoint.get("path") == "/metrics/cadvisor"
        for monitor in monitors
        for endpoint in monitor.get("spec", {}).get("endpoints", [])
    ):
        raise EksPilotPreflightError("missing kubelet/cAdvisor ServiceMonitor")
    if not any(
        monitor.get("metadata", {}).get("name", "").endswith("kube-state-metrics")
        for monitor in monitors
    ):
        raise EksPilotPreflightError("missing kube-state-metrics ServiceMonitor")


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EksPilotPreflightError(f"could not run {command[0]}") from exc


def _check_clean_terraform_inputs(terraform_dir: Path) -> None:
    if any(name.startswith("TF_VAR_") for name in os.environ):
        raise EksPilotPreflightError("unset TF_VAR_* before the default-plan preflight")
    if any(name.startswith("TF_CLI_ARGS") for name in os.environ) or os.environ.get(
        "TF_DATA_DIR"
    ):
        raise EksPilotPreflightError("unset Terraform CLI/data overrides for this preflight")
    if os.environ.get("TF_WORKSPACE", "default") != "default":
        raise EksPilotPreflightError("use the default Terraform workspace for this preflight")
    if any(terraform_dir.glob("*.auto.tfvars*")) or any(terraform_dir.glob("terraform.tfvars*")):
        raise EksPilotPreflightError("remove auto-loaded tfvars from this preflight directory")
    if any(terraform_dir.glob("*.tfstate*")):
        raise EksPilotPreflightError("this preflight requires an unused local state directory")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    terraform_dir = root / "deploy" / "eks-pilot"
    try:
        _check_clean_terraform_inputs(terraform_dir)
        workspace = _run(["terraform", "workspace", "show"], cwd=terraform_dir)
        if workspace.returncode != 0 or workspace.stdout.strip() != "default":
            raise EksPilotPreflightError("use the default Terraform workspace")
        for command in (["terraform", "fmt", "-check"], ["terraform", "validate", "-no-color"]):
            result = _run(command, cwd=terraform_dir)
            if result.returncode != 0:
                raise EksPilotPreflightError(
                    f"{command[1]} failed; run terraform init and inspect locally"
                )

        result = _run(
            [
                "terraform",
                "plan",
                "-detailed-exitcode",
                "-input=false",
                "-refresh=false",
                "-lock=false",
                "-no-color",
            ],
            cwd=terraform_dir,
        )
        if result.returncode != 0:
            raise EksPilotPreflightError(
                "default Terraform plan is not empty or could not be evaluated"
            )

        result = _run(
            [
                "helm",
                "template",
                "monitoring",
                "prometheus-community/kube-prometheus-stack",
                "--version",
                "88.5.0",
                "--namespace",
                "monitoring",
                "--kube-version",
                "1.34.0",
                "--values",
                str(terraform_dir / "prometheus-values.yaml"),
            ],
            cwd=root,
        )
        if result.returncode != 0:
            raise EksPilotPreflightError("Helm render failed; check the chart repository locally")
        validate_rendered_manifests(result.stdout)
    except EksPilotPreflightError as exc:
        print(f"EKS pilot preflight: FAIL — {exc}", file=sys.stderr)
        return 1

    print("EKS pilot preflight: PASS — default plan has 0 changes")
    print("Monitoring render is ephemeral and internal-only.")
    print("No AWS resources were created. Live EKS target health remains unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
