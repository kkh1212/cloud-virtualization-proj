// Shared helpers for k6 scenarios against the mock LLM service.
//
// BASE_URL is read from the BASE_URL env var so the same scripts can hit:
//   - port-forward (default): http://localhost:8000
//   - in-cluster Service:     http://mock-llm.llm-ops.svc.cluster.local:8000
//
// Token counting on the server is whitespace-split, so building prompts out
// of a fixed-length word lets us approximate "prompt_tokens ≈ promptChars/6".

import http from 'k6/http';
import { check, sleep } from 'k6';

export const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
export const HEADERS = { 'Content-Type': 'application/json' };

// Repeats "token " (6 chars incl. trailing space) until length ≈ approxChars.
export function buildPrompt(approxChars) {
  const word = 'token ';
  const n = Math.max(1, Math.floor(approxChars / word.length));
  return word.repeat(n).trim();
}

export function buildPayload({ promptChars = 50, maxTokens = 64, role = 'user' } = {}) {
  return JSON.stringify({
    model: 'mock',
    messages: [{ role, content: buildPrompt(promptChars) }],
    max_tokens: maxTokens,
  });
}

export function chatCompletions(payload) {
  return http.post(`${BASE_URL}/v1/chat/completions`, payload, { headers: HEADERS });
}

// Exponential (Poisson-process) inter-arrival sleep; mean is in seconds.
// -mean*ln(1-U) yields exponentially distributed gaps, so per-VU arrivals
// approximate a Poisson process instead of a fixed cadence — closer to how
// real users hit a service.
export function expSleep(meanSeconds) {
  const gap = -meanSeconds * Math.log(1 - Math.random());
  sleep(gap);
}

// Weighted random choice. items: [{ weight, value }, ...]. Returns a value.
export function pickWeighted(items) {
  const total = items.reduce((acc, it) => acc + it.weight, 0);
  let r = Math.random() * total;
  for (const it of items) {
    r -= it.weight;
    if (r <= 0) return it.value;
  }
  return items[items.length - 1].value;
}

// Standard success check used by every scenario; results show up in the
// k6 summary as "checks" pass/fail counts, separate from http_req_failed.
//
// Depth: enough to catch a regression that would otherwise pass silently
// (empty assistant content, missing usage block, broken token counting).
// Stops short of asserting exact prompt_tokens because that would couple
// the load tests to the simulator's whitespace-split heuristic.
export function checkOk(res) {
  return check(res, {
    'status is 200': (r) => r.status === 200,
    'has non-empty content': (r) => {
      try {
        const body = r.json();
        if (!body || !Array.isArray(body.choices) || body.choices.length === 0) return false;
        const content = body.choices[0].message && body.choices[0].message.content;
        return typeof content === 'string' && content.length > 0;
      } catch (_e) {
        return false;
      }
    },
    'has usage tokens': (r) => {
      try {
        const u = r.json('usage');
        return u
            && typeof u.prompt_tokens === 'number'     && u.prompt_tokens     >= 0
            && typeof u.completion_tokens === 'number' && u.completion_tokens > 0
            && typeof u.total_tokens === 'number'      && u.total_tokens      > 0;
      } catch (_e) {
        return false;
      }
    },
  });
}
