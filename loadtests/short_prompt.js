// Scenario: short_prompt — baseline constant-VU load.
//
// Purpose: keep a steady population of users sending small requests for 2
// minutes. With the default mock-llm config (MAX_CONCURRENCY=4 × 2 replicas
// = 8 slots), 6 VUs leave headroom so this remains a stable "happy path"
// baseline. Queue-heavy behavior is covered by burst_traffic.
//
// Run:
//   k6 run loadtests/short_prompt.js
//   BASE_URL=http://otherhost:8000 k6 run loadtests/short_prompt.js

import { sleep } from 'k6';
import { buildPayload, chatCompletions, checkOk } from './lib/common.js';

export const options = {
  scenarios: {
    short_prompt: {
      executor: 'constant-vus',
      vus: 6,
      duration: '2m',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<3000'],
  },
};

export default function () {
  const payload = buildPayload({ promptChars: 50, maxTokens: 64 });
  const res = chatCompletions(payload);
  checkOk(res);
  // Small think-time so VUs aren't pure tight loops; keeps RPS in a realistic band.
  sleep(0.2);
}
