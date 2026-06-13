// Scenario: json_extraction — structured extraction / classification at high RPS.
//
// Models a backend automation workload (CRM/email field extraction, ticket
// routing, intent classification): short-to-medium input, a *tiny* output
// (a JSON object or a label), and a high request rate. Unlike the interactive
// scenarios this is throughput/queue-bound, not decode-bound — so it stresses
// scheduling, queue depth and p99 stability rather than TTFT/TPOT.
//
// Uses constant-arrival-rate so the offered load is an explicit RPS target
// (EXTRACT_RATE), matching the workload ladder's `load_unit: rps`. As a rung's
// rate climbs past capacity, requests_waiting / queue_wait / p99 should rise —
// the knee the session aggregator reports as the safe extraction throughput.
//
// Run:
//   k6 run loadtests/json_extraction.js
//   EXTRACT_RATE=100 k6 run loadtests/json_extraction.js

import { SUMMARY_TREND_STATS, buildPayloadByTokens, chatCompletions, checkOk } from './lib/common.js';

const RATE = Number(__ENV.EXTRACT_RATE || '20');           // target requests/sec
const DURATION = __ENV.EXTRACT_DURATION || '2m';
const INPUT_TOKENS = Number(__ENV.EXTRACT_INPUT_TOKENS || '300');  // short/medium doc/ticket
const MAX_TOKENS = Number(__ENV.EXTRACT_MAX_TOKENS || '64');       // tiny JSON / label

export const options = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    json_extraction: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      // Sized so k6 can sustain the rate while requests queue server-side
      // rather than starving the generator of VUs.
      preAllocatedVUs: Math.max(50, RATE * 2),
      maxVUs: Math.max(200, RATE * 10),
    },
  },
  thresholds: {
    // Advisory only: the analyzer's Prometheus evidence (p99, queue_wait,
    // requests_waiting) is the real signal. Guard against mass failure.
    http_req_failed: ['rate<0.10'],
  },
};

export default function () {
  const payload = buildPayloadByTokens({
    inputTokens: INPUT_TOKENS,
    maxTokens: MAX_TOKENS,
    promptPrefix: 'field',
  });
  const res = chatCompletions(payload, {
    scenario_type: 'json_extraction',
    prompt_type: 'structured',
    output_type: 'json',
  });
  checkOk(res);
}
