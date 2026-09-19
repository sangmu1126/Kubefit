import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { VERIFIED_PAIR_ID } from "./showcase";
import type {
  AnalysisReview,
  BenchmarkCampaignReview,
  BenchmarkReview,
  CounterbalancedPairReview,
  EvaluationResult,
} from "./types";

const result: EvaluationResult = {
  current: {
    cpu_request_millicores: 1000,
    cpu_limit_millicores: 2000,
    memory_request_mib: 2048,
    memory_limit_mib: 4096,
  },
  recommendation: {
    recommended: {
      cpu_request_millicores: 290,
      cpu_limit_millicores: 580,
      memory_request_mib: 896,
      memory_limit_mib: 1344,
    },
    cpu_request_change_percent: -71,
    memory_request_change_percent: -56.3,
    readiness: { status: "ready", reasons: [] },
    risk: {
      oom: "low",
      cpu_throttling: "low",
      reasons: ["safe headroom"],
    },
    evidence: ["CPU request uses 7-day P95 plus 25% safety margin"],
  },
  cost: {
    assumptions: {
      currency: "USD",
      cpu_core_hour_usd: 0.04,
      memory_gib_hour_usd: 0.005,
      monthly_hours: 730,
      price_source: "example://local-model",
    },
    replica_count: 2,
    basis: "resource_requests",
    current: { cpu_usd: "58.400000", memory_usd: "14.600000", total_usd: "73.000000" },
    recommended: { cpu_usd: "16.936000", memory_usd: "6.387500", total_usd: "23.323500" },
    monthly_delta_usd: "-49.676500",
    savings_percent: "68.1",
    caveats: ["request cost is a projection"],
  },
  patch_eligibility: {
    status: "eligible",
    checks: [
      { code: "recommendation_readiness", status: "pass", reason: "evidence is sufficient" },
    ],
    blocking_reasons: [],
    warnings: [],
  },
};

const recordedEvaluationResult: EvaluationResult = {
  ...structuredClone(result),
  recommendation: {
    ...structuredClone(result.recommendation),
    recommended: {
      cpu_request_millicores: 10,
      cpu_limit_millicores: 20,
      memory_request_mib: 32,
      memory_limit_mib: 48,
    },
  },
  cost: {
    ...structuredClone(result.cost),
    recommended: { cpu_usd: "0.584000", memory_usd: "0.228125", total_usd: "0.812125" },
    monthly_delta_usd: "-72.187875",
    savings_percent: "98.9",
  },
};

const artifactReview: AnalysisReview = {
  schema_version: 1,
  artifact_schema_version: 1,
  verification_level: "integrity_only",
  target: { namespace: "payments", deployment: "checkout-api", container: "api" },
  workload_uid: "deployment-uid-1234",
  workload_created_at: "2026-08-21T00:00:00Z",
  evaluation: result,
  checks: [
    { code: "cost_comparison", status: "pass", reason: "cost comparison was recomputed" },
  ],
  limitations: ["schema v1 does not retain raw observed usage"],
};

const benchmarkBefore: BenchmarkReview["before"] = {
    schema_version: 1,
    profile_version: "kubefit-load-v1",
    proposal_id: "proposal-925669808e28e594baeeb442c3d447c8",
    variant: "before",
    dropped_iterations: 0,
    steady: { expected_iterations: 300, completed_iterations: 300, requests: 300, error_rate: 0, latency_p95_ms: 10.3, latency_p99_ms: 11.1 },
    spike: { expected_iterations: 750, completed_iterations: 750, requests: 750, error_rate: 0, latency_p95_ms: 8.1, latency_p99_ms: 8.8 },
    recovery: { expected_iterations: 300, completed_iterations: 300, requests: 300, error_rate: 0, latency_p95_ms: 10, latency_p99_ms: 11 },
    runtime: { cpu_throttling_p95_percent: 0, oom_killed_count: 0, restart_count: 0, traffic_spike_recovery_seconds: 5, traffic_spike_recovered: true },
    provenance: { run_started_at: "2026-08-21T00:00:00Z", run_finished_at: "2026-08-21T00:03:00Z", pods: ["api-before"], k6_summary_sha256: "a".repeat(64), k6_raw_sha256: "b".repeat(64), prometheus_rate_window_seconds: 30 },
    request_cost_usd: "73.000000",
};

