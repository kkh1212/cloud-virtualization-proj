// Scenario: long_prompt — long-context, large-output requests.
//
// Purpose: with mock-llm's per-output-token coefficient dominating, each
// request spends 4-5 seconds in the decode phase. Five concurrent VUs fit
// inside the 8-slot capacity, so this scenario stresses *latency* without
// driving queue depth. In Phase 8 the analyzer should observe high
// p95 latency with low requests_waiting — distinct from short_prompt's
// pattern.
//
// On real GPU later (vLLM), this is also the scenario most likely to hit
// KV-cache pressure; the mock can't reproduce that, but the analyzer's
// GPU-memory rule will activate once those metrics appear.
//
// Run:
//   k6 run loadtests/long_prompt.js

import { sleep } from 'k6';
import { SUMMARY_TREND_STATS, buildPayloadByTokens, chatCompletions, checkOk } from './lib/common.js';

export const options = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    long_prompt: {
      executor: 'constant-vus',
      vus: 5,
      duration: '2m',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<10000'],
  },
};

export default function () {
  const payload = buildPayloadByTokens({
    inputTokens: 1000,
    maxTokens: 512,
    promptPrefix: 'legacy',
  });
  const res = chatCompletions(payload, {
    scenario_type: 'long_prompt',
    prompt_type: 'long',
    output_type: 'long_output',
  });
  checkOk(res);
  sleep(0.5);
}
