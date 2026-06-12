// Scenario: rag_like — short question plus long retrieved context.
//
// Purpose: mimic RAG services where user input is short but retrieved chunks
// dominate the prompt. This should look like long_input in TTFT/prefill while
// keeping the user-facing question shape realistic.

import { sleep } from 'k6';
import { SUMMARY_TREND_STATS, buildRagPayload, chatCompletions, checkOk } from './lib/common.js';

const QUESTION_TOKENS = Number(__ENV.RAG_QUESTION_TOKENS || '50');
const CONTEXT_TOKENS = Number(__ENV.RAG_CONTEXT_TOKENS || '4000');
const MAX_TOKENS = Number(__ENV.RAG_MAX_TOKENS || '500');
const VUS = Number(__ENV.RAG_VUS || '4');
const DURATION = __ENV.RAG_DURATION || '3m';

export const options = {
  summaryTrendStats: SUMMARY_TREND_STATS,
  scenarios: {
    rag_like: {
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
  const payload = buildRagPayload({
    questionTokens: QUESTION_TOKENS,
    contextTokens: CONTEXT_TOKENS,
    maxTokens: MAX_TOKENS,
  });
  const res = chatCompletions(payload, {
    scenario_type: 'rag_like',
    prompt_type: 'rag',
    output_type: 'medium_output',
  });
  checkOk(res);
  sleep(0.5);
}