const benchmarkReview: BenchmarkReview = {
  schema_version: 1,
  artifact_id: "benchmark-f84d0caf061d50a5d93bc03088eb0247",
  proposal_id: "proposal-925669808e28e594baeeb442c3d447c8",
  verification_level: "index_bound_replay",
  before: benchmarkBefore,
  after: {
    ...structuredClone(benchmarkBefore),
    variant: "after",
    steady: { ...benchmarkBefore.steady, latency_p95_ms: 10, latency_p99_ms: 10.6 },
    spike: { ...benchmarkBefore.spike, latency_p95_ms: 8.2, latency_p99_ms: 9.1 },
    request_cost_usd: "1.395760",
  },
  verdict: {
    status: "pass",
    checks: [{ code: "new_oom_killed", status: "pass", reason: "candidate run observed no OOMKilled events" }],
    failures: [],
    invalid_reasons: [],
    warnings: [],
    cost_change_percent: "-98.088",
  },
  checks: [
    { code: "index_identity", status: "pass", reason: "artifact identity checked" },
    { code: "selected_payload_hashes", status: "pass", reason: "selected payloads checked" },
    { code: "proposal_binding", status: "pass", reason: "proposal checked" },
    { code: "raw_evidence_binding", status: "pass", reason: "raw evidence digests checked" },
    { code: "verdict_replay", status: "pass", reason: "verdict replayed" },
  ],
  limitations: ["k6 raw bytes were not uploaded"],
};

const fullBenchmarkReview: BenchmarkReview = {
  ...benchmarkReview,
  verification_level: "full_artifact_replay",
  checks: [
    {
      code: "complete_artifact_integrity",
      status: "pass",
      reason: "complete artifact bytes and semantics were revalidated",
    },
    { code: "verdict_replay", status: "pass", reason: "verdict replayed" },
  ],
  limitations: ["fixed demo traffic is not representative production traffic"],
};

const pairReview: CounterbalancedPairReview = {
  schema_version: 1,
  artifact_id: "benchmark-pair-" + "c".repeat(32),
  proposal_id: benchmarkReview.proposal_id,
  verification_level: "pair_full_artifact_replay",
  status: "pass",
  benchmark_ids: [benchmarkReview.artifact_id, "benchmark-" + "d".repeat(32)],
  metrics: [
    {
      code: "steady_latency_p95",
      label: "Steady latency P95",
      unit: "ms",
      lower_is_better: true,
      direction: "improved",
      delta_min: -2,
      delta_max: -1,
      change_percent_min: -20,
      change_percent_max: -10,
      trials: [
        {
          benchmark_id: benchmarkReview.artifact_id,
          measurement_order: "before-after",
          before: 10,
          after: 8,
          delta: -2,
          change_percent: -20,
          direction: "improved",
        },
        {
          benchmark_id: "benchmark-" + "d".repeat(32),
          measurement_order: "after-before",
          before: 10,
          after: 9,
          delta: -1,
          change_percent: -10,
          direction: "improved",
        },
      ],
    },
    {
      code: "cpu_throttling_p95",
      label: "CPU throttling P95",
      unit: "%",
      lower_is_better: true,
      direction: "mixed",
      delta_min: -0.2,
      delta_max: 0.1,
      change_percent_min: null,
      change_percent_max: null,
      trials: [
        {
          benchmark_id: benchmarkReview.artifact_id,
          measurement_order: "before-after",
          before: 0,
          after: 0.1,
          delta: 0.1,
          change_percent: null,
          direction: "regressed",
        },
        {
          benchmark_id: "benchmark-" + "d".repeat(32),
          measurement_order: "after-before",
          before: 0.2,
          after: 0,
          delta: -0.2,
          change_percent: -100,
          direction: "improved",
        },
      ],
    },
  ],
  checks: [
    { code: "opposite_orders", status: "pass", reason: "opposite orders verified" },
    { code: "both_trials_pass", status: "pass", reason: "both trials passed" },
  ],
  limitations: [
    "the displayed range is the minimum and maximum of two observed order-specific changes, not a confidence interval",
  ],
};

