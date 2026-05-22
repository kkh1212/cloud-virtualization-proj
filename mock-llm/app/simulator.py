"""Async simulator that fakes LLM decoding latency and queue back-pressure.

Design notes:
  * `asyncio.Semaphore(max_concurrency)` caps concurrent "running" requests.
    Excess callers genuinely await on `.acquire()`, so `waiting` reflects real
    queue depth rather than a synthetic counter.
  * The simulator owns plain int counters (`running`, `waiting`); the FastAPI
    layer mirrors them into Prometheus gauges. Tests can read them directly.
  * Counter mutations occur in code regions with no `await`, so no lock is
    needed — they are atomic under asyncio's single-threaded scheduler.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    """Whitespace-split token estimation. tiktoken is overkill for fake delays."""
    if not text:
        return 0
    return len(text.split())


@dataclass
class CompletionResult:
    output_text: str
    prompt_tokens: int
    output_tokens: int
    queue_wait_s: float
    decode_s: float
    ttft_s: float
    tpot_s: float
    tokens_per_s: float


class QueueTimeout(Exception):
    """Raised when a request waits longer than queue_timeout_s for a slot."""


class LLMSimulator:
    def __init__(
        self,
        max_concurrency: int,
        base_latency_ms: int,
        per_prompt_token_ms: float,
        per_output_token_ms: float,
        queue_timeout_s: float,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)
        self.max_concurrency = max_concurrency
        self._base_latency_ms = base_latency_ms
        self._per_prompt_token_ms = per_prompt_token_ms
        self._per_output_token_ms = per_output_token_ms
        self._queue_timeout_s = queue_timeout_s

        self.running = 0
        self.waiting = 0

    def estimate_delay_s(self, prompt_tokens: int, max_tokens: int) -> float:
        ms = (
            self._base_latency_ms
            + prompt_tokens * self._per_prompt_token_ms
            + max_tokens * self._per_output_token_ms
        )
        return ms / 1000.0

    def estimate_phases_s(
        self, prompt_tokens: int, max_tokens: int
    ) -> tuple[float, float, float, float]:
        """Split the fake generation into prefill (TTFT) and decode phases.

        Returns (ttft_s, decode_remaining_s, total_s, tpot_s). ttft covers
        prefill (base + prompt) plus the first output token; the remaining
        tokens make up decode. total_s equals estimate_delay_s so end-to-end
        latency is unchanged — we only expose the LLM-serving decomposition.
        """
        prefill_ms = self._base_latency_ms + prompt_tokens * self._per_prompt_token_ms
        ttft_ms = prefill_ms + self._per_output_token_ms
        remaining_ms = max(0, max_tokens - 1) * self._per_output_token_ms
        ttft_s = ttft_ms / 1000.0
        remaining_s = remaining_ms / 1000.0
        tpot_s = self._per_output_token_ms / 1000.0
        return ttft_s, remaining_s, ttft_s + remaining_s, tpot_s

    async def generate(self, prompt: str, max_tokens: int) -> CompletionResult:
        prompt_tokens = estimate_tokens(prompt)
        ttft_s, remaining_s, total_s, tpot_s = self.estimate_phases_s(
            prompt_tokens, max_tokens
        )

        loop = asyncio.get_running_loop()
        wait_started = loop.time()

        self.waiting += 1
        try:
            try:
                await asyncio.wait_for(
                    self._sem.acquire(), timeout=self._queue_timeout_s
                )
            except asyncio.TimeoutError as exc:
                raise QueueTimeout(
                    f"queue_timeout_s={self._queue_timeout_s}"
                ) from exc
        finally:
            self.waiting -= 1

        queue_wait_s = loop.time() - wait_started
        self.running += 1
        try:
            await asyncio.sleep(ttft_s)       # prefill + first token
            await asyncio.sleep(remaining_s)  # remaining decode tokens
            output_text = _fake_output(max_tokens)
            output_tokens = estimate_tokens(output_text)
            tokens_per_s = output_tokens / total_s if total_s > 0 else 0.0
            return CompletionResult(
                output_text=output_text,
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
                queue_wait_s=queue_wait_s,
                decode_s=total_s,
                ttft_s=ttft_s,
                tpot_s=tpot_s,
                tokens_per_s=tokens_per_s,
            )
        finally:
            self.running -= 1
            self._sem.release()


def _fake_output(max_tokens: int) -> str:
    return " ".join(["mock"] * max(1, max_tokens))
