// Scenario: long_input — long context with bounded output.
//
// Purpose: isolate prefill/TTFT pressure. The request carries thousands of
// input tokens but only asks for a medium-length answer, so queue behavior can
// be separated from context-processing cost.

import { sleep } from 'k6';
import { SUMMARY_TREND_STATS, buildPayloadByTokens, chatCompletions, checkOk } from './lib/common.js';

const INPUT_TOKENS = Number(__ENV.LONG_INPUT_TOKENS || '4000');
const MAX_TOKENS = Number(__ENV.LONG_INPUT_MAX_TOKENS || '300');
const VUS = Number(__ENV.LONG_INPUT_VUS || '4');
const DURATION = __ENV.LONG_INPUT_DURATION || '3m';

export const options = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    long_input: {
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
    promptPrefix: 'context',
  });
  const res = chatCompletions(payload, {
    scenario_type: 'long_input',
    prompt_type: 'long',
    output_type: 'medium_output',
  });
  checkOk(res);
  sleep(0.5);
}
