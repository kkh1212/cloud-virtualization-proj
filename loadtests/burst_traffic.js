// Scenario: burst_traffic — sudden RPS spike to expose queue bottleneck.
//
// Purpose: drive arrival rate from 10 RPS to 200 RPS over 15s, sustain
// the spike for 30s, then drop back. With 8 concurrency slots in total
// and ~0.7s per short request, sustainable throughput is ~11 RPS; 200
// RPS is ~18× over capacity, so requests_waiting will climb sharply,
// p95 latency will explode, and queue_timeout errors will appear once
// MOCK_LLM_QUEUE_TIMEOUT_S is reached.
//
// This is the primary input for the analyzer's queue_bottleneck rule
// (Phase 8). In Phase 7 (HPA), the same scenario will also exercise
// scale_out_lag: replicas should rise during the spike but Pod Ready
// transition takes ~5s, so the analyzer can observe the gap.
//
// Run:
//   k6 run loadtests/burst_traffic.js

import { buildPayload, chatCompletions, checkOk } from './lib/common.js';

// Two intensity levels:
//   normal (default) — spike to 80 RPS (~10× capacity at 8 slots).
//                       maxVUs 1500. Safe to run on a dev VM without the
//                       k6 client itself becoming the bottleneck.
//   high             — spike to 200 RPS (~25× capacity).
//                       maxVUs 7000. Opt-in only when you know the host
//                       can handle thousands of concurrent sockets/FDs;
//                       otherwise k6 client saturation will be mixed
//                       into the analyzer's evidence.
// Select with:  BURST_INTENSITY=high k6 run loadtests/burst_traffic.js
const INTENSITY = (__ENV.BURST_INTENSITY || 'normal').toLowerCase();
const PROFILE = INTENSITY === 'high'
  ? { spikeRate: 200, preAllocatedVUs: 200, maxVUs: 7000 }
  : { spikeRate: 80,  preAllocatedVUs: 50,  maxVUs: 1500 };

export const options = {
  scenarios: {
    burst: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: PROFILE.preAllocatedVUs,
      maxVUs: PROFILE.maxVUs,
      stages: [
        { target: 10,                 duration: '30s' },  // warmup at baseline
        { target: PROFILE.spikeRate,  duration: '15s' },  // ramp up to spike
        { target: PROFILE.spikeRate,  duration: '30s' },  // sustained spike
        { target: 10,                 duration: '15s' },  // ramp down
        { target: 10,                 duration: '30s' },  // cooldown / recovery observation
      ],
    },
  },
  // Thresholds intentionally omitted: this scenario is destructive by
  // design. A failing k6 threshold here would mask the analyzer's real
  // evidence, which lives in Prometheus (mock_llm_requests_waiting,
  // mock_llm_errors_total{reason="queue_timeout"}).
};

export default function () {
  const payload = buildPayload({ promptChars: 100, maxTokens: 64 });
  const res = chatCompletions(payload);
  checkOk(res);
}
