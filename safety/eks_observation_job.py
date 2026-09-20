"""Verify a completed EKS pilot k6 Job without mistaking partial logs for a full run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

EXPECTED_IMAGE = (
    "docker.io/grafana/k6:2.1.0@"
    "sha256:65c920dc067d5e2e00befbf982af6ad6ad0117034e8b1c65817c7975c52d4669"
)
EXPECTED_PROFILE = "kubefit-observation-demo-v1"
EXPECTED_REQUESTS = 5 * 600 + 25 * 2100 + 100 * 300 + 25 * 600
EXPECTED_SCRIPT_SHA256 = "1e2e6ae0cc7e7f434cf981b8a95970d7e51b519d651dbb4c862dbd1a00d2a816"
EXPECTED_TARGET = "http://overprovisioned-api.kubefit-demo.svc.cluster.local/"


class ObservationJobEvidenceError(ValueError):
    """The supplied Job, Pod, or k6 log does not prove a complete load profile."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ObservationJobEvidenceError(reason)


def _timestamp(value: Any, field: str) -> datetime:
    _require(isinstance(value, str), f"{field} is missing")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObservationJobEvidenceError(f"{field} is not an RFC3339 timestamp") from exc
    _require(timestamp.tzinfo is not None, f"{field} lacks a timezone")
    return timestamp


