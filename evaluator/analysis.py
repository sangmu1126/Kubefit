from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from evaluator.cost import EvaluationResult, compare_request_costs, evaluate_resources
from evaluator.safety import evaluate_patch_eligibility
from recommender import ObservedUsage, RecommendationPolicy


class AnalysisTarget(BaseModel):
    namespace: str = Field(min_length=1)
    deployment: str = Field(min_length=1)
    container: str = Field(min_length=1)


class ObservationSource(BaseModel):
    verification: Literal["operator_declared"] = "operator_declared"
    cluster_label: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    metrics_source_label: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )


class AnalysisArtifact(BaseModel):
    schema_version: Literal[1, 2] = 1
    target: AnalysisTarget
    workload_uid: str = Field(min_length=1)
    workload_created_at: datetime
    evaluation: EvaluationResult
    observed_usage: ObservedUsage | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    recommendation_policy: "RecommendationPolicySnapshot | None" = Field(
        default=None, exclude_if=lambda value: value is None
    )
    observation_source: ObservationSource | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def creation_time_has_timezone(self) -> "AnalysisArtifact":
        if self.workload_created_at.tzinfo is None:
            raise ValueError("workload creation timestamp must include timezone")
        _validate_evaluation_integrity(self.evaluation)
        if self.schema_version == 1:
            if (
                self.observed_usage is not None
                or self.recommendation_policy is not None
                or self.observation_source is not None
            ):
                raise ValueError("schema v1 must not contain schema v2 replay inputs")
            return self
        if self.observed_usage is None or self.recommendation_policy is None:
            raise ValueError("schema v2 requires observed_usage and recommendation_policy")
        _validate_replay_identity(self)
        _validate_recommendation_replay(self)
        return self


class RecommendationPolicySnapshot(BaseModel):
    algorithm: Literal["resource-recommendation/v1"] = "resource-recommendation/v1"
    safety_margin: float = Field(default=0.25, ge=0)
    cpu_limit_multiplier: float = Field(default=2.0, ge=1)
    memory_limit_multiplier: float = Field(default=1.5, ge=1)
    cpu_step_millicores: int = Field(default=10, ge=1)
    memory_step_mib: int = Field(default=16, ge=1)
    minimum_cpu_millicores: int = Field(default=10, ge=1)
    minimum_memory_mib: int = Field(default=32, ge=1)
    minimum_observation_coverage: float = Field(default=0.7, ge=0, le=1)
    minimum_sample_count: int = Field(default=100, ge=1)

    @classmethod
    def from_policy(
        cls, policy: RecommendationPolicy | None = None
    ) -> "RecommendationPolicySnapshot":
        selected = policy or RecommendationPolicy()
        return cls(
            safety_margin=selected.safety_margin,
            cpu_limit_multiplier=selected.cpu_limit_multiplier,
            memory_limit_multiplier=selected.memory_limit_multiplier,
            cpu_step_millicores=selected.cpu_step_millicores,
            memory_step_mib=selected.memory_step_mib,
            minimum_cpu_millicores=selected.minimum_cpu_millicores,
            minimum_memory_mib=selected.minimum_memory_mib,
            minimum_observation_coverage=selected.minimum_observation_coverage,
            minimum_sample_count=selected.minimum_sample_count,
        )

    def to_policy(self) -> RecommendationPolicy:
        values = self.model_dump(exclude={"algorithm"})
        return RecommendationPolicy(**values)


class AnalysisIntegrityCheck(BaseModel):
    code: Literal[
        "resource_values",
        "request_changes",
        "cost_comparison",
        "patch_eligibility",
        "recommendation_replay",
    ]
    status: Literal["pass"] = "pass"
    reason: str


