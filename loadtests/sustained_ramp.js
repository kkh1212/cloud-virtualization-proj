// Scenario: sustained_ramp — slow multi-minute climb to test autoscaling.
//
// burst_traffic spikes in 15s — too fast for an autoscaler to react, so it
// mainly exposes the *failure* mode (queue bottleneck, scale_out_lag). This
// scenario instead ramps arrival rate up gradually over several minutes, holds
// at a peak above single-replica capacity, then ramps down. That gives CPU HPA
// / KEDA time to actually scale out and back in, so the analyzer can observe
// whether autoscaling kept latency/queue within bounds (the "appropriate
// autoscaling" question this project is about).
//
// Capacity reference: 8 slots at min replicas (4 concurrency × 2). Peak rate
// defaults to 40 RPS (~edge of 8-replica capacity), so scaling is required to
// keep up but achievable — the interesting regime for HPA vs KEDA comparison.
//
// Tunables:
//   RAMP_PEAK_RATE  peak arrival rate in RPS (default 40)
//   RAMP_CLIMB      climb duration            (default 5m)
//   RAMP_HOLD       hold-at-peak duration     (default 3m)
//
// Run:
//   k6 run loadtests/sustained_ramp.js
//   RAMP_PEAK_RATE=60 k6 run loadtests/sustained_ramp.js

import { SUMMARY_TREND_STATS, buildPayloadByTokens, chatCompletions, checkOk } from './lib/common.js';

const PEAK = Number(__ENV.RAMP_PEAK_RATE || '40');
const CLIMB = __ENV.RAMP_CLIMB || '5m';
const HOLD = __ENV.RAMP_HOLD || '3m';

export const options = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    sustained_ramp: {
      executor: 'ramping-arrival-rate',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 2000,
      stages: [
        { target: 5,    duration: '1m' },    // warmup at baseline
        { target: PEAK, duration: CLIMB },    // gradual climb — autoscaler should react here
        { target: PEAK, duration: HOLD },     // hold at peak — observe steady-state scaling
        { target: 5,    duration: '2m' },     // ramp down — observe scale-in
      ],
    },
  },
  // No strict thresholds: the analyzer's Prometheus evidence (latency, waiting,
  // replicas desired/ready) is the real output, and a failing k6 threshold
  // would mask it.
};

export default function () {
  const payload = buildPayloadByTokens({ inputTokens: 100, maxTokens: 100 });
  const res = chatCompletions(payload, {
    scenario_type: 'sustained_ramp',
    prompt_type: 'short',
    output_type: 'short_output',
  });
  checkOk(res);
}
