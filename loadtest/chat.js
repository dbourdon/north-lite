// k6 load test for north-lite: a mix of search and RAG chat, the two hot paths.
//
//   BASE_URL=http://127.0.0.1:8000 k6 run loadtest/chat.js
//   SUMMARY=loadtest/out/full.json k6 run loadtest/chat.js   # where to write the JSON summary
//
// Absolute thresholds (k6 fails the run): p95 < 300 ms, errors < 1%.
// Regression against a baseline is the bench-gate action's job, not k6's.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://127.0.0.1:8000";
const HEADERS = { headers: { "Content-Type": "application/json" } };

const QUESTIONS = [
  "How long are CI build logs kept?",
  "When does the on-call shift hand over?",
  "Can I deploy on a Friday?",
  "What p95 latency regression blocks a release?",
  "How often are API keys rotated?",
];

export const options = {
  scenarios: {
    steady: {
      // Fixed arrival rate, not fixed VUs: a slower server then shows up as
      // higher latency instead of silently doing less work.
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 20), // requests per second
      timeUnit: "1s",
      duration: __ENV.DURATION || "30s",
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<300"],
    http_req_failed: ["rate<0.01"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  const q = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  // 2:1 search vs chat, roughly what a help assistant sees.
  if (Math.random() < 2 / 3) {
    const r = http.post(`${BASE}/search`, JSON.stringify({ query: q, top_n: 3 }), {
      ...HEADERS,
      tags: { endpoint: "search" },
    });
    check(r, { "search 200": (res) => res.status === 200 });
  } else {
    const body = { messages: [{ role: "user", content: q }], rag: true };
    const r = http.post(`${BASE}/chat`, JSON.stringify(body), { ...HEADERS, tags: { endpoint: "chat" } });
    check(r, { "chat 200": (res) => res.status === 200 });
  }
}

// Compact, stable JSON for the bench-gate action (k6's full summary format is
// verbose and has changed between versions).
export function handleSummary(data) {
  const d = data.metrics.http_req_duration.values;
  const failed = data.metrics.http_req_failed.values;
  const summary = {
    schema: 1,
    requests: data.metrics.http_reqs.values.count,
    rps: data.metrics.http_reqs.values.rate,
    error_rate: failed.rate,
    latency_ms: { avg: d.avg, p50: d.med, p90: d["p(90)"], p95: d["p(95)"], p99: d["p(99)"], max: d.max },
  };
  const out = { stdout: `\n${JSON.stringify(summary, null, 2)}\n` };
  if (__ENV.SUMMARY) out[__ENV.SUMMARY] = JSON.stringify(summary, null, 2);
  return out;
}