def _summary(log: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    matches: list[dict[str, Any]] = []
    for index, character in enumerate(log):
        if character != "{":
            continue
        try:
            payload, end = decoder.raw_decode(log[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("profile_version") == EXPECTED_PROFILE:
            _require(not log[index + end :].strip(), "unexpected output after k6 summary")
            matches.append(payload)
    _require(len(matches) == 1, "expected exactly one complete k6 profile summary")
    return matches[0]


def verify_observation_job(
    job: dict[str, Any], pod_list: dict[str, Any], configmap: dict[str, Any], log: str
) -> dict[str, Any]:
    """Return bounded load evidence; this does not establish KubeFit recommendation readiness."""
    _require(isinstance(job, dict), "Job JSON must be an object")
    _require(isinstance(pod_list, dict), "Pod list JSON must be an object")
    _require(isinstance(configmap, dict), "ConfigMap JSON must be an object")
    _require(isinstance(log, str), "k6 log must be text")
    config_meta = configmap.get("metadata") or {}
    config_data = configmap.get("data") or {}
    _require(
        isinstance(config_meta, dict) and isinstance(config_data, dict),
        "ConfigMap metadata and data must be objects",
    )
    _require(config_meta.get("name") == "kubefit-observation-profile", "unexpected ConfigMap name")
    _require(config_meta.get("namespace") == "kubefit-demo", "unexpected ConfigMap namespace")
    _require(configmap.get("immutable") is True, "profile ConfigMap is not immutable")
    script = config_data.get("observation_profile.js")
    _require(isinstance(script, str), "profile script is missing from ConfigMap")
    script_sha = hashlib.sha256(script.encode("utf-8")).hexdigest()
    _require(script_sha == EXPECTED_SCRIPT_SHA256, "profile script checksum differs")
    metadata = job.get("metadata") or {}
    spec = job.get("spec") or {}
    status = job.get("status") or {}
    _require(
        all(isinstance(item, dict) for item in (metadata, spec, status)),
        "Job metadata, spec, and status must be objects",
    )
    _require(metadata.get("name") == "kubefit-observation", "unexpected Job name")
    _require(metadata.get("namespace") == "kubefit-demo", "unexpected Job namespace")
    _require(bool(metadata.get("uid")), "Job UID is missing")
    _require(spec.get("completions") == spec.get("parallelism") == 1, "Job is not single-run")
    _require(spec.get("backoffLimit") == 0, "Job allows retry")
    _require(spec.get("activeDeadlineSeconds") == 3900, "Job deadline changed")
    template = spec.get("template") or {}
    _require(isinstance(template, dict), "Job Pod template is invalid")
    template_spec = template.get("spec") or {}
    _require(isinstance(template_spec, dict), "Job Pod template spec is invalid")
    _require(
        template_spec.get("restartPolicy") == "Never",
        "Job Pod restart policy changed",
    )
    _require(status.get("succeeded") == 1, "Job has not succeeded exactly once")
    _require(status.get("failed", 0) == 0, "Job has a failed Pod")
    _require(status.get("active", 0) == 0, "Job still has an active Pod")
    conditions = status.get("conditions") or []
    _require(isinstance(conditions, list), "Job conditions must be a list")
    _require(all(isinstance(item, dict) for item in conditions), "Job condition is invalid")
    _require(
        any(item.get("type") == "Complete" and item.get("status") == "True" for item in conditions),
        "Job Complete condition is missing",
    )
    _require(
        not any(
            item.get("type") == "Failed" and item.get("status") == "True"
            for item in conditions
        ),
        "Job has a Failed condition",
    )

    items = pod_list.get("items") if isinstance(pod_list, dict) else None
    _require(isinstance(items, list) and len(items) == 1, "expected exactly one Job Pod")
    pod = items[0]
    _require(isinstance(pod, dict), "Job Pod is invalid")
    pod_meta = pod.get("metadata") or {}
    _require(isinstance(pod_meta, dict), "Job Pod metadata is invalid")
    _require(pod_meta.get("namespace") == "kubefit-demo", "Job Pod namespace differs")
    _require(bool(pod_meta.get("uid")), "Job Pod UID is missing")
    owners = pod_meta.get("ownerReferences") or []
    _require(isinstance(owners, list), "Job Pod owners are invalid")
    _require(all(isinstance(owner, dict) for owner in owners), "Job Pod owner is invalid")
    _require(
        any(
            owner.get("kind") == "Job" and owner.get("uid") == metadata["uid"]
            for owner in owners
        ),
        "Pod is not owned by this Job UID",
    )
    pod_spec = pod.get("spec") or {}
    _require(isinstance(pod_spec, dict), "Job Pod spec is invalid")
    volumes = pod_spec.get("volumes") or []
    _require(
        isinstance(volumes, list)
        and any(
            isinstance(volume, dict)
            and volume.get("name") == "script"
            and isinstance(volume.get("configMap"), dict)
            and volume["configMap"].get("name") == "kubefit-observation-profile"
            for volume in volumes
        ),
        "Job Pod does not mount the fixed profile ConfigMap",
    )
    containers = pod_spec.get("containers") or []
    _require(
        isinstance(containers, list)
        and len(containers) == 1
        and isinstance(containers[0], dict)
        and containers[0].get("name") == "k6"
        and containers[0].get("image") == EXPECTED_IMAGE
        and containers[0].get("args") == ["run", "--quiet", "/scripts/observation_profile.js"]
        and containers[0].get("env") == [
            {"name": "KUBEFIT_TARGET_URL", "value": EXPECTED_TARGET}
        ],
        "Job Pod image, command, or target differs from the fixed profile",
    )
    pod_status = pod.get("status") or {}
    _require(isinstance(pod_status, dict), "Job Pod status is invalid")
    _require(pod_status.get("phase") == "Succeeded", "Job Pod did not succeed")
    container_statuses = pod_status.get("containerStatuses") or []
    _require(
        isinstance(container_statuses, list)
        and len(container_statuses) == 1
        and isinstance(container_statuses[0], dict),
        "expected one k6 container status",
    )
    runner = container_statuses[0]
    _require(runner.get("name") == "k6", "unexpected container status")
    _require(runner.get("restartCount") == 0, "k6 container restarted")
    _require(bool(runner.get("imageID")), "k6 image identity is missing")
    runner_state = runner.get("state") or {}
    _require(isinstance(runner_state, dict), "k6 container state is invalid")
    terminated = runner_state.get("terminated") or {}
    _require(isinstance(terminated, dict), "k6 termination status is invalid")
    _require(terminated.get("exitCode") == 0, "k6 did not exit successfully")
    start = _timestamp(terminated.get("startedAt"), "container startedAt")
    finish = _timestamp(terminated.get("finishedAt"), "container finishedAt")
    duration = (finish - start).total_seconds()
    _require(3590 <= duration <= 3900, "k6 container did not run for the full bounded hour")

    summary = _summary(log)
    _require(
        type(summary.get("schema_version")) is int and summary["schema_version"] == 1,
        "unexpected k6 summary schema",
    )
    _require(
        type(summary.get("duration_minutes")) is int and summary["duration_minutes"] == 60,
        "unexpected configured profile duration",
    )
    requests = summary.get("requests")
    _require(
        type(requests) is int and int(EXPECTED_REQUESTS * 0.99) <= requests <= 102_000,
        "k6 request count is inconsistent with the fixed profile",
    )
    _require(
        type(summary.get("dropped_iterations")) is int
        and summary["dropped_iterations"] == 0,
        "k6 dropped iterations",
    )
    error_rate = summary.get("error_rate")
    _require(
        type(error_rate) in {int, float}
        and math.isfinite(error_rate)
        and 0 <= error_rate < 0.01,
        "k6 request error rate failed the profile threshold",
    )
    return {
        "verification": "load_profile_complete",
        "job_uid": metadata["uid"],
        "pod_uid": pod_meta.get("uid"),
        "profile_script_sha256": script_sha,
        "container_duration_seconds": duration,
        "requests": requests,
        "dropped_iterations": 0,
        "error_rate": error_rate,
        "note": "Load completion does not imply KubeFit readiness or AWS savings.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-json", required=True, type=Path)
    parser.add_argument("--pods-json", required=True, type=Path)
    parser.add_argument("--configmap-json", required=True, type=Path)
    parser.add_argument("--k6-log", required=True, type=Path)
    args = parser.parse_args()
    try:
        job = json.loads(args.job_json.read_text(encoding="utf-8"))
        pods = json.loads(args.pods_json.read_text(encoding="utf-8"))
        configmap = json.loads(args.configmap_json.read_text(encoding="utf-8"))
        log = args.k6_log.read_text(encoding="utf-8")
        result = verify_observation_job(job, pods, configmap, log)
    except (OSError, json.JSONDecodeError, ObservationJobEvidenceError) as exc:
        print(f"EKS observation Job evidence: FAIL — {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
