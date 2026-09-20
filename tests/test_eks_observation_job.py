"""Keep the proposed EKS load Job bounded and internal-only."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

JOB = Path(__file__).resolve().parents[1] / "deploy/eks-pilot/observation-job.yaml"


def test_observation_job_uses_one_bounded_non_privileged_runner() -> None:
    document = yaml.safe_load(JOB.read_text(encoding="utf-8"))
    assert document["apiVersion"] == "batch/v1"
    assert document["kind"] == "Job"
    assert document["metadata"]["namespace"] == "kubefit-demo"

    job = document["spec"]
    assert job["completions"] == job["parallelism"] == 1
    assert job["backoffLimit"] == 0
    assert 3600 < job["activeDeadlineSeconds"] <= 3900
    assert job["ttlSecondsAfterFinished"] <= 3600

    pod = job["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert len(pod["containers"]) == 1
    assert len(pod["volumes"]) == 1

    runner = pod["containers"][0]
    assert re.fullmatch(r"docker\.io/grafana/k6:2\.1\.0@sha256:[0-9a-f]{64}", runner["image"])
    assert runner["args"] == ["run", "--quiet", "/scripts/observation_profile.js"]
    assert runner["env"] == [
        {
            "name": "KUBEFIT_TARGET_URL",
            "value": "http://overprovisioned-api.kubefit-demo.svc.cluster.local/",
        }
    ]
    assert runner["resources"]["requests"] == {"cpu": "250m", "memory": "256Mi"}
    assert runner["resources"]["limits"] == {"cpu": "1", "memory": "1Gi"}
    assert runner["securityContext"]["allowPrivilegeEscalation"] is False
    assert runner["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert runner["volumeMounts"] == [
        {"name": "script", "mountPath": "/scripts", "readOnly": True}
    ]
    assert pod["volumes"] == [
        {
            "name": "script",
            "configMap": {"name": "kubefit-observation-profile", "defaultMode": 292},
        }
    ]