const campaignReview: BenchmarkCampaignReview = {
  schema_version: 1,
  artifact_id: "benchmark-campaign-evidence-" + "e".repeat(32),
  campaign_id: "benchmark-campaign-" + "f".repeat(32),
  proposal_id: benchmarkReview.proposal_id,
  verification_level: "campaign_full_artifact_replay",
  status: "complete",
  planned_pairs: 3,
  completed_pairs: 3,
  stopping_rule: "complete_all_planned_pairs",
  aggregation_performed: false,
  blocks: [
    {
      block: 1,
      pair_id: pairReview.artifact_id,
      status: "pass",
      scheduled_first_order: "before-after",
      observed_first_order: "before-after",
      measurement_started_at: "2026-08-21T01:00:00Z",
      measurement_finished_at: "2026-08-21T01:30:00Z",
      benchmark_ids: pairReview.benchmark_ids,
    },
    {
      block: 2,
      pair_id: "benchmark-pair-" + "a".repeat(32),
      status: "pass",
      scheduled_first_order: "after-before",
      observed_first_order: "after-before",
      measurement_started_at: "2026-08-21T03:00:00Z",
      measurement_finished_at: "2026-08-21T03:30:00Z",
      benchmark_ids: ["benchmark-" + "1".repeat(32), "benchmark-" + "2".repeat(32)],
    },
    {
      block: 3,
      pair_id: "benchmark-pair-" + "b".repeat(32),
      status: "pass",
      scheduled_first_order: "before-after",
      observed_first_order: "before-after",
      measurement_started_at: "2026-08-21T06:00:00Z",
      measurement_finished_at: "2026-08-21T06:30:00Z",
      benchmark_ids: ["benchmark-" + "3".repeat(32), "benchmark-" + "4".repeat(32)],
    },
  ],
  checks: [
    { code: "fixed_pair_count", status: "pass", reason: "all preregistered pairs are present" },
    { code: "randomized_schedule", status: "pass", reason: "randomized_schedule is valid" },
  ],
  limitations: [
    "completion verifies preregistered collection discipline and does not compute an aggregate treatment effect",
  ],
};

