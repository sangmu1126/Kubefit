from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from safety.eks_observation_job import (
    EXPECTED_IMAGE,
    EXPECTED_SCRIPT_SHA256,
    ObservationJobEvidenceError,
    verify_observation_job,
)


def _evidence() -> tuple[dict, dict, dict, str]:
    script_path = Path(__file__).resolve().parents[1] / "benchmarks/k6/observation_profile.js"
    configmap = {
        "metadata": {"name": "kubefit-observation-profile", "namespace": "kubefit-demo"},
        "immutable": True,
        "data": {"observation_profile.js": script_path.read_text(encoding="utf-8")},
    }
    job = {
        "metadata": {"name": "kubefit-observation", "namespace": "kubefit-demo", "uid": "job-1"},
        "spec": {
            "completions": 1,
            "parallelism": 1,
            "backoffLimit": 0,
            "activeDeadlineSeconds": 3900,
            "template": {"spec": {"restartPolicy": "Never"}},
        },
        "status": {
            "succeeded": 1,
            "conditions": [{"type": "Complete", "status": "True"}],
        },
    }
    pods = {
        "items": [
            {
                "metadata": {
                    "namespace": "kubefit-demo",
                    "uid": "pod-1",
                    "ownerReferences": [{"kind": "Job", "uid": "job-1"}],
                },
                "spec": {
                    "volumes": [
                        {"name": "script", "configMap": {"name": "kubefit-observation-profile"}}
                    ],
                    "containers": [
                        {
                            "name": "k6",
                            "image": EXPECTED_IMAGE,
                            "args": ["run", "--quiet", "/scripts/observation_profile.js"],
                            "env": [
                                {
                                    "name": "KUBEFIT_TARGET_URL",
                                    "value": "http://overprovisioned-api.kubefit-demo.svc.cluster.local/",
                                }
                            ],
                        }
                    ],
                },
                "status": {
                    "phase": "Succeeded",
                    "containerStatuses": [
                        {
                            "name": "k6",
                            "restartCount": 0,
                            "imageID": "docker.io/grafana/k6@sha256:example",
                            "state": {
                                "terminated": {
                                    "exitCode": 0,
                                    "startedAt": "2026-09-21T00:00:00Z",
                                    "finishedAt": "2026-09-21T01:00:01Z",
                                }
                            },
                        }
                    ],
                },
            }
        ]
    }
    log = json.dumps(
        {
            "schema_version": 1,
            "profile_version": "kubefit-observation-demo-v1",
            "duration_minutes": 60,
            "requests": 100_500,
            "dropped_iterations": 0,
            "error_rate": 0,
        },
        indent=2,
    )
    return job, pods, configmap, log


def test_accepts_completed_full_hour_without_claiming_recommendation() -> None:
    job, pods, configmap, log = _evidence()
    result = verify_observation_job(job, pods, configmap, log)
    assert result["verification"] == "load_profile_complete"
    assert result["container_duration_seconds"] == 3601
    assert result["requests"] == 100_500
    assert result["profile_script_sha256"] == EXPECTED_SCRIPT_SHA256
    assert "does not imply KubeFit readiness" in result["note"]


def test_verifier_image_matches_proposed_job() -> None:
    manifest = Path(__file__).resolve().parents[1] / "deploy/eks-pilot/observation-job.yaml"
    job = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == EXPECTED_IMAGE


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda j, p: j["status"].update(succeeded=0), "succeeded exactly once"),
        (lambda j, p: j["status"].update(failed=1), "failed Pod"),
        (lambda j, p: j["status"].update(conditions=[]), "Complete condition"),
        (lambda j, p: j["spec"].update(backoffLimit=1), "allows retry"),
        (lambda j, p: p["items"].append(deepcopy(p["items"][0])), "exactly one Job Pod"),
        (
            lambda j, p: p["items"][0]["metadata"]["ownerReferences"][0].update(uid="other"),
            "not owned",
        ),
        (
            lambda j, p: p["items"][0]["spec"]["containers"][0].update(image="grafana/k6:latest"),
            "image, command, or target",
        ),
        (
            lambda j, p: p["items"][0]["status"]["containerStatuses"][0].update(
                restartCount=1
            ),
            "restarted",
        ),
        (
            lambda j, p: p["items"][0]["status"]["containerStatuses"][0]["state"][
                "terminated"
            ].update(exitCode=1),
            "exit successfully",
        ),
        (
            lambda j, p: p["items"][0]["status"]["containerStatuses"][0]["state"][
                "terminated"
            ].update(finishedAt="2026-09-21T00:34:47Z"),
            "full bounded hour",
        ),
    ],
)
def test_rejects_incomplete_or_mismatched_job(change, reason: str) -> None:
    job, pods, configmap, log = _evidence()
    change(job, pods)
    with pytest.raises(ObservationJobEvidenceError, match=reason):
        verify_observation_job(job, pods, configmap, log)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("requests", 25_741, "request count"),
        ("dropped_iterations", 51, "dropped iterations"),
        ("error_rate", 0.1189, "error rate"),
        ("duration_minutes", 15, "configured profile duration"),
        ("profile_version", "different", "summary"),
    ],
)
def test_rejects_aborted_or_wrong_k6_summary(field: str, value, reason: str) -> None:
    job, pods, configmap, log = _evidence()
    summary = json.loads(log)
    summary[field] = value
    with pytest.raises(ObservationJobEvidenceError, match=reason):
        verify_observation_job(job, pods, configmap, json.dumps(summary))


def test_rejects_output_after_k6_summary() -> None:
    job, pods, configmap, log = _evidence()
    with pytest.raises(ObservationJobEvidenceError, match="unexpected output"):
        verify_observation_job(job, pods, configmap, log + "\nrun interrupted")


@pytest.mark.parametrize("invalid", [[], "wrong", {"status": []}])
def test_rejects_malformed_job_json(invalid) -> None:
    _, pods, configmap, log = _evidence()
    with pytest.raises(ObservationJobEvidenceError):
        verify_observation_job(invalid, pods, configmap, log)


def test_rejects_boolean_counts_that_equal_zero_or_one() -> None:
    job, pods, configmap, log = _evidence()
    summary = json.loads(log)
    summary["dropped_iterations"] = False
    with pytest.raises(ObservationJobEvidenceError, match="dropped iterations"):
        verify_observation_job(job, pods, configmap, json.dumps(summary))


def test_rejects_mutable_or_changed_profile_script() -> None:
    job, pods, configmap, log = _evidence()
    configmap["immutable"] = False
    with pytest.raises(ObservationJobEvidenceError, match="not immutable"):
        verify_observation_job(job, pods, configmap, log)

    configmap["immutable"] = True
    configmap["data"]["observation_profile.js"] += "\n// altered\n"
    with pytest.raises(ObservationJobEvidenceError, match="checksum differs"):
        verify_observation_job(job, pods, configmap, log)


def test_rejects_changed_pod_target() -> None:
    job, pods, configmap, log = _evidence()
    pods["items"][0]["spec"]["containers"][0]["env"][0]["value"] = "http://another/"
    with pytest.raises(ObservationJobEvidenceError, match="command, or target"):
        verify_observation_job(job, pods, configmap, log)
