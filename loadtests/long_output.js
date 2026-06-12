// Scenario: long_output — moderate input with long generation.
//
// Purpose: isolate decode/TPOT pressure. The prompt is modest, but max_tokens
// is large enough to stress output token throughput and tail latency.

import { sleep } from 'k6';
import { SUMMARY_TREND_STATS, buildPayloadByTokens, chatCompletions, checkOk } from './lib/common.js';

const INPUT_TOKENS = Number(__ENV.LONG_OUTPUT_INPUT_TOKENS || '300');
const MAX_TOKENS = Number(__ENV.LONG_OUTPUT_MAX_TOKENS || '1000');
const VUS = Number(__ENV.LONG_OUTPUT_VUS || '4');
const DURATION = __ENV.LONG_OUTPUT_DURATION || '3m';

export const options = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    long_output: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.10'],
  },
};

export default function () {
  const payload = buildPayloadByTokens({
    inputTokens: INPUT_TOKENS,
    maxTokens: MAX_TOKENS,
    promptPrefix: 'brief',
  });
  const res = chatCompletions(payload, {
    scenario_type: 'long_output',
    prompt_type: 'medium',
    output_type: 'long_output',
  });
  checkOk(res);
  sleep(0.5);
}
