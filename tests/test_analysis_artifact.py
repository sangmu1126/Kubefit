from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evaluator import (
    AnalysisArtifact,
    AnalysisTarget,
    CostAssumptions,
    ObservationSource,
    RecommendationPolicySnapshot,
    evaluate_resources,
    review_analysis_artifact,
)
from recommender import ObservedUsage
from tests.test_manifest import eligible_evaluation


def replayable_analysis() -> AnalysisArtifact:
    identity_time = datetime(2026, 8, 21, tzinfo=UTC)
    observed = ObservedUsage(
        cpu_p95_millicores=230,
        memory_p99_mib=710,
        cpu_max_millicores=400,
        memory_max_mib=900,
        observation_days=7,
        step_seconds=300,
        sample_count=1900,
        observation_coverage=0.95,
        desired_replicas=2,
        available_replicas=2,
        observed_replicas=2,
        metric_pod_count=2,
        workload_uid="deployment-uid",
        workload_created_at=identity_time,
        history_clipped=True,
        authorized_replica_set_count=1,
        identity_snapshot_enabled=True,
        cpu_throttling_p95_percent=0.2,
        cpu_throttling_max_percent=0.5,
        cpu_throttling_sample_count=1900,
        cpu_throttling_pod_count=2,
        cpu_throttling_observation_coverage=0.95,
        container_status_count=2,
        restart_count=0,
        oom_killed_count=0,
    )
    baseline = eligible_evaluation()
    policy = RecommendationPolicySnapshot.from_policy()
    evaluation = evaluate_resources(
        baseline.current,
        observed,
        CostAssumptions(
            cpu_core_hour_usd="0.04",
            memory_gib_hour_usd="0.005",
            price_source="example://test",
        ),
        2,
        policy.to_policy(),
    )
    return AnalysisArtifact(
        schema_version=2,
        target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=identity_time,
        evaluation=evaluation,
        observed_usage=observed,
        recommendation_policy=policy,
    )


def test_binds_evaluation_to_workload_identity() -> None:
    result = AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    )

    restored = AnalysisArtifact.model_validate_json(result.model_dump_json())

    assert restored.target.deployment == "api"
    assert restored.workload_uid == "deployment-uid"
    assert restored.evaluation == result.evaluation
    assert "observed_usage" not in result.model_dump()
    assert "recommendation_policy" not in result.model_dump()


def test_rejects_naive_workload_creation_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        AnalysisArtifact(
            target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
            workload_uid="deployment-uid",
            workload_created_at=datetime(2026, 8, 21),
            evaluation=eligible_evaluation(),
        )


def test_review_recomputes_integrity_checks_and_states_v1_limitations() -> None:
    artifact = AnalysisArtifact(
        target=AnalysisTarget(namespace="demo", deployment="api", container="api"),
        workload_uid="deployment-uid",
        workload_created_at=datetime(2026, 8, 21, tzinfo=UTC),
        evaluation=eligible_evaluation(),
    )

    review = review_analysis_artifact(artifact)

    assert review.verification_level == "integrity_only"
    assert [check.code for check in review.checks] == [
        "resource_values",
        "request_changes",
        "cost_comparison",
        "patch_eligibility",
    ]
    assert all(check.status == "pass" for check in review.checks)
    assert "does not retain observed usage" in review.limitations[0]


def test_v2_replays_recommendation_from_retained_inputs() -> None:
    artifact = replayable_analysis()

    restored = AnalysisArtifact.model_validate_json(artifact.model_dump_json())
    review = review_analysis_artifact(restored)

    assert review.artifact_schema_version == 2
    assert review.verification_level == "recommendation_replayed"
    assert review.checks[-1].code == "recommendation_replay"
    assert "raw Prometheus time series" in review.limitations[0]


def test_v2_source_declaration_survives_review_without_claiming_authentication() -> None:
    artifact = replayable_analysis().model_copy(
        update={
            "observation_source": ObservationSource(
                cluster_label="eks-seoul-pilot",
                metrics_source_label="incluster-prometheus",
            )
        }
    )

    restored = AnalysisArtifact.model_validate_json(artifact.model_dump_json())
    review = review_analysis_artifact(restored)

    assert review.observation_source == artifact.observation_source
    assert review.observation_source.verification == "operator_declared"
    assert any("does not authenticate" in item for item in review.limitations)
    assert "observation_source" not in replayable_analysis().model_dump()


@pytest.mark.parametrize("label", ["https://private.example", "token=value", "bad label"])
def test_source_declaration_rejects_url_or_credential_like_labels(label: str) -> None:
    with pytest.raises(ValidationError):
        ObservationSource(cluster_label=label, metrics_source_label="prometheus")


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("observed_usage", "cpu_p95_millicores"), 400, "replayed observation"),
        (("recommendation_policy", "safety_margin"), 0.5, "replayed observation"),
        (("observed_usage", "workload_uid"), "other-uid", "workload UID"),
    ],
)
def test_v2_rejects_replay_input_tampering(
    path: tuple[str, str], value: object, message: str
) -> None:
    artifact = replayable_analysis().model_dump(mode="json")
    artifact[path[0]][path[1]] = value

    with pytest.raises(ValidationError, match=message):
        AnalysisArtifact.model_validate(artifact)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("recommendation", "cpu_request_change_percent"), -99.0, "change percentage"),
        (("cost", "savings_percent"), "99.9", "cost comparison"),
        (("patch_eligibility", "status"), "blocked", "patch eligibility"),
    ],
)
def test_rejects_internally_conflicting_evaluation(
    path: tuple[str, str], value: object, message: str
) -> None:
    evaluation = eligible_evaluation().model_dump(mode="json")
    evaluation[path[0]][path[1]] = value

    with pytest.raises(ValidationError, match=message):
        AnalysisArtifact.model_validate(
            {
                "target": {"namespace": "demo", "deployment": "api", "container": "api"},
                "workload_uid": "deployment-uid",
                "workload_created_at": "2026-08-21T00:00:00Z",
                "evaluation": evaluation,
            }
        )