function directoryFile(path: string, content = `{\"path\":\"${path}\"}`): File {
  const file = new File([content], path.split("/").at(-1)!, { type: "application/json" });
  Object.defineProperty(file, "webkitRelativePath", {
    value: `benchmark-result/${path}`,
  });
  return file;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

describe("KubeFit dashboard", () => {
  it("renders an explanation-first empty state", () => {
    render(<App />);
    expect(screen.getByText("아직 계산하지 않았습니다.")).toBeInTheDocument();
    expect(screen.getByLabelText("CPU P95")).toHaveValue(230);
  });

  it("submits the scenario to the evaluation API and renders its decision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(result), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: /추천과 위험 계산/ }));

    expect(await screen.findByText("패치 제안 가능")).toBeInTheDocument();
    expect(screen.getByText("68.1%")).toBeInTheDocument();
    expect(screen.getByText("CPU request uses 7-day P95 plus 25% safety margin")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/evaluations",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("loads the insufficient-evidence scenario without pretending it is evaluated", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "근거 부족" }));

    expect(screen.getByLabelText("Usage samples")).toHaveValue(null);
    expect(screen.getByText("아직 계산하지 않았습니다.")).toBeInTheDocument();
  });

  it("shows a stable API error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("unavailable", { status: 503 }));
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: /추천과 위험 계산/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("평가 요청 실패 (503)");
  });

  it("loads a backend-validated analysis artifact into the same review surface", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(artifactReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);
    const file = new File(['{"schema_version":1}'], "analysis.json", {
      type: "application/json",
    });

    await userEvent.upload(screen.getByLabelText("analysis artifact JSON"), file);

    expect(await screen.findByText("payments / checkout-api")).toBeInTheDocument();
    expect(screen.getByText("INTEGRITY ONLY")).toBeInTheDocument();
    expect(screen.getByText("패치 제안 가능")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/analysis-reviews",
      expect.objectContaining({ body: '{"schema_version":1}' }),
    );
  });

  it("distinguishes a replayed schema v2 recommendation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        ...artifactReview,
        artifact_schema_version: 2,
        verification_level: "recommendation_replayed",
        observation_source: {
          verification: "operator_declared",
          cluster_label: "eks-seoul-pilot",
          metrics_source_label: "prometheus-pilot",
        },
        checks: [
          ...artifactReview.checks,
          { code: "recommendation_replay", status: "pass", reason: "recommendation replayed" },
        ],
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("analysis artifact JSON"),
      new File(['{"schema_version":2}'], "analysis.json", { type: "application/json" }),
    );

    expect(await screen.findByText("RECOMMENDATION REPLAYED")).toBeInTheDocument();
    expect(screen.getByText("ANALYSIS ARTIFACT · SCHEMA 2")).toBeInTheDocument();
    expect(screen.getByText("eks-seoul-pilot")).toBeInTheDocument();
    expect(screen.getByText(/CPU \$0\.04\/core-hour/)).toBeInTheDocument();
  });

  it("rejects an oversized artifact before sending it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<App />);
    const file = new File([new Uint8Array(1024 * 1024 + 1)], "large.json", {
      type: "application/json",
    });

    await userEvent.upload(screen.getByLabelText("analysis artifact JSON"), file);

    expect(screen.getByRole("alert")).toHaveTextContent("1 MiB 이하여야 합니다");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads compact indexed benchmark files and renders the replayed result", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(benchmarkReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);
    const files = [
      directoryFile("result.json", "result-content"),
      directoryFile("measurements/before.json", "before-content"),
      directoryFile("measurements/after.json", "after-content"),
      directoryFile("verdict.json", "verdict-content"),
    ];

    await userEvent.upload(screen.getByLabelText("benchmark result directory"), files);

    expect(await screen.findByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("INDEX-BOUND REPLAY")).toBeInTheDocument();
    expect(screen.getByText("-98.1%")).toBeInTheDocument();
    expect(screen.getByText(/k6 raw bytes were not uploaded/)).toBeInTheDocument();
    expect(screen.getByText("candidate run observed no OOMKilled events")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/benchmark-reviews",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          result_json: "result-content",
          before_json: "before-content",
          after_json: "after-content",
          verdict_json: "verdict-content",
        }),
      }),
    );
  });

  it("rejects an incomplete benchmark folder before sending it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("benchmark result directory"),
      [directoryFile("result.json")],
    );

    expect(screen.getByRole("alert")).toHaveTextContent("필수 파일이 없습니다");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads a fully verified stored benchmark from a shareable query", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(fullBenchmarkReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    window.history.replaceState(
      {},
      "",
      `/?benchmark=${benchmarkReview.artifact_id}`,
    );

    render(<App />);

    expect(await screen.findByText("FULL ARTIFACT REPLAY")).toBeInTheDocument();
    expect(screen.getByText("전체 번들 검사 2/2")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      `/v1/benchmark-results/${benchmarkReview.artifact_id}/review`,
    );
  });

  it("rejects a malformed benchmark query without calling the API", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    window.history.replaceState({}, "", "/?benchmark=../../secret");

    render(<App />);

    expect(screen.getByRole("alert")).toHaveTextContent("artifact ID 형식");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads a fully replayed pair and visualizes two observations without statistical claims", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(pairReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    window.history.replaceState({}, "", `/?pair=${pairReview.artifact_id}`);

    render(<App />);

    expect(await screen.findByText("PAIR FULL ARTIFACT REPLAY")).toBeInTheDocument();
    expect(screen.getByText("1/2")).toBeInTheDocument();
    expect(screen.getAllByText("두 순서 모두 개선")).toHaveLength(2);
    expect(screen.getByText("순서별 방향 불일치")).toBeInTheDocument();
    expect(screen.getByText(/신뢰구간이나 통계적 유의성이 아닙니다/)).toBeInTheDocument();
    expect(screen.getByLabelText("Steady latency P95 두 실행 순서 관측 범위")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      `/v1/benchmark-pairs/${pairReview.artifact_id}/review`,
    );
  });

  it("runs recommendation and pair replay APIs as two operator-triggered demo steps", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(recordedEvaluationResult), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(pairReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    window.history.replaceState({}, "", "/?showcase=decision-journey");

    render(<App />);

    expect(screen.getByRole("heading", { name: /비용보다 안전을/ })).toBeInTheDocument();
    expect(screen.getByText("WAITING FOR OPERATOR")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /리소스가 줄어드는 순간보다/ })).toBeInTheDocument();
    expect(screen.getByLabelText("리소스 변화 비교")).toHaveTextContent("CURRENT REQUEST");
    expect(screen.getByLabelText("실행 과정 로그")).toHaveTextContent("operator action boundary ready");
    expect(screen.queryByRole("link", { name: /Draft PR #23/ })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /추천 계산 실행/ }));

    expect(await screen.findByText("LIVE CANDIDATE COMPUTED")).toBeInTheDocument();
    expect(screen.getAllByText("10m / 20m")).toHaveLength(2);
    expect(screen.getByText("ready / eligible")).toBeInTheDocument();
    expect(screen.getByText(/readiness 통과는 수집 근거가 충분하다는 뜻/)).toBeInTheDocument();
    expect(screen.getByText("10m CANDIDATE ≠ SAFE")).toBeInTheDocument();
    expect(screen.getByLabelText("실행 과정 로그")).toHaveTextContent("candidate rejected");
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/v1/evaluations",
      expect.objectContaining({ method: "POST" }),
    );
    const evaluationRequest = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(evaluationRequest).toMatchObject({
      current: {
        cpu_request_millicores: 1000,
        memory_request_mib: 2048,
      },
      observed: {
        memory_p99_mib: 7.68671875,
        sample_count: 122,
      },
    });
    expect(evaluationRequest.observed.cpu_p95_millicores).toBeCloseTo(3.658831, 6);
    expect(screen.queryByRole("link", { name: /Draft PR #23/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /20m Pair 검증 계속/ }));

    expect(await screen.findByText("PAIR REPLAY VERIFIED")).toBeInTheDocument();
    expect(screen.getByText("+40.804%")).toBeInTheDocument();
    expect(screen.getByText("-98.088%")).toBeInTheDocument();
    expect(screen.getByText("1/2 방향 일치 · 1 mixed")).toBeInTheDocument();
    expect(screen.getByText(/deploy\/demo\/overprovisioned-api.yaml/)).toBeInTheDocument();
    expect(screen.getByLabelText("Pair 반대 순서 검증 결과")).toHaveTextContent("2/2 PASS");
    expect(screen.getByLabelText("Pair 반대 순서 검증 결과")).toHaveTextContent(/opposite orders/i);
    expect(screen.getByLabelText("리소스 변화 비교")).toHaveTextContent("VERIFIED");
    expect(screen.getByText(/통계적 유의성을 주장하지 않습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "워크로드 관측값" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pair 상세 보기 →" })).toHaveAttribute(
      "href",
      `/?pair=${VERIFIED_PAIR_ID}`,
    );
    for (const link of screen.getAllByRole("link", { name: /Draft PR #23/ })) {
      expect(link).toHaveAttribute("href", "https://github.com/sangmu1126/kubefit/pull/23");
    }
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/v1/benchmark-pairs/${VERIFIED_PAIR_ID}/review`,
    );
  });

  it("keeps the recorded journey visible when triggered pair replay is unavailable", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(recordedEvaluationResult), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response("evidence unavailable", { status: 404 }));
    window.history.replaceState({}, "", "/?showcase=decision-journey");

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: /추천 계산 실행/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Pair 재검증/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("실행이 중단됐습니다");
    expect(screen.getByText("LIVE EXECUTION FAILED")).toBeInTheDocument();
    expect(screen.getByText("+40.804%")).toBeInTheDocument();
  });

  it("does not reuse the recorded rejection when the live candidate identity changes", async () => {
    const changedEvaluation = structuredClone(recordedEvaluationResult);
    changedEvaluation.recommendation.recommended.cpu_request_millicores = 15;
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify(changedEvaluation), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    window.history.replaceState({}, "", "/?showcase=decision-journey");

    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /추천 계산 실행/ }));

    expect(await screen.findByText("UNBENCHMARKED")).toBeInTheDocument();
    expect(screen.getByText("recorded rejection applies to 10m only")).toBeInTheDocument();
    expect(screen.getByLabelText("실행 과정 로그")).toHaveTextContent("recorded verdict not reused");
  });

  it("rejects an unknown showcase without calling the API", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    window.history.replaceState({}, "", "/?showcase=unknown");

    render(<App />);

    expect(screen.getByRole("alert")).toHaveTextContent("지원하지 않는 showcase");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("loads a completed campaign as chronological blocks without aggregation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(campaignReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    window.history.replaceState({}, "", `/?campaign=${campaignReview.artifact_id}`);

    render(<App />);

    expect(await screen.findByText("CAMPAIGN FULL ARTIFACT REPLAY")).toBeInTheDocument();
    expect(screen.getByText("3/3")).toBeInTheDocument();
    expect(screen.getByText("없음")).toBeInTheDocument();
    expect(screen.getAllByText("Candidate-first")).toHaveLength(2);
    expect(screen.getByLabelText("Block 2 측정 시간 위치")).toBeInTheDocument();
    expect(screen.getByText(/블록을 평균내거나 통계적 신뢰도를 추정하지 않습니다/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      `/v1/benchmark-campaigns/${campaignReview.artifact_id}/review`,
    );
  });

  it("rejects a malformed campaign query without calling the API", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    window.history.replaceState({}, "", "/?campaign=../../secret");

    render(<App />);

    expect(screen.getByRole("alert")).toHaveTextContent("artifact ID 형식");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects ambiguous review links without calling the API", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    window.history.replaceState(
      {},
      "",
      `/?benchmark=${benchmarkReview.artifact_id}&campaign=${campaignReview.artifact_id}`,
    );

    render(<App />);

    expect(screen.getByRole("alert")).toHaveTextContent("하나만 지정할 수 있습니다");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
