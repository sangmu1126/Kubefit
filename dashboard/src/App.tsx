import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import {
  evaluateResources,
  fetchStoredBenchmarkCampaignReview,
  fetchStoredBenchmarkPairReview,
  fetchStoredBenchmarkReview,
  reviewAnalysisArtifact,
  reviewBenchmarkArtifact,
} from "./api";
import { eligibleScenario, insufficientScenario } from "./scenarios";
import {
  DECISION_JOURNEY_ID,
  VERIFIED_PAIR_ID,
  decisionJourneyEvidence,
  recordedInitialEvaluationRequest,
  recordedYamlDiff,
} from "./showcase";
import type {
  CheckStatus,
  AnalysisReview,
  BenchmarkCampaignReview,
  BenchmarkMeasurement,
  BenchmarkReview,
  CounterbalancedPairReview,
  EvaluationRequest,
  EvaluationResult,
  Resources,
  RiskLevel,
  DecimalValue,
  PairMetricComparison,
} from "./types";

const MAX_ANALYSIS_ARTIFACT_BYTES = 1024 * 1024;
const MAX_BENCHMARK_REVIEW_FILE_BYTES = 128 * 1024;
const BENCHMARK_REVIEW_PATHS = [
  "result.json",
  "measurements/before.json",
  "measurements/after.json",
  "verdict.json",
] as const;

type NumericPath =
  | keyof EvaluationRequest["current"]
  | keyof EvaluationRequest["observed"]
  | "replica_count";

const resourceRows: Array<{
  label: string;
  key: keyof Resources;
  unit: string;
}> = [
  { label: "CPU request", key: "cpu_request_millicores", unit: "m" },
  { label: "CPU limit", key: "cpu_limit_millicores", unit: "m" },
  { label: "Memory request", key: "memory_request_mib", unit: "Mi" },
  { label: "Memory limit", key: "memory_limit_mib", unit: "Mi" },
];

function cloneScenario(scenario: EvaluationRequest): EvaluationRequest {
  return structuredClone(scenario);
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("선택한 파일을 읽을 수 없습니다."));
    reader.readAsText(file);
  });
}

function formatMoney(value: DecimalValue): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function riskLabel(level: RiskLevel): string {
  return { low: "낮음", medium: "주의", high: "높음", unknown: "판단 불가" }[level];
}

function statusLabel(status: CheckStatus): string {
  return { pass: "통과", warning: "검토", block: "차단" }[status];
}

function NumberField({
  label,
  value,
  unit,
  min = 0,
  step = 1,
  required = true,
  onChange,
}: {
  label: string;
  value: number | undefined;
  unit?: string;
  min?: number;
  step?: number;
  required?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label className="number-field">
      <span>{label}</span>
      <span className="input-shell">
        <input
          aria-label={label}
          type="number"
          min={min}
          step={step}
          value={value ?? ""}
          required={required}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        {unit && <small>{unit}</small>}
      </span>
    </label>
  );
}

