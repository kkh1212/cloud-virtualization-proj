"""Unit tests for the mock LLM simulator.

These run without uvicorn / k8s and verify the four behaviours that the
analyzer's queue-bottleneck rule will later depend on:
  1. delay scales with prompt length and max_tokens
  2. asyncio.Semaphore caps concurrency
  3. waiting/running counters move correctly under contention
  4. queue timeout fires when slots stay full too long
"""
from __future__ import annotations

import asyncio

import pytest

from app.simulator import LLMSimulator, QueueTimeout, estimate_tokens


def make_sim(
    max_concurrency: int = 2,
    queue_timeout_s: float = 5.0,
    base_latency_ms: int = 100,
    per_prompt_token_ms: float = 10.0,
    per_output_token_ms: float = 20.0,
) -> LLMSimulator:
    return LLMSimulator(
        max_concurrency=max_concurrency,
        base_latency_ms=base_latency_ms,
        per_prompt_token_ms=per_prompt_token_ms,
        per_output_token_ms=per_output_token_ms,
        queue_timeout_s=queue_timeout_s,
    )


def test_estimate_tokens_basic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("  a  b  c  ") == 3


def test_delay_scales_with_inputs():
    sim = make_sim()
    short = sim.estimate_delay_s(prompt_tokens=1, max_tokens=1)
    long_prompt = sim.estimate_delay_s(prompt_tokens=100, max_tokens=1)
    long_output = sim.estimate_delay_s(prompt_tokens=1, max_tokens=100)
    assert long_prompt > short
    # output coefficient is heavier than prompt coefficient by design
    assert long_output > long_prompt


async def test_concurrency_cap_respected():
    sim = make_sim(
        max_concurrency=2,
        base_latency_ms=0,
        per_prompt_token_ms=0,
        per_output_token_ms=50.0,  # 50 ms per output token
    )

    async def call():
        # 4 tokens × 50 ms = 200 ms decode; gives the test time to observe state.
        return await sim.generate(prompt="hi", max_tokens=4)

    tasks = [asyncio.create_task(call()) for _ in range(5)]
    await asyncio.sleep(0.05)
    assert sim.running <= 2
    assert sim.waiting >= 1

    await asyncio.gather(*tasks)
    assert sim.running == 0
    assert sim.waiting == 0


async def test_queue_timeout_raises():
    sim = make_sim(
        max_concurrency=1,
        queue_timeout_s=0.1,
        base_latency_ms=0,
        per_prompt_token_ms=0,
        per_output_token_ms=100.0,
    )

    # 10 tokens × 100 ms = 1.0 s of decoding, holding the only slot.
    slow = asyncio.create_task(sim.generate(prompt="x", max_tokens=10))
    await asyncio.sleep(0.02)  # let `slow` acquire the slot

    with pytest.raises(QueueTimeout):
        await sim.generate(prompt="y", max_tokens=1)

    await slow
    assert sim.running == 0
    assert sim.waiting == 0
