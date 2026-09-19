export type RiskLevel = "low" | "medium" | "high" | "unknown";
export type CheckStatus = "pass" | "warning" | "block";
export type DecimalValue = number | string;

export interface EvaluationRequest {
  current: {
    cpu_request_millicores: number;
    cpu_limit_millicores: number;
    memory_request_mib: number;
    memory_limit_mib: number;
  };
  observed: {
    cpu_p95_millicores: number;
    memory_p99_mib: number;
    cpu_max_millicores?: number;
    memory_max_mib?: number;
    observation_days: number;
    sample_count?: number;
    observation_coverage?: number;
    desired_replicas?: number;
    available_replicas?: number;
    observed_replicas?: number;
    metric_pod_count?: number;
    cpu_throttling_p95_percent?: number;
    cpu_throttling_max_percent?: number;
    cpu_throttling_sample_count?: number;
    cpu_throttling_pod_count?: number;
    cpu_throttling_observation_coverage?: number;
    container_status_count?: number;
    restart_count?: number;
    oom_killed_count?: number;
  };
  cost_assumptions: {
    currency: "USD";
    cpu_core_hour_usd: number;
    memory_gib_hour_usd: number;
    monthly_hours: number;
    price_source: string;
  };
  replica_count: number;
}

export interface Resources {
  cpu_request_millicores: number;
  cpu_limit_millicores: number;
  memory_request_mib: number;
  memory_limit_mib: number;
}

export interface EvaluationResult {
  current: Resources;
  recommendation: {
    recommended: Resources;
    cpu_request_change_percent: number;
    memory_request_change_percent: number;
    readiness: {
      status: "ready" | "insufficient_data";
      reasons: string[];
    };
    risk: {
      oom: RiskLevel;
      cpu_throttling: RiskLevel;
      reasons: string[];
    };
    evidence: string[];
  };
  cost: {
    assumptions: {
      currency: "USD";
      cpu_core_hour_usd: DecimalValue;
      memory_gib_hour_usd: DecimalValue;
      monthly_hours: DecimalValue;
      price_source: string;
    };
    replica_count: number;
    basis: "resource_requests";
    current: { cpu_usd: DecimalValue; memory_usd: DecimalValue; total_usd: DecimalValue };
    recommended: { cpu_usd: DecimalValue; memory_usd: DecimalValue; total_usd: DecimalValue };
    monthly_delta_usd: DecimalValue;
    savings_percent: DecimalValue;
    caveats: string[];
  };
  patch_eligibility: {
    status: "eligible" | "blocked";
    checks: Array<{ code: string; status: CheckStatus; reason: string }>;
    blocking_reasons: string[];
    warnings: string[];
  };
}

export interface AnalysisReview {
  schema_version: 1;
  artifact_schema_version: 1 | 2;
  verification_level: "integrity_only" | "recommendation_replayed";
  target: {
    namespace: string;
    deployment: string;
    container: string;
  };
  workload_uid: string;
  workload_created_at: string;
  evaluation: EvaluationResult;
  checks: Array<{
    code: "resource_values" | "request_changes" | "cost_comparison" | "patch_eligibility" | "recommendation_replay";
    status: "pass";
    reason: string;
  }>;
  limitations: string[];
  observation_source?: {
    verification: "operator_declared";
    cluster_label: string;
    metrics_source_label: string;
  };
}

export interface BenchmarkPhaseMetrics {
  expected_iterations: number;
  completed_iterations: number;
  requests: number;
  error_rate: number;
  latency_p95_ms: number;
  latency_p99_ms: number;
}

export interface BenchmarkMeasurement {
  schema_version: 1;
  profile_version: string;
  proposal_id: string;
  variant: "before" | "after";
  dropped_iterations: number;
  steady: BenchmarkPhaseMetrics;
  spike: BenchmarkPhaseMetrics;
  recovery: BenchmarkPhaseMetrics;
  runtime: {
    cpu_throttling_p95_percent: number;
    oom_killed_count: number;
    restart_count: number;
    traffic_spike_recovery_seconds: number;
    traffic_spike_recovered: boolean;
  };
  provenance: {
    run_started_at: string;
    run_finished_at: string;
    pods: string[];
    k6_summary_sha256: string;
    k6_raw_sha256: string;
    prometheus_rate_window_seconds: number;
  };
  request_cost_usd: DecimalValue;
}

export interface BenchmarkVerdict {
  status: "pass" | "fail" | "invalid";
  checks: Array<{
    code: string;
    status: "pass" | "fail" | "invalid" | "warning";
    reason: string;
  }>;
  failures: string[];
  invalid_reasons: string[];
  warnings: string[];
  cost_change_percent: DecimalValue | null;
}

export interface BenchmarkReviewRequest {
  result_json: string;
  before_json: string;
  after_json: string;
  verdict_json: string;
}

export type BenchmarkReviewCheckCode =
  | "index_identity"
  | "selected_payload_hashes"
  | "proposal_binding"
  | "raw_evidence_binding"
  | "verdict_replay"
  | "complete_artifact_integrity";

export interface BenchmarkReview {
  schema_version: 1;
  artifact_id: string;
  proposal_id: string;
  verification_level: "index_bound_replay" | "full_artifact_replay";
  before: BenchmarkMeasurement;
  after: BenchmarkMeasurement;
  verdict: BenchmarkVerdict;
  checks: Array<{
    code: BenchmarkReviewCheckCode;
    status: "pass";
    reason: string;
  }>;
  limitations: string[];
}

export type PairMetricDirection = "improved" | "regressed" | "unchanged" | "mixed";

export interface PairMetricTrial {
  benchmark_id: string;
  measurement_order: "before-after" | "after-before";
  before: number;
  after: number;
  delta: number;
  change_percent: number | null;
  direction: Exclude<PairMetricDirection, "mixed">;
}

export interface PairMetricComparison {
  code: string;
  label: string;
  unit: "ms" | "%" | "s";
  lower_is_better: true;
  direction: PairMetricDirection;
  delta_min: number;
  delta_max: number;
  change_percent_min: number | null;
  change_percent_max: number | null;
  trials: [PairMetricTrial, PairMetricTrial];
}

export interface CounterbalancedPairReview {
  schema_version: 1;
  artifact_id: string;
  proposal_id: string;
  verification_level: "pair_full_artifact_replay";
  status: "pass";
  benchmark_ids: [string, string];
  metrics: PairMetricComparison[];
  checks: Array<{
    code: string;
    status: "pass" | "fail" | "invalid" | "warning";
    reason: string;
  }>;
  limitations: string[];
}

export interface BenchmarkCampaignBlockReview {
  block: number;
  pair_id: string;
  status: "pass";
  scheduled_first_order: "before-after" | "after-before";
  observed_first_order: "before-after" | "after-before";
  measurement_started_at: string;
  measurement_finished_at: string;
  benchmark_ids: [string, string];
}

export interface BenchmarkCampaignReview {
  schema_version: 1;
  artifact_id: string;
  campaign_id: string;
  proposal_id: string;
  verification_level: "campaign_full_artifact_replay";
  status: "complete";
  planned_pairs: number;
  completed_pairs: number;
  stopping_rule: "complete_all_planned_pairs";
  aggregation_performed: false;
  blocks: BenchmarkCampaignBlockReview[];
  checks: Array<{
    code: string;
    status: "pass" | "incomplete" | "invalid";
    reason: string;
  }>;
  limitations: string[];
}
