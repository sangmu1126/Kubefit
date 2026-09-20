import http from "k6/http";

const targetUrl = __ENV.KUBEFIT_TARGET_URL;
if (!targetUrl) {
  throw new Error("KUBEFIT_TARGET_URL is required");
}

export const options = {
  discardResponseBodies: true,
  scenarios: {
    smoke: {
      executor: "constant-arrival-rate",
      rate: 5,
      timeUnit: "1s",
      duration: "15s",
      preAllocatedVUs: 5,
      maxVUs: 10,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(99)<1000"],
    dropped_iterations: ["count==0"],
  },
};

export default function () {
  http.get(targetUrl);
}

export function handleSummary(data) {
  return {
    stdout: `${JSON.stringify({
      smoke_only: true,
      requests: data.metrics.http_reqs?.values.count ?? 0,
      dropped_iterations: data.metrics.dropped_iterations?.values.count ?? 0,
      error_rate: data.metrics.http_req_failed?.values.rate ?? 0,
    })}\n`,
  };
}
