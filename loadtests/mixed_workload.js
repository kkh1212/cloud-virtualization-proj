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

import { SUMMARY_TREND_STATS, buildPayloadByTokens, buildRagPayload, chatCompletions, checkOk, expSleep, pickWeighted } from './lib/common.js';

// Weighted mix. Weights are relative, not percentages.
const WORKLOADS = [
  {
    weight: 60,
    value: {
      label: 'short_qa',
      tags: { scenario_type: 'mixed', prompt_type: 'short', output_type: 'short_output' },
      payload: () => buildPayloadByTokens({ inputTokens: 100, maxTokens: 100 }),
    },
  },
  {
    weight: 25,
    value: {
      label: 'rag_like',
      tags: { scenario_type: 'mixed', prompt_type: 'rag', output_type: 'medium_output' },
      payload: () => buildRagPayload({ questionTokens: 50, contextTokens: 2500, maxTokens: 500 }),
    },
  },
  {
    weight: 15,
    value: {
      label: 'long_generation',
      tags: { scenario_type: 'mixed', prompt_type: 'medium', output_type: 'long_output' },
      payload: () => buildPayloadByTokens({ inputTokens: 300, maxTokens: 900, promptPrefix: 'generate' }),
    },
  },
];

// Mean think-time between a VU's requests, in seconds (exponential).
const THINK_MEAN_S = Number(__ENV.MIXED_THINK_MEAN_S || '0.5');

export const options = {
  summaryTrendStats: SUMMARY_TREND_STATS,
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
    // High ceilings keep the scenario advisory while forcing k6 to include
    // tag-specific submetrics in summary exports.
    'http_req_duration{prompt_type:short}': ['p(95)<60000'],
    'http_req_duration{prompt_type:rag}': ['p(95)<60000'],
    'http_req_duration{output_type:long_output}': ['p(95)<60000'],
  },
};

export default function () {
  const shape = pickWeighted(WORKLOADS);
  const res = chatCompletions(shape.payload(), shape.tags);
  checkOk(res);
  expSleep(THINK_MEAN_S);
}