class AnalysisReview(BaseModel):
    schema_version: Literal[1] = 1
    artifact_schema_version: Literal[1, 2]
    verification_level: Literal["integrity_only", "recommendation_replayed"]
    target: AnalysisTarget
    workload_uid: str
    workload_created_at: datetime
    evaluation: EvaluationResult
    checks: list[AnalysisIntegrityCheck]
    limitations: list[str]
    observation_source: ObservationSource | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def review_analysis_artifact(artifact: AnalysisArtifact) -> AnalysisReview:
    replayed = artifact.schema_version == 2
    checks = [
        AnalysisIntegrityCheck(
            code="resource_values",
            reason=(
                "recommended requests and limits are positive and limits are not below requests"
            ),
        ),
        AnalysisIntegrityCheck(
            code="request_changes",
            reason="request change percentages match current and recommended resources",
        ),
        AnalysisIntegrityCheck(
            code="cost_comparison",
            reason="cost comparison was recomputed from resources and stored assumptions",
        ),
        AnalysisIntegrityCheck(
            code="patch_eligibility",
            reason="patch eligibility was recomputed from recommendation readiness and risk",
        ),
    ]
    limitations = [
        (
            "integrity validation does not authenticate the artifact producer or "
            "bind the artifact to repository bytes"
        )
    ]
    if replayed:
        checks.append(
            AnalysisIntegrityCheck(
                code="recommendation_replay",
                reason=(
                    "recommendation was replayed from retained observed usage and "
                    "resource-recommendation/v1 policy inputs"
                ),
            )
        )
        limitations.insert(
            0,
            (
                "schema v2 retains percentile summaries, not raw Prometheus time series, "
                "so percentile aggregation cannot be replayed"
            ),
        )
        if artifact.observation_source is not None:
            limitations.append(
                "observation source labels are operator-declared; artifact replay does "
                "not authenticate the Kubernetes cluster or Prometheus endpoint"
            )
    else:
        limitations.insert(
            0,
            (
                "schema v1 does not retain observed usage or policy inputs, so the "
                "recommendation cannot be replayed"
            ),
        )
    return AnalysisReview(
        artifact_schema_version=artifact.schema_version,
        verification_level=("recommendation_replayed" if replayed else "integrity_only"),
        target=artifact.target,
        workload_uid=artifact.workload_uid,
        workload_created_at=artifact.workload_created_at,
        evaluation=artifact.evaluation,
        checks=checks,
        limitations=limitations,
        observation_source=artifact.observation_source,
    )


def _validate_replay_identity(artifact: AnalysisArtifact) -> None:
    observed = artifact.observed_usage
    assert observed is not None
    if observed.workload_uid != artifact.workload_uid:
        raise ValueError("observed usage workload UID conflicts with artifact identity")
    if observed.workload_created_at != artifact.workload_created_at:
        raise ValueError("observed usage creation timestamp conflicts with artifact identity")
    if observed.desired_replicas != artifact.evaluation.cost.replica_count:
        raise ValueError("observed desired replicas conflict with cost replica count")


def _validate_recommendation_replay(artifact: AnalysisArtifact) -> None:
    observed = artifact.observed_usage
    policy = artifact.recommendation_policy
    assert observed is not None and policy is not None
    expected = evaluate_resources(
        artifact.evaluation.current,
        observed,
        artifact.evaluation.cost.assumptions,
        artifact.evaluation.cost.replica_count,
        policy.to_policy(),
    )
    if artifact.evaluation != expected:
        raise ValueError("evaluation conflicts with replayed observation and recommendation policy")


def _validate_evaluation_integrity(evaluation: EvaluationResult) -> None:
    recommended = evaluation.recommendation.recommended
    values = (
        recommended.cpu_request_millicores,
        recommended.cpu_limit_millicores,
        recommended.memory_request_mib,
        recommended.memory_limit_mib,
    )
    if any(value <= 0 for value in values):
        raise ValueError("recommended resources must be positive")
    if recommended.cpu_limit_millicores < recommended.cpu_request_millicores:
        raise ValueError("recommended CPU limit must not be below its request")
    if recommended.memory_limit_mib < recommended.memory_request_mib:
        raise ValueError("recommended memory limit must not be below its request")

    expected_cpu_change = round(
        (recommended.cpu_request_millicores / evaluation.current.cpu_request_millicores - 1) * 100,
        1,
    )
    expected_memory_change = round(
        (recommended.memory_request_mib / evaluation.current.memory_request_mib - 1) * 100,
        1,
    )
    if evaluation.recommendation.cpu_request_change_percent != expected_cpu_change:
        raise ValueError("CPU request change percentage conflicts with resources")
    if evaluation.recommendation.memory_request_change_percent != expected_memory_change:
        raise ValueError("memory request change percentage conflicts with resources")

    expected_cost = compare_request_costs(
        evaluation.current,
        recommended,
        evaluation.cost.assumptions,
        evaluation.cost.replica_count,
    )
    if evaluation.cost != expected_cost:
        raise ValueError("cost comparison conflicts with resources or assumptions")

    expected_eligibility = evaluate_patch_eligibility(evaluation.recommendation)
    if evaluation.patch_eligibility != expected_eligibility:
        raise ValueError("patch eligibility conflicts with recommendation evidence")
