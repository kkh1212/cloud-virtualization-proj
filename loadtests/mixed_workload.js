// Scenario: mixed_workload — realistic multi-modal LLM traffic.
//
// Real LLM services rarely see one prompt shape. This scenario keeps a steady
// population of VUs, each request randomly drawn (weighted) from three shapes:
//   - short QA          (small prompt, small output)  — the bulk of traffic
//   - long summarization(large prompt, large output)  — heavy decode + context
//   - code generation   (medium prompt, large output) — decode-heavy
// Per-VU think-time is exponential (Poisson-ish arrivals) rather than a fixed
// cadence, so the aggregate arrival pattern is bursty like real usage.
//
// Purpose: exercise the analyzer/LLM metrics under heterogeneous load — TTFT,
// inter-token latency and KV-cache proxy will vary by shape, and p95/p99 will
// reflect a realistic long tail driven by the large-output requests.
//
// Run:
//   k6 run loadtests/mixed_workload.js
//   BASE_URL=http://otherhost:8000 k6 run loadtests/mixed_workload.js

import { buildPayload, chatCompletions, checkOk, expSleep, pickWeighted } from './lib/common.js';

// Weighted mix. Weights are relative, not percentages.
const WORKLOADS = [
  { weight: 60, value: { promptChars: 50,   maxTokens: 64,  label: 'short_qa' } },
  { weight: 25, value: { promptChars: 2000, maxTokens: 512, label: 'long_summary' } },
  { weight: 15, value: { promptChars: 400,  maxTokens: 256, label: 'code_gen' } },
];

// Mean think-time between a VU's requests, in seconds (exponential).
const THINK_MEAN_S = Number(__ENV.MIXED_THINK_MEAN_S || '0.5');

export const options = {
  scenarios: {
    mixed_workload: {
      executor: 'constant-vus',
      vus: Number(__ENV.MIXED_VUS || '8'),
      duration: __ENV.MIXED_DURATION || '3m',
    },
  },
  thresholds: {
    // Lenient: this is a heterogeneous mix, so a single tight latency bound
    // would be meaningless. We still guard against mass failure.
    http_req_failed: ['rate<0.10'],
  },
};

export default function () {
  const shape = pickWeighted(WORKLOADS);
  const payload = buildPayload({ promptChars: shape.promptChars, maxTokens: shape.maxTokens });
  const res = chatCompletions(payload);
  checkOk(res);
  expSleep(THINK_MEAN_S);
}
