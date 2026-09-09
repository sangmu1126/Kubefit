import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const profileVersion = "kubefit-load-v1";
const targetUrl = requiredEnv("KUBEFIT_TARGET_URL");
const proposalId = __ENV.KUBEFIT_PROPOSAL_ID;
const changeId = __ENV.KUBEFIT_CHANGE_ID;
const variant = requiredEnv("KUBEFIT_VARIANT");
const summaryPath = requiredEnv("KUBEFIT_SUMMARY_PATH");
const recoveryStart = new Counter("kubefit_recovery_start");

if (Boolean(proposalId) === Boolean(changeId)) {
  throw new Error("exactly one of KUBEFIT_PROPOSAL_ID or KUBEFIT_CHANGE_ID is required");
}
if (proposalId && !/^proposal-[0-9a-f]{32}$/.test(proposalId)) {
  throw new Error("KUBEFIT_PROPOSAL_ID must match proposal- followed by 32 lowercase hex digits");
}
if (changeId && !/^change-[0-9a-f]{32}$/.test(changeId)) {
  throw new Error("KUBEFIT_CHANGE_ID must match change- followed by 32 lowercase hex digits");
}
if (variant !== "before" && variant !== "after") {
  throw new Error("KUBEFIT_VARIANT must be before or after");
}

export const options = {
  discardResponseBodies: true,
  systemTags: ["status", "method", "scenario"],
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
  scenarios: {
    warmup: phase("warmup", 1, "10s", "0s"),
    steady: phase("steady", 5, "60s", "10s"),
    spike: phase("spike", 25, "30s", "70s"),
    recovery: phase("recovery", 5, "60s", "100s"),
  },
  thresholds: {
    "http_req_duration{kubefit_phase:steady}": ["p(99)<60000"],
    "http_req_duration{kubefit_phase:spike}": ["p(99)<60000"],
    "http_req_duration{kubefit_phase:recovery}": ["p(99)<60000"],
    "http_req_failed{kubefit_phase:steady}": ["rate<=1"],
    "http_req_failed{kubefit_phase:spike}": ["rate<=1"],
    "http_req_failed{kubefit_phase:recovery}": ["rate<=1"],
    "http_reqs{kubefit_phase:steady}": ["count>=0"],
    "http_reqs{kubefit_phase:spike}": ["count>=0"],
    "http_reqs{kubefit_phase:recovery}": ["count>=0"],
    "iterations{kubefit_phase:steady}": ["count>=0"],
    "iterations{kubefit_phase:spike}": ["count>=0"],
    "iterations{kubefit_phase:recovery}": ["count>=0"],
  },
};

function phase(name, rate, duration, startTime) {
  return {
    executor: "constant-arrival-rate",
    exec: name,
    rate,
    timeUnit: "1s",
    duration,
    startTime,
    preAllocatedVUs: Math.max(rate, 5),
    maxVUs: Math.max(rate * 4, 20),
    tags: { kubefit_phase: name },
  };
}

function request(phaseName) {
  const response = http.get(targetUrl, { tags: { kubefit_phase: phaseName } });
  check(response, { "status is below 500": (result) => result.status < 500 });
}

export function warmup() {
  request("warmup");
}

export function steady() {
  request("steady");
}

export function spike() {
  request("spike");
}

export function recovery() {
  recoveryStart.add(1, { kubefit_phase: "recovery" });
  request("recovery");
}

export function handleSummary(data) {
  const result = {
    schema_version: 1,
    profile_version: profileVersion,
    ...(proposalId ? { proposal_id: proposalId } : { change_id: changeId }),
    variant,
    dropped_iterations: metricValue(data, "dropped_iterations", "count", 0),
    steady: phaseSummary(data, "steady", 300),
    spike: phaseSummary(data, "spike", 750),
    recovery: phaseSummary(data, "recovery", 300),
  };
  return { [summaryPath]: `${JSON.stringify(result, null, 2)}\n` };
}

function phaseSummary(data, phaseName, expectedIterations) {
  return {
    expected_iterations: expectedIterations,
    completed_iterations: metricValue(data, tagged("iterations", phaseName), "count"),
    requests: metricValue(data, tagged("http_reqs", phaseName), "count"),
    error_rate: metricValue(data, tagged("http_req_failed", phaseName), "rate"),
    latency_p95_ms: metricValue(data, tagged("http_req_duration", phaseName), "p(95)"),
    latency_p99_ms: metricValue(data, tagged("http_req_duration", phaseName), "p(99)"),
  };
}

function tagged(metric, phaseName) {
  return `${metric}{kubefit_phase:${phaseName}}`;
}

function metricValue(data, metricName, valueName, fallback) {
  const metric = data.metrics[metricName];
  if (metric && metric.values[valueName] !== undefined) {
    return metric.values[valueName];
  }
  if (fallback !== undefined) {
    return fallback;
  }
  throw new Error(`k6 summary is missing ${metricName}.${valueName}`);
}

function requiredEnv(name) {
  const value = __ENV[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}