function ResourceComparison({ result }: { result: EvaluationResult }) {
  return (
    <section className="panel comparison-panel" aria-labelledby="resource-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">CAPACITY</p>
          <h2 id="resource-title">현재와 추천 리소스</h2>
        </div>
        <p>P95/P99 + 안전 여유 25%</p>
      </div>
      <div className="comparison-head" aria-hidden="true">
        <span />
        <span>현재</span>
        <span>추천</span>
      </div>
      <div className="resource-rows">
        {resourceRows.map((row) => {
          const current = result.current[row.key];
          const recommended = result.recommendation.recommended[row.key];
          const max = Math.max(current, recommended);
          return (
            <div className="resource-row" key={row.key}>
              <strong>{row.label}</strong>
              <div className="bar-cell">
                <span>{current.toLocaleString()}{row.unit}</span>
                <i style={{ width: `${(current / max) * 100}%` }} />
              </div>
              <div className="bar-cell recommended">
                <span>{recommended.toLocaleString()}{row.unit}</span>
                <i style={{ width: `${(recommended / max) * 100}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function DecisionPanel({ result }: { result: EvaluationResult }) {
  const eligible = result.patch_eligibility.status === "eligible";
  return (
    <section className={`decision ${eligible ? "eligible" : "blocked"}`}>
      <div className="decision-copy">
        <p className="eyebrow">GITOPS DECISION GATE</p>
        <h2>{eligible ? "패치 제안 가능" : "패치 제안 차단"}</h2>
        <p>
          {eligible
            ? "관측 근거와 안전성 검사를 통과했습니다. 다음 단계에서 YAML 변경안을 검토할 수 있습니다."
            : "비용 절감 예상값은 참고용입니다. 근거가 충분해질 때까지 YAML과 클러스터는 변경하지 않습니다."}
        </p>
      </div>
      <span className="decision-mark" aria-label={eligible ? "eligible" : "blocked"}>
        {eligible ? "✓" : "!"}
      </span>
    </section>
  );
}

function ArtifactContext({ review }: { review: AnalysisReview }) {
  const replayed = review.verification_level === "recommendation_replayed";
  return (
    <section className="artifact-context" aria-labelledby="artifact-title">
      <div>
        <p className="eyebrow">ANALYSIS ARTIFACT · SCHEMA {review.artifact_schema_version}</p>
        <h2 id="artifact-title">
          {review.target.namespace} / {review.target.deployment}
        </h2>
        <p>
          container <strong>{review.target.container}</strong> · created {new Date(review.workload_created_at).toLocaleString("ko-KR")}
        </p>
        {review.observation_source && (
          <p>
            관측 출처(운영자 선언): <strong>{review.observation_source.cluster_label}</strong>
            {" · "}메트릭: {review.observation_source.metrics_source_label}
          </p>
        )}
      </div>
      <div className="artifact-verification">
        <span>무결성 검사 {review.checks.length}/{review.checks.length}</span>
        <strong>{replayed ? "RECOMMENDATION REPLAYED" : "INTEGRITY ONLY"}</strong>
        <code title={review.workload_uid}>{review.workload_uid}</code>
      </div>
      <details>
        <summary>검증 범위와 한계</summary>
        <ul>
          {review.checks.map((check) => <li key={check.code}>✓ {check.reason}</li>)}
          {review.limitations.map((limitation) => <li className="limitation" key={limitation}>△ {limitation}</li>)}
        </ul>
      </details>
    </section>
  );
}

function Results({ result, review }: { result: EvaluationResult; review: AnalysisReview | null }) {
  return (
    <main className="results" aria-live="polite">
      {review && <ArtifactContext review={review} />}
      <DecisionPanel result={result} />
      <section className="metric-grid" aria-label="평가 요약">
        <article className="metric-card accent">
          <p>예상 request 비용 절감</p>
          <strong>{Number(result.cost.savings_percent).toFixed(1)}%</strong>
          <span>
            월 {formatMoney(result.cost.current.total_usd)} → {formatMoney(result.cost.recommended.total_usd)}
          </span>
        </article>
        <article className="metric-card">
          <p>OOM 위험</p>
          <strong className={`risk-${result.recommendation.risk.oom}`}>
            {riskLabel(result.recommendation.risk.oom)}
          </strong>
          <span>memory max 대비 limit 여유 기반</span>
        </article>
        <article className="metric-card">
          <p>CPU throttling 위험</p>
          <strong className={`risk-${result.recommendation.risk.cpu_throttling}`}>
            {riskLabel(result.recommendation.risk.cpu_throttling)}
          </strong>
          <span>throttled-period P95와 CPU headroom 기반</span>
        </article>
      </section>
      <ResourceComparison result={result} />
      <div className="evidence-grid">
        <section className="panel" aria-labelledby="checks-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">SAFETY CHECKS</p>
              <h2 id="checks-title">적용 전 판단</h2>
            </div>
          </div>
          <ul className="check-list">
            {result.patch_eligibility.checks.map((check) => (
              <li key={check.code}>
                <span className={`status-dot ${check.status}`} />
                <div>
                  <strong>{statusLabel(check.status)}</strong>
                  <p>{check.reason}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel" aria-labelledby="evidence-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">WHY THIS VALUE</p>
              <h2 id="evidence-title">추천 근거</h2>
            </div>
          </div>
          <ol className="evidence-list">
            {result.recommendation.evidence.map((item) => <li key={item}>{item}</li>)}
          </ol>
        </section>
      </div>
      <details className="caveats">
        <summary>비용 추정의 전제와 한계</summary>
        <p>
          {result.cost.replica_count} replicas · {result.cost.assumptions.monthly_hours} hours · {result.cost.assumptions.price_source}
          {" · "}CPU ${result.cost.assumptions.cpu_core_hour_usd}/core-hour
          {" · "}memory ${result.cost.assumptions.memory_gib_hour_usd}/GiB-hour
        </p>
        <ul>{result.cost.caveats.map((item) => <li key={item}>{item}</li>)}</ul>
      </details>
    </main>
  );
}

type BenchmarkMetric = {
  label: string;
  before: number;
  after: number;
  unit: string;
};

function metricRows(
  before: BenchmarkMeasurement,
  after: BenchmarkMeasurement,
): BenchmarkMetric[] {
  return [
    { label: "Steady P95", before: before.steady.latency_p95_ms, after: after.steady.latency_p95_ms, unit: "ms" },
    { label: "Steady P99", before: before.steady.latency_p99_ms, after: after.steady.latency_p99_ms, unit: "ms" },
    { label: "Spike P95", before: before.spike.latency_p95_ms, after: after.spike.latency_p95_ms, unit: "ms" },
    { label: "Spike P99", before: before.spike.latency_p99_ms, after: after.spike.latency_p99_ms, unit: "ms" },
    { label: "CPU throttling P95", before: before.runtime.cpu_throttling_p95_percent, after: after.runtime.cpu_throttling_p95_percent, unit: "%" },
    { label: "Recovery", before: before.runtime.traffic_spike_recovery_seconds, after: after.runtime.traffic_spike_recovery_seconds, unit: "s" },
  ];
}

function BenchmarkResults({ review }: { review: BenchmarkReview }) {
  const verdict = review.verdict.status;
  const fullArtifact = review.verification_level === "full_artifact_replay";
  const rows = metricRows(review.before, review.after);
  const candidateErrorRate = Math.max(
    review.after.steady.error_rate,
    review.after.spike.error_rate,
    review.after.recovery.error_rate,
  );
  return (
    <main className="results benchmark-results" aria-live="polite">
      <section className="artifact-context" aria-labelledby="benchmark-artifact-title">
        <div>
          <p className="eyebrow">BENCHMARK RESULT · SCHEMA {review.schema_version}</p>
          <h2 id="benchmark-artifact-title">Before / After 실행 근거</h2>
          <p>proposal <strong>{review.proposal_id}</strong></p>
        </div>
        <div className="artifact-verification">
          <span>
            {fullArtifact ? "전체 번들 검사" : "인덱스 결합 검사"}{" "}
            {review.checks.length}/{review.checks.length}
          </span>
          <strong>{fullArtifact ? "FULL ARTIFACT REPLAY" : "INDEX-BOUND REPLAY"}</strong>
          <code title={review.artifact_id}>{review.artifact_id}</code>
        </div>
        <details>
          <summary>검증 범위와 한계</summary>
          <ul>
            {review.checks.map((check) => <li key={check.code}>✓ {check.reason}</li>)}
            {review.limitations.map((limitation) => <li className="limitation" key={limitation}>△ {limitation}</li>)}
          </ul>
        </details>
      </section>
      <section className={`benchmark-verdict ${verdict}`}>
        <div>
          <p className="eyebrow">REPLAYED VERDICT</p>
          <h2>{verdict.toUpperCase()}</h2>
          <p>저장된 측정값으로 정책 판정을 서버에서 다시 계산했습니다.</p>
        </div>
        <span>{verdict === "pass" ? "✓" : "!"}</span>
      </section>
      <section className="metric-grid" aria-label="벤치마크 요약">
        <article className="metric-card accent">
          <p>request 비용 변화</p>
          <strong>{review.verdict.cost_change_percent === null ? "—" : `${Number(review.verdict.cost_change_percent).toFixed(1)}%`}</strong>
          <span>{formatMoney(review.before.request_cost_usd)} → {formatMoney(review.after.request_cost_usd)}</span>
        </article>
        <article className="metric-card">
          <p>후보 최대 오류율</p>
          <strong>{(candidateErrorRate * 100).toFixed(2)}%</strong>
          <span>steady · spike · recovery 중 최대</span>
        </article>
        <article className="metric-card">
          <p>후보 런타임 이상</p>
          <strong>{review.after.runtime.oom_killed_count + review.after.runtime.restart_count}</strong>
          <span>OOM {review.after.runtime.oom_killed_count} · restart {review.after.runtime.restart_count}</span>
        </article>
      </section>
      <section className="panel comparison-panel" aria-labelledby="benchmark-comparison-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">MEASURED IMPACT</p>
            <h2 id="benchmark-comparison-title">성능과 안정성 비교</h2>
          </div>
          <p>낮을수록 좋음</p>
        </div>
        <div className="comparison-head" aria-hidden="true"><span /><span>Before</span><span>After</span></div>
        <div className="resource-rows">
          {rows.map((row) => {
            const max = Math.max(row.before, row.after, 0.001);
            return (
              <div className="resource-row" key={row.label}>
                <strong>{row.label}</strong>
                <div className="bar-cell"><span>{row.before.toFixed(3)}{row.unit}</span><i style={{ width: `${(row.before / max) * 100}%` }} /></div>
                <div className="bar-cell recommended"><span>{row.after.toFixed(3)}{row.unit}</span><i style={{ width: `${(row.after / max) * 100}%` }} /></div>
              </div>
            );
          })}
        </div>
      </section>
      <section className="panel" aria-labelledby="verdict-checks-title">
        <div className="section-heading">
          <div><p className="eyebrow">POLICY CHECKS</p><h2 id="verdict-checks-title">판정 근거</h2></div>
        </div>
        <ul className="check-list benchmark-checks">
          {review.verdict.checks.map((check) => (
            <li key={check.code}>
              <span className={`status-dot ${check.status}`} />
              <div><strong>{check.status.toUpperCase()} · {check.code}</strong><p>{check.reason}</p></div>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function pairDirectionLabel(direction: PairMetricComparison["direction"]): string {
  return {
    improved: "두 순서 모두 개선",
    regressed: "두 순서 모두 악화",
    unchanged: "두 순서 모두 동일",
    mixed: "순서별 방향 불일치",
  }[direction];
}

function formatPairChange(metric: PairMetricComparison, order: "before-after" | "after-before"): string {
  const trial = metric.trials.find((item) => item.measurement_order === order)!;
  return trial.change_percent === null
    ? `${trial.delta >= 0 ? "+" : ""}${trial.delta.toFixed(3)}${metric.unit}`
    : `${trial.change_percent >= 0 ? "+" : ""}${trial.change_percent.toFixed(3)}%`;
}

function PairMetricRange({ metric }: { metric: PairMetricComparison }) {
  const maxMagnitude = Math.max(Math.abs(metric.delta_min), Math.abs(metric.delta_max), 0.001);
  const position = (delta: number) => 50 + (delta / maxMagnitude) * 48;
  const low = position(metric.delta_min);
  const high = position(metric.delta_max);
  const byOrder = Object.fromEntries(
    metric.trials.map((trial) => [trial.measurement_order, trial]),
  ) as Record<"before-after" | "after-before", PairMetricComparison["trials"][number]>;
  return (
    <article className={`pair-metric ${metric.direction}`}>
      <div className="pair-metric-copy">
        <strong>{metric.label}</strong>
        <span>{pairDirectionLabel(metric.direction)}</span>
      </div>
      <div className="range-plot" aria-label={`${metric.label} 두 실행 순서 관측 범위`}>
        <i className="zero-line" />
        <i className="observed-range" style={{ left: `${low}%`, width: `${Math.max(high - low, 2)}%` }} />
        <i className="order-point before-first" style={{ left: `${position(byOrder["before-after"].delta)}%` }} />
        <i className="order-point candidate-first" style={{ left: `${position(byOrder["after-before"].delta)}%` }} />
      </div>
      <div className="pair-values">
        <span><i className="before-first" />Before-first <strong>{formatPairChange(metric, "before-after")}</strong></span>
        <span><i className="candidate-first" />Candidate-first <strong>{formatPairChange(metric, "after-before")}</strong></span>
      </div>
    </article>
  );
}

function PairResults({ review }: { review: CounterbalancedPairReview }) {
  const agreements = review.metrics.filter((metric) => metric.direction !== "mixed").length;
  const improvements = review.metrics.filter((metric) => metric.direction === "improved").length;
  const mixed = review.metrics.length - agreements;
  return (
    <main className="results benchmark-results" aria-live="polite">
      <section className="artifact-context" aria-labelledby="pair-artifact-title">
        <div>
          <p className="eyebrow">COUNTERBALANCED PAIR · SCHEMA {review.schema_version}</p>
          <h2 id="pair-artifact-title">두 실행 순서의 관측 변화</h2>
          <p>proposal <strong>{review.proposal_id}</strong></p>
        </div>
        <div className="artifact-verification">
          <span>전체 페어 검사 {review.checks.length}/{review.checks.length}</span>
          <strong>PAIR FULL ARTIFACT REPLAY</strong>
          <code title={review.artifact_id}>{review.artifact_id}</code>
        </div>
        <details>
          <summary>검증 범위와 한계</summary>
          <ul>
            {review.checks.map((check) => <li key={check.code}>✓ {check.reason}</li>)}
            {review.limitations.map((limitation) => <li className="limitation" key={limitation}>△ {limitation}</li>)}
          </ul>
        </details>
      </section>
      <section className="benchmark-verdict pass">
        <div>
          <p className="eyebrow">COUNTERBALANCED POLICY GATE</p>
          <h2>PASS</h2>
          <p>두 반대 실행 순서가 같은 안전 정책을 통과했습니다.</p>
        </div>
        <span>✓</span>
      </section>
      <section className="metric-grid" aria-label="페어 관측 요약">
        <article className="metric-card accent">
          <p>방향 일치</p><strong>{agreements}/{review.metrics.length}</strong>
          <span>동일 포함 · 순서별 변화 방향 비교</span>
        </article>
        <article className="metric-card">
          <p>두 순서 모두 개선</p><strong>{improvements}</strong>
          <span>낮을수록 좋은 핵심 신호 기준</span>
        </article>
        <article className="metric-card">
          <p>방향 불일치</p><strong>{mixed}</strong>
          <span>불일치가 있어도 정책 한계 내 PASS 가능</span>
        </article>
      </section>
      <section className="panel pair-observations" aria-labelledby="pair-observations-title">
        <div className="section-heading">
          <div><p className="eyebrow">TWO ORDER-SPECIFIC OBSERVATIONS</p><h2 id="pair-observations-title">변화 방향과 관측 범위</h2></div>
          <p>← 개선 · 0 · 악화 →</p>
        </div>
        <p className="range-warning"><strong>표본 2개의 최소–최대 범위입니다.</strong> 신뢰구간이나 통계적 유의성이 아닙니다.</p>
        <div className="pair-metric-list">
          {review.metrics.map((metric) => <PairMetricRange key={metric.code} metric={metric} />)}
        </div>
      </section>
    </main>
  );
}

function orderLabel(order: "before-after" | "after-before"): string {
  return order === "before-after" ? "Before-first" : "Candidate-first";
}

function CampaignResults({ review }: { review: BenchmarkCampaignReview }) {
  const startedAt = Math.min(
    ...review.blocks.map((block) => Date.parse(block.measurement_started_at)),
  );
  const finishedAt = Math.max(
    ...review.blocks.map((block) => Date.parse(block.measurement_finished_at)),
  );
  const span = Math.max(finishedAt - startedAt, 1);
  const checksPassed = review.checks.filter((check) => check.status === "pass").length;
  return (
    <main className="results benchmark-results" aria-live="polite">
      <section className="artifact-context" aria-labelledby="campaign-artifact-title">
        <div>
          <p className="eyebrow">PREREGISTERED CAMPAIGN · SCHEMA {review.schema_version}</p>
          <h2 id="campaign-artifact-title">시간순 반복 검증 블록</h2>
          <p>campaign <strong>{review.campaign_id}</strong></p>
          <p>proposal <strong>{review.proposal_id}</strong></p>
        </div>
        <div className="artifact-verification">
          <span>전체 캠페인 검사 {review.checks.length}/{review.checks.length}</span>
          <strong>CAMPAIGN FULL ARTIFACT REPLAY</strong>
          <code title={review.artifact_id}>{review.artifact_id}</code>
        </div>
        <details>
          <summary>검증 범위와 한계</summary>
          <ul>
            {review.checks.map((check) => <li key={check.code}>✓ {check.reason}</li>)}
            {review.limitations.map((limitation) => <li className="limitation" key={limitation}>△ {limitation}</li>)}
          </ul>
        </details>
      </section>
      <section className="benchmark-verdict pass">
        <div>
          <p className="eyebrow">PREREGISTERED COMPLETION GATE</p>
          <h2>COMPLETE</h2>
          <p>사전에 정한 모든 블록이 계획된 순서로 수집됐습니다.</p>
        </div>
        <span>✓</span>
      </section>
      <section className="metric-grid" aria-label="캠페인 검증 요약">
        <article className="metric-card accent">
          <p>완료 블록</p><strong>{review.completed_pairs}/{review.planned_pairs}</strong>
          <span>complete-all stopping rule</span>
        </article>
        <article className="metric-card">
          <p>검증 항목</p><strong>{checksPassed}/{review.checks.length}</strong>
          <span>전체 중첩 artifact 재실행</span>
        </article>
        <article className="metric-card">
          <p>통계 집계</p><strong>{review.aggregation_performed ? "수행" : "없음"}</strong>
          <span>평균 · 신뢰구간 · 유의성 미계산</span>
        </article>
      </section>
      <section className="panel campaign-review" aria-labelledby="campaign-blocks-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">CHRONOLOGICAL COLLECTION</p>
            <h2 id="campaign-blocks-title">계획과 실제 실행 순서</h2>
          </div>
          <p>시간 위치와 측정 구간</p>
        </div>
        <p className="range-warning"><strong>반복 수집 규율만 보여줍니다.</strong> 블록을 평균내거나 통계적 신뢰도를 추정하지 않습니다.</p>
        <div className="campaign-timeline">
          {review.blocks.map((block) => {
            const blockStart = Date.parse(block.measurement_started_at);
            const blockFinish = Date.parse(block.measurement_finished_at);
            const left = ((blockStart - startedAt) / span) * 100;
            const width = Math.min(
              Math.max(((blockFinish - blockStart) / span) * 100, 1.5),
              100 - left,
            );
            return (
              <article className="campaign-block" key={block.pair_id}>
                <div className="campaign-block-heading">
                  <span>{String(block.block).padStart(2, "0")}</span>
                  <div><strong>Block {block.block}</strong><code title={block.pair_id}>{block.pair_id}</code></div>
                  <b>PASS</b>
                </div>
                <div className="campaign-order">
                  <span>계획 <strong>{orderLabel(block.scheduled_first_order)}</strong></span>
                  <span>실제 <strong>{orderLabel(block.observed_first_order)}</strong></span>
                </div>
                <div className="campaign-timebar" aria-label={`Block ${block.block} 측정 시간 위치`}>
                  <i className={block.observed_first_order} style={{ left: `${left}%`, width: `${width}%` }} />
                </div>
                <p>{new Date(block.measurement_started_at).toLocaleString("ko-KR")} → {new Date(block.measurement_finished_at).toLocaleString("ko-KR")}</p>
              </article>
            );
          })}
        </div>
      </section>
    </main>
  );
}

function EvidenceLink({ href, children }: { href: string; children: string }) {
  return <a href={href} target="_blank" rel="noreferrer">{children} ↗</a>;
}

type ShowcaseRunState =
  | "idle"
  | "evaluating"
  | "candidate-ready"
  | "replaying"
  | "verified"
  | "failed";

function allocationWidth(value: number, current: number) {
  return `${Math.max(8, (Math.log10(value + 1) / Math.log10(current + 1)) * 100)}%`;
}

function ResourceTrack({
  label,
  unit,
  current,
  observed,
  candidate,
  verified,
}: {
  label: string;
  unit: string;
  current: number;
  observed: number;
  candidate: number | null;
  verified: number | null;
}) {
  const stages = [
    { name: "CURRENT REQUEST", value: current, tone: "current" },
    { name: "OBSERVED", value: observed, tone: "observed" },
    { name: "LIVE CANDIDATE", value: candidate, tone: "rejected" },
    { name: "VERIFIED", value: verified, tone: "verified" },
  ];

  return (
    <div className="resource-track">
      <div className="resource-track-title"><strong>{label}</strong><small>log scale · request 기준</small></div>
      {stages.map((stage) => (
        <div className={`resource-bar-row ${stage.tone} ${stage.value === null ? "locked" : ""}`} key={stage.name}>
          <span>{stage.name}</span>
          <div className="resource-bar-rail">
            {stage.value !== null && <i style={{ width: allocationWidth(stage.value, current) }} />}
          </div>
          <b>{stage.value === null ? "LOCKED" : `${stage.value.toFixed(stage.value < 10 ? 2 : 0)}${unit}`}</b>
        </div>
      ))}
    </div>
  );
}

function DecisionJourney({
  review,
  evaluation,
  runState,
  error,
  onEvaluate,
  onReplay,
  onReset,
}: {
  review: CounterbalancedPairReview | null;
  evaluation: EvaluationResult | null;
  runState: ShowcaseRunState;
  error: string | null;
  onEvaluate: () => void;
  onReplay: () => void;
  onReset: () => void;
}) {
  const evidence = decisionJourneyEvidence;
  const agreements = review?.metrics.filter((metric) => metric.direction !== "mixed").length ?? 0;
  const mixed = review?.metrics.filter((metric) => metric.direction === "mixed").length ?? 0;
  const throttling = review?.metrics.find((metric) => metric.code === "cpu_throttling_p95");
  const throttlingValues = throttling?.trials.map((trial) => trial.after) ?? [];
  const throttlingSummary = throttlingValues.length > 0 && throttlingValues.every((value) => value === 0)
    ? "0%"
    : "검증 상세 참조";
  const verificationReady = review?.status === "pass";
  const running = runState === "evaluating" || runState === "replaying";
  const recommendationReady = evaluation !== null;
  const liveCandidateCpu = evaluation?.recommendation.recommended.cpu_request_millicores ?? null;
  const recordedCandidateMatches = liveCandidateCpu === evidence.rejected.cpuRequestMillicores;
  const heroStatus = {
    idle: ["WAITING FOR OPERATOR", "RUN"],
    evaluating: ["POST /v1/evaluations", "…"],
    "candidate-ready": ["LIVE CANDIDATE COMPUTED", evaluation ? `${evaluation.recommendation.recommended.cpu_request_millicores}m` : "—"],
    replaying: ["GET /v1/benchmark-pairs/{id}/review", "…"],
    verified: ["PAIR REPLAY VERIFIED", "PASS"],
    failed: ["LIVE EXECUTION FAILED", "!"],
  }[runState];
  const traceLines = [
    { visible: true, source: "SYSTEM", text: "operator action boundary ready" },
    { visible: runState === "failed", source: "ERROR", text: error ?? "live execution stopped" },
    { visible: runState !== "idle", source: "LIVE", text: "POST /v1/evaluations · retained observation" },
    { visible: recommendationReady, source: "LIVE", text: evaluation ? `candidate ${evaluation.recommendation.recommended.cpu_request_millicores}m/${evaluation.recommendation.recommended.cpu_limit_millicores}m · saving ${evaluation.cost.savings_percent}%` : "" },
    { visible: recommendationReady, source: "RECORDED", text: recordedCandidateMatches ? `steady P99 +${evidence.rejected.steadyP99ChangePercent}% · candidate rejected` : "live candidate differs · recorded verdict not reused" },
    { visible: recommendationReady, source: "POLICY", text: "monotonic CPU floor · refined proposal 20m/40m" },
    { visible: runState === "replaying" || verificationReady, source: "LIVE", text: "GET pair review · replaying two opposite orders" },
    { visible: verificationReady, source: "LIVE", text: review ? `${review.checks.length}/${review.checks.length} checks PASS · GitOps evidence unlocked` : "" },
  ].filter((line) => line.visible);
  const steadyLatency = review?.metrics.find((metric) => metric.code === "steady_latency_p99")
    ?? review?.metrics.find((metric) => metric.code === "steady_latency_p95");

  return (
    <main className="showcase" id="top" aria-live="polite">
      <section className="showcase-hero">
        <div>
          <p className="eyebrow">INTERACTIVE VERIFIED DEMO · OPERATOR TRIGGERED</p>
          <h1>비용보다 안전을<br />먼저 검증했습니다.</h1>
          <p>
            98% 절감 후보를 바로 적용하지 않았습니다. 실패한 10m 제안을 보존하고,
            재분석한 20m 제안을 반대 순서로 검증한 뒤 Draft PR까지만 만들었습니다.
          </p>
        </div>
        <div className={`showcase-verdict ${verificationReady ? "verified" : "pending"}`}>
          <span>{heroStatus[0]}</span>
          <strong>{heroStatus[1]}</strong>
          <small>{review ? `${review.checks.length}/${review.checks.length} checks · 2/2 orders` : recommendationReady ? "2단계 Pair 재검증을 실행하세요" : "버튼을 눌러 실제 API를 실행하세요"}</small>
        </div>
      </section>

      <section className="demo-runner" aria-labelledby="demo-runner-title">
        <div className="demo-runner-copy">
          <p className="eyebrow">LIVE EXECUTION</p>
          <h2 id="demo-runner-title">설명이 아니라 서버 코드를 실행합니다.</h2>
          <p>첫 호출은 보존된 관측 입력으로 추천·비용·readiness를 다시 계산합니다. 두 번째 호출은 공개 Pair의 모든 artifact와 정책 verdict를 서버에서 다시 검증합니다.</p>
        </div>
        <ol className="demo-runner-steps">
          <li className={recommendationReady ? "done" : runState === "evaluating" ? "active" : ""}>
            <span>1</span><div><strong>추천 API</strong><small>POST /v1/evaluations</small></div><b>{recommendationReady ? "DONE" : runState === "evaluating" ? "RUNNING" : "WAIT"}</b>
          </li>
          <li className={verificationReady ? "done" : runState === "replaying" ? "active" : ""}>
            <span>2</span><div><strong>Pair Replay API</strong><small>GET /v1/benchmark-pairs/…/review</small></div><b>{verificationReady ? "DONE" : runState === "replaying" ? "RUNNING" : "WAIT"}</b>
          </li>
          <li className={verificationReady ? "done" : ""}>
            <span>3</span><div><strong>GitOps 증거</strong><small>검증된 YAML diff + Draft PR</small></div><b>{verificationReady ? "READY" : "LOCKED"}</b>
          </li>
        </ol>
        <div className="demo-runner-action">
          {runState === "idle" || runState === "failed" ? (
            <button type="button" onClick={onEvaluate} disabled={running}>1. 추천 계산 실행 <span>→</span></button>
          ) : runState === "candidate-ready" ? (
            <button type="button" onClick={onReplay}>2. 실패 기록을 거쳐 Pair 재검증 <span>→</span></button>
          ) : running ? (
            <button type="button" disabled>서버 응답 검증 중…</button>
          ) : (
            <button type="button" className="secondary" onClick={onReset}>데모 다시 실행 ↻</button>
          )}
          <small>클러스터·GitHub를 변경하지 않는 read-only 실행</small>
        </div>
      </section>

      {error && (
        <p className="showcase-error" role="alert">
          실행이 중단됐습니다. 기록된 증거는 변경되지 않았습니다: {error}
        </p>
      )}

      <section className={`decision-console state-${runState}`} aria-labelledby="decision-console-title">
        <div className="decision-console-heading">
          <div><p className="eyebrow">DECISION CONSOLE · LIVE + RECORDED EVIDENCE</p><h2 id="decision-console-title">리소스가 줄어드는 순간보다, 멈춘 이유를 보여줍니다.</h2></div>
          <span>{runState.replace("-", " ").toUpperCase()}</span>
        </div>
        <div className="decision-console-grid">
          <div className="resource-visual" aria-label="리소스 변화 비교">
            <ResourceTrack
              label="CPU"
              unit="m"
              current={recordedInitialEvaluationRequest.current.cpu_request_millicores}
              observed={recordedInitialEvaluationRequest.observed.cpu_p95_millicores}
              candidate={evaluation?.recommendation.recommended.cpu_request_millicores ?? null}
              verified={verificationReady ? evidence.refined.cpuRequestMillicores : null}
            />
            <ResourceTrack
              label="MEMORY"
              unit="Mi"
              current={recordedInitialEvaluationRequest.current.memory_request_mib}
              observed={recordedInitialEvaluationRequest.observed.memory_p99_mib}
              candidate={evaluation?.recommendation.recommended.memory_request_mib ?? null}
              verified={verificationReady ? evidence.refined.memoryRequestMiB : null}
            />
          </div>
          <div className={`decision-gate ${recommendationReady ? "decided" : "waiting"} ${verificationReady ? "passed" : ""}`}>
            <small>PERFORMANCE GATE</small>
            {!recommendationReady ? (
              <><strong>WAITING</strong><p>추천 API 실행 전</p></>
            ) : (
              <>
                <div className="decision-candidate"><span>{liveCandidateCpu}m</span><b>{recordedCandidateMatches ? "REJECTED" : "UNBENCHMARKED"}</b><small>{recordedCandidateMatches ? `recorded steady P99 +${evidence.rejected.steadyP99ChangePercent}%` : "recorded rejection applies to 10m only"}</small></div>
                <i>↓ MONOTONIC REFINE</i>
                <div className={verificationReady ? "decision-refined verified" : "decision-refined"}><span>20m</span><b>{verificationReady ? "VERIFIED" : "REPLAY REQUIRED"}</b><small>{verificationReady ? `2 orders · ${review?.checks.length}/${review?.checks.length} checks` : "readiness ≠ performance safety"}</small></div>
                {runState === "candidate-ready" && <button type="button" className="gate-action" onClick={onReplay}>20m Pair 검증 계속 →</button>}
              </>
            )}
          </div>
          <div className="execution-trace" aria-label="실행 과정 로그">
            <div><span>EXECUTION TRACE</span><b>{running ? "RUNNING" : verificationReady ? "COMPLETE" : "READY"}</b></div>
            <ol>
              {traceLines.map((line, index) => <li key={`${line.source}-${line.text}`}><i>{String(index + 1).padStart(2, "0")}</i><em>{line.source}</em><span>{line.text}</span></li>)}
            </ol>
          </div>
        </div>
        {evaluation && (
          <div className="live-result-strip">
            <div><span>LIVE CPU</span><strong>{evaluation.recommendation.recommended.cpu_request_millicores}m / {evaluation.recommendation.recommended.cpu_limit_millicores}m</strong></div>
            <div><span>LIVE MEMORY</span><strong>{evaluation.recommendation.recommended.memory_request_mib}Mi / {evaluation.recommendation.recommended.memory_limit_mib}Mi</strong></div>
            <div><span>PROJECTED</span><strong>{evaluation.cost.savings_percent}%</strong></div>
            <div><span>READINESS / GATE</span><strong>{evaluation.recommendation.readiness.status} / {evaluation.patch_eligibility.status}</strong></div>
            <p>readiness 통과는 수집 근거가 충분하다는 뜻입니다. 성능 안전성은 별도이며, 기록된 회귀 때문에 10m 후보는 자동 적용되지 않았습니다.</p>
          </div>
        )}
        {review && steadyLatency && (
          <div className="pair-proof" aria-label="Pair 반대 순서 검증 결과">
            <div className="pair-proof-title"><span>COUNTERBALANCED PAIR</span><strong>2 ORDERS</strong><b>{review.checks.length}/{review.checks.length} PASS</b></div>
            <div className="order-proof-grid">
              {steadyLatency.trials.map((trial, index) => (
                <article key={trial.benchmark_id}>
                  <small>ORDER {index + 1} · {trial.measurement_order}</small>
                  <div><span>{trial.before.toFixed(2)}ms</span><i>→</i><strong>{trial.after.toFixed(2)}ms</strong></div>
                  <b>{trial.change_percent?.toFixed(2)}% · {trial.direction.toUpperCase()}</b>
                </article>
              ))}
            </div>
            <ul>{review.checks.map((check) => <li key={check.code}><i>✓</i><span>{check.code.replaceAll("_", " ")}</span><b>{check.status.toUpperCase()}</b></li>)}</ul>
          </div>
        )}
      </section>

      {evaluation && !verificationReady && (
        <section className="candidate-warning" aria-label="후보 거부 근거">
          <strong>{liveCandidateCpu}m CANDIDATE ≠ SAFE</strong>
          <p>API는 계산을 완료했지만 성능 검증은 별도입니다. {recordedCandidateMatches ? `기록된 steady P99 +${evidence.rejected.steadyP99ChangePercent}% 회귀가 이 후보를 멈췄습니다.` : "현재 후보와 기록된 10m 실패가 다르므로 과거 verdict를 적용하지 않습니다."}</p>
        </section>
      )}

      {verificationReady && (
        <section className="live-gitops" aria-labelledby="live-gitops-title">
          <div>
            <p className="eyebrow">VERIFIED OUTPUT · READ ONLY</p>
            <h2 id="live-gitops-title">검증이 끝난 뒤에만 YAML 변경 근거를 엽니다.</h2>
            <p>아래 diff는 검증된 refined proposal의 기록입니다. 이 데모는 새 PR을 만들거나 기존 PR을 병합하지 않습니다.</p>
            <EvidenceLink href={evidence.sources.draftPullRequest}>Draft PR #23 열기</EvidenceLink>
          </div>
          <pre><code>{recordedYamlDiff}</code></pre>
        </section>
      )}

      <section className="showcase-metrics" aria-label="발표 핵심 지표">
        <article><span>예시 요율 기반 비용 투영</span><strong>{evidence.costProjection.changePercent}%</strong><small>AWS 청구서 절감 실측이 아님</small></article>
        <article><span>고정 부하 요청 오류</span><strong>{evidence.observation.errors}</strong><small>{evidence.observation.requests.toLocaleString()} requests</small></article>
        <article><span>Candidate throttling P95</span><strong>{throttlingSummary}</strong><small>Pair artifact replay 기준</small></article>
        <article><span>반대 실행 순서 검증</span><strong>{review ? "2/2" : "—"}</strong><small>{review ? `${agreements}/${review.metrics.length} 방향 일치 · ${mixed} mixed` : "API 연결 대기"}</small></article>
        <article className="draft"><span>GitOps 변경 상태</span><strong>DRAFT</strong><small>자동 병합·배포 없음</small></article>
      </section>

      <section className="journey-panel" aria-labelledby="journey-title">
        <div className="showcase-section-heading">
          <div><p className="eyebrow">WHY → HOW → WHAT</p><h2 id="journey-title">한 번의 성공 대신, 판단 과정을 남겼습니다.</h2></div>
          <p>기록값은 커밋된 개발 기록, PASS는 현재 API replay 결과입니다.</p>
        </div>
        <div className="journey-flow">
          <article>
            <b>01</b><em>RECORDED INPUT</em><i>OBSERVE</i><h3>과다 할당을 관측</h3>
            <p>1000m/2000m CPU와 2Gi/4Gi 메모리 상태에서 한 시간 동안 고정 부하를 수집했습니다.</p>
            <dl><div><dt>requests</dt><dd>{evidence.observation.requests.toLocaleString()}</dd></div><div><dt>coverage</dt><dd>{evidence.observation.usageCoveragePercent}%</dd></div></dl>
            <EvidenceLink href={evidence.sources.refinement}>관측 근거</EvidenceLink>
          </article>
          <article className="rejected">
            <b>02</b><em>RECORDED BENCHMARK</em><i>REJECT</i><h3>10m 후보를 거부</h3>
            <p>비용 절감 폭이 커도 steady P99가 정책 한계를 넘자 실패 artifact를 그대로 보존했습니다.</p>
            <dl><div><dt>CPU</dt><dd>10m / 20m</dd></div><div><dt>steady P99</dt><dd>+{evidence.rejected.steadyP99ChangePercent}%</dd></div></dl>
            <EvidenceLink href={evidence.sources.refinement}>실패 기록</EvidenceLink>
          </article>
          <article>
            <b>03</b><em>RECORDED REANALYSIS</em><i>REFINE</i><h3>정책 입력으로 재분석</h3>
            <p>새로운 유휴 구간을 고르지 않고 보존된 schema v2 입력에 단조 증가 CPU floor를 적용했습니다.</p>
            <dl><div><dt>CPU</dt><dd>20m / 40m</dd></div><div><dt>Memory</dt><dd>32Mi / 48Mi</dd></div></dl>
            <EvidenceLink href={evidence.sources.refinement}>재분석 경계</EvidenceLink>
          </article>
          <article className={verificationReady ? "verified" : "pending"}>
            <b>04</b><em>LIVE API</em><i>VERIFY</i><h3>반대 순서로 재검증</h3>
            <p>Before-first와 Candidate-first 결과를 한 Pair로 묶고 서버가 원본 artifact를 다시 계산합니다.</p>
            <dl><div><dt>policy</dt><dd>{verificationReady ? "PASS" : runState === "replaying" ? "REPLAY…" : "WAIT"}</dd></div><div><dt>checks</dt><dd>{review ? `${review.checks.length}/${review.checks.length}` : "—"}</dd></div></dl>
            {verificationReady ? <a href={`/?pair=${VERIFIED_PAIR_ID}`}>Pair 상세 보기 →</a> : <span className="locked-link">Pair replay 실행 후 열림</span>}
          </article>
          <article>
            <b>05</b><em>RECORDED GITOPS</em><i>PROPOSE</i><h3>Draft PR까지만 제안</h3>
            <p>YAML 한 파일의 수정 근거와 비용 투영을 제출했지만 merge, deploy, cluster mutation은 수행하지 않았습니다.</p>
            <dl><div><dt>cost model</dt><dd>${evidence.costProjection.currentUsd} → ${evidence.costProjection.recommendedUsd}</dd></div><div><dt>state</dt><dd>DRAFT</dd></div></dl>
            {verificationReady ? <EvidenceLink href={evidence.sources.draftPullRequest}>Draft PR #23</EvidenceLink> : <span className="locked-link">Pair replay 실행 후 열림</span>}
          </article>
        </div>
      </section>

      <section className="showcase-boundary">
        <div><p className="eyebrow">EVIDENCE BOUNDARY</p><h2>PASS가 말하지 않는 것</h2></div>
        <ul>
          <li><strong>{review ? mixed : 2}개 mixed spike 신호</strong>를 숨기지 않으며 Pair 정책 한계 안의 PASS입니다.</li>
          <li>두 번째 반복 block이 실패해 캠페인은 <strong>incomplete</strong>이며 통계적 유의성을 주장하지 않습니다.</li>
          <li>로컬 kind 고정 부하 결과이며 실제 프로덕션 트래픽·AWS 비용 절감을 입증하지 않습니다.</li>
        </ul>
        <EvidenceLink href={evidence.sources.publication}>출판 범위와 한계</EvidenceLink>
      </section>
    </main>
  );
}

export default function App() {
  const [request, setRequest] = useState(() => cloneScenario(eligibleScenario));
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [artifactReview, setArtifactReview] = useState<AnalysisReview | null>(null);
  const [benchmarkReview, setBenchmarkReview] = useState<BenchmarkReview | null>(null);
  const [pairReview, setPairReview] = useState<CounterbalancedPairReview | null>(null);
  const [campaignReview, setCampaignReview] = useState<BenchmarkCampaignReview | null>(null);
  const [loading, setLoading] = useState(false);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showcaseEvaluation, setShowcaseEvaluation] = useState<EvaluationResult | null>(null);
  const [showcaseRunState, setShowcaseRunState] = useState<ShowcaseRunState>("idle");
  const [showcaseMode] = useState(
    () => new URLSearchParams(window.location.search).get("showcase"),
  );

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search);
    const artifactId = parameters.get("benchmark");
    const pairId = parameters.get("pair");
    const campaignId = parameters.get("campaign");
    const showcaseId = parameters.get("showcase");
    if ([artifactId, pairId, campaignId, showcaseId].filter((value) => value !== null).length > 1) {
      setError("benchmark, pair, campaign, showcase 링크는 하나만 지정할 수 있습니다.");
      return;
    }
    if (showcaseId !== null) {
      if (showcaseId !== DECISION_JOURNEY_ID) {
        setError("지원하지 않는 showcase 링크입니다.");
        return;
      }
      return;
    }
    if (campaignId !== null) {
      if (!/^benchmark-campaign-evidence-[0-9a-f]{32}$/.test(campaignId)) {
        setError("benchmark campaign 링크의 artifact ID 형식이 올바르지 않습니다.");
        return;
      }
      let active = true;
      setBenchmarkLoading(true);
      fetchStoredBenchmarkCampaignReview(campaignId)
        .then((review) => {
          if (active) setCampaignReview(review);
        })
        .catch((reason: unknown) => {
          if (active) setError(reason instanceof Error ? reason.message : "저장된 benchmark campaign 검증에 실패했습니다.");
        })
        .finally(() => {
          if (active) setBenchmarkLoading(false);
        });
      return () => { active = false; };
    }
    if (pairId !== null) {
      if (!/^benchmark-pair-[0-9a-f]{32}$/.test(pairId)) {
        setError("benchmark pair 링크의 artifact ID 형식이 올바르지 않습니다.");
        return;
      }
      let active = true;
      setBenchmarkLoading(true);
      fetchStoredBenchmarkPairReview(pairId)
        .then((review) => {
          if (active) setPairReview(review);
        })
        .catch((reason: unknown) => {
          if (active) setError(reason instanceof Error ? reason.message : "저장된 benchmark pair 검증에 실패했습니다.");
        })
        .finally(() => {
          if (active) setBenchmarkLoading(false);
        });
      return () => { active = false; };
    }
    if (artifactId === null) return;
    if (!/^benchmark-[0-9a-f]{32}$/.test(artifactId)) {
      setError("benchmark 링크의 artifact ID 형식이 올바르지 않습니다.");
      return;
    }
    let active = true;
    setBenchmarkLoading(true);
    fetchStoredBenchmarkReview(artifactId)
      .then((review) => {
        if (active) setBenchmarkReview(review);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : "저장된 benchmark 결과 검증에 실패했습니다.",
          );
        }
      })
      .finally(() => {
        if (active) setBenchmarkLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const runShowcaseEvaluation = async () => {
    setError(null);
    setPairReview(null);
    setShowcaseEvaluation(null);
    setShowcaseRunState("evaluating");
    try {
      setShowcaseEvaluation(await evaluateResources(recordedInitialEvaluationRequest));
      setShowcaseRunState("candidate-ready");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Showcase 추천 계산에 실패했습니다.");
      setShowcaseRunState("failed");
    }
  };

  const runShowcasePairReplay = async () => {
    setError(null);
    setPairReview(null);
    setShowcaseRunState("replaying");
    try {
      setPairReview(await fetchStoredBenchmarkPairReview(VERIFIED_PAIR_ID));
      setShowcaseRunState("verified");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Showcase Pair 검증에 실패했습니다.");
      setShowcaseRunState("failed");
    }
  };

  const resetShowcaseDemo = () => {
    setShowcaseEvaluation(null);
    setPairReview(null);
    setError(null);
    setShowcaseRunState("idle");
  };

  const setCurrent = (key: keyof EvaluationRequest["current"], value: number) => {
    setRequest((previous) => ({
      ...previous,
      current: { ...previous.current, [key]: value },
    }));
  };

  const setObserved = (key: keyof EvaluationRequest["observed"], value: number) => {
    setRequest((previous) => ({
      ...previous,
      observed: { ...previous.observed, [key]: value },
    }));
  };

  const useScenario = (scenario: EvaluationRequest) => {
    setRequest(cloneScenario(scenario));
    setResult(null);
    setArtifactReview(null);
    setBenchmarkReview(null);
    setPairReview(null);
    setCampaignReview(null);
    setError(null);
  };

  const loadArtifact = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setResult(null);
    setArtifactReview(null);
    setError(null);
    setBenchmarkReview(null);
    setPairReview(null);
    setCampaignReview(null);
    if (file.size > MAX_ANALYSIS_ARTIFACT_BYTES) {
      setError("analysis artifact는 1 MiB 이하여야 합니다.");
      return;
    }
    setArtifactLoading(true);
    try {
      const review = await reviewAnalysisArtifact(await readFile(file));
      setArtifactReview(review);
      setResult(review.evaluation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "analysis artifact 검증에 실패했습니다.");
    } finally {
      setArtifactLoading(false);
    }
  };

  const loadBenchmark = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) return;
    setResult(null);
    setArtifactReview(null);
    setBenchmarkReview(null);
    setPairReview(null);
    setCampaignReview(null);
    setError(null);
    const selected = new Map<string, File>();
    for (const file of files) {
      const parts = file.webkitRelativePath.split("/");
      const relativePath = parts.slice(1).join("/");
      if (BENCHMARK_REVIEW_PATHS.includes(relativePath as typeof BENCHMARK_REVIEW_PATHS[number])) {
        selected.set(relativePath, file);
      }
    }
    const missing = BENCHMARK_REVIEW_PATHS.filter((path) => !selected.has(path));
    if (missing.length > 0) {
      setError(`benchmark 결과 폴더에 필수 파일이 없습니다: ${missing.join(", ")}`);
      return;
    }
    const oversized = BENCHMARK_REVIEW_PATHS.find(
      (path) => (selected.get(path)?.size ?? 0) > MAX_BENCHMARK_REVIEW_FILE_BYTES,
    );
    if (oversized) {
      setError(`benchmark 리뷰 파일은 각각 128 KiB 이하여야 합니다: ${oversized}`);
      return;
    }
    setBenchmarkLoading(true);
    try {
      const [resultJson, beforeJson, afterJson, verdictJson] = await Promise.all(
        BENCHMARK_REVIEW_PATHS.map((path) => readFile(selected.get(path)!)),
      );
      setBenchmarkReview(await reviewBenchmarkArtifact({
        result_json: resultJson,
        before_json: beforeJson,
        after_json: afterJson,
        verdict_json: verdictJson,
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "benchmark 결과 검증에 실패했습니다.");
    } finally {
      setBenchmarkLoading(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setArtifactReview(null);
    setBenchmarkReview(null);
    setPairReview(null);
    setCampaignReview(null);
    setError(null);
    try {
      setResult(await evaluateResources(request));
    } catch (reason) {
      setResult(null);
      setError(reason instanceof Error ? reason.message : "평가 요청에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const fields: Array<{
    group: "current" | "observed";
    key: NumericPath;
    label: string;
    unit?: string;
    step?: number;
  }> = [
    { group: "current", key: "cpu_request_millicores", label: "CPU request", unit: "m" },
    { group: "current", key: "cpu_limit_millicores", label: "CPU limit", unit: "m" },
    { group: "current", key: "memory_request_mib", label: "Memory request", unit: "Mi" },
    { group: "current", key: "memory_limit_mib", label: "Memory limit", unit: "Mi" },
    { group: "observed", key: "cpu_p95_millicores", label: "CPU P95", unit: "m" },
    { group: "observed", key: "cpu_max_millicores", label: "CPU max", unit: "m" },
    { group: "observed", key: "memory_p99_mib", label: "Memory P99", unit: "Mi" },
    { group: "observed", key: "memory_max_mib", label: "Memory max", unit: "Mi" },
    { group: "observed", key: "sample_count", label: "Usage samples" },
    { group: "observed", key: "observation_coverage", label: "Usage coverage", unit: "0–1", step: 0.01 },
    { group: "observed", key: "cpu_throttling_p95_percent", label: "Throttling P95", unit: "%", step: 0.1 },
    { group: "observed", key: "cpu_throttling_sample_count", label: "Throttle samples" },
    { group: "observed", key: "restart_count", label: "Restarts" },
    { group: "observed", key: "oom_killed_count", label: "OOMKilled" },
  ];

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="KubeFit 홈">
          <span>K</span>KubeFit
        </a>
        <div className="principle"><i /> cluster mutation: off</div>
      </header>
      {showcaseMode === DECISION_JOURNEY_ID ? (
        <DecisionJourney
          review={pairReview}
          evaluation={showcaseEvaluation}
          runState={showcaseRunState}
          error={error}
          onEvaluate={runShowcaseEvaluation}
          onReplay={runShowcasePairReplay}
          onReset={resetShowcaseDemo}
        />
      ) : <><div className="hero" id="top">
        <div>
          <p className="eyebrow">EXPLAINABLE · GITOPS-FIRST</p>
          <h1>줄여도 되는 이유를<br />먼저 보여줍니다.</h1>
          <p className="hero-copy">
            실제 관측값으로 requests/limits를 계산하고, 비용과 위험을 함께 검토한 뒤에만 변경안을 제안합니다.
          </p>
        </div>
        <div className="hero-rule">
          <span>01</span><p>Measure</p><span>02</span><p>Explain</p><span>03</span><p>Review</p>
        </div>
      </div>
      <div className="workspace">
        <aside className="input-column">
          <form className="panel input-panel" onSubmit={submit}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">SCENARIO INPUT</p>
                <h2>워크로드 관측값</h2>
              </div>
            </div>
            <label className="artifact-loader">
              <span className="artifact-loader-icon">↥</span>
              <span>
                <strong>{artifactLoading ? "artifact 검증 중…" : "analysis.json 불러오기"}</strong>
                <small>kubefit analyze 출력 · 최대 1 MiB</small>
              </span>
              <input
                aria-label="analysis artifact JSON"
                type="file"
                accept="application/json,.json"
                disabled={artifactLoading}
                onChange={loadArtifact}
              />
            </label>
            <label className="artifact-loader benchmark-loader">
              <span className="artifact-loader-icon">↥</span>
              <span>
                <strong>{benchmarkLoading ? "benchmark 검증 중…" : "benchmark 결과 폴더 불러오기"}</strong>
                <small>인덱스 결합 측정값 · raw k6 파일은 읽지 않음</small>
              </span>
              <input
                aria-label="benchmark result directory"
                type="file"
                multiple
                disabled={benchmarkLoading}
                onChange={loadBenchmark}
                {...{ webkitdirectory: "" }}
              />
            </label>
            <div className="input-divider"><span>또는 예제 입력</span></div>
            <div className="scenario-switch" aria-label="예제 시나리오">
              <button type="button" onClick={() => useScenario(eligibleScenario)}>검증 가능</button>
              <button type="button" onClick={() => useScenario(insufficientScenario)}>근거 부족</button>
            </div>
            <fieldset>
              <legend>현재 설정</legend>
              <div className="field-grid">
                {fields.filter(({ group }) => group === "current").map((field) => (
                  <NumberField
                    key={field.key}
                    label={field.label}
                    value={request.current[field.key as keyof EvaluationRequest["current"]]}
                    unit={field.unit}
                    step={field.step}
                    min={1}
                    onChange={(value) => setCurrent(field.key as keyof EvaluationRequest["current"], value)}
                  />
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend>Prometheus · Kubernetes 근거</legend>
              <div className="field-grid">
                {fields.filter(({ group }) => group === "observed").map((field) => (
                  <NumberField
                    key={field.key}
                    label={field.label}
                    value={request.observed[field.key as keyof EvaluationRequest["observed"]] as number | undefined}
                    unit={field.unit}
                    step={field.step}
                    required={field.key === "cpu_p95_millicores" || field.key === "memory_p99_mib"}
                    onChange={(value) => setObserved(field.key as keyof EvaluationRequest["observed"], value)}
                  />
                ))}
              </div>
            </fieldset>
            <button className="evaluate-button" type="submit" disabled={loading}>
              {loading ? "평가 중…" : "추천과 위험 계산"}<span>→</span>
            </button>
            {error && <p className="error" role="alert">{error}</p>}
          </form>
        </aside>
        {campaignReview ? <CampaignResults review={campaignReview} /> : pairReview ? <PairResults review={pairReview} /> : benchmarkReview ? <BenchmarkResults review={benchmarkReview} /> : result ? <Results result={result} review={artifactReview} /> : (
          <main className="empty-state">
            <span>↳</span>
            <h2>아직 계산하지 않았습니다.</h2>
            <p>샘플을 선택하거나 관측값을 조정한 뒤 평가를 실행하세요. 결과는 기존 KubeFit API가 계산합니다.</p>
          </main>
        )}
      </div></>}
      <footer>OPEN SOURCE · HUMAN-APPROVED OPTIMIZATION</footer>
    </div>
  );
}
