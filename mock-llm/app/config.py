"""Mock LLM runtime configuration.

All values are read from environment variables with the prefix ``MOCK_LLM_``
(e.g. ``MOCK_LLM_MAX_CONCURRENCY=8``). Defaults match the Phase 1 design:
asyncio.Semaphore-backed queue with length-proportional fake delays.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOCK_LLM_", env_file=None)

    # Latency model:
    #   total_decode_seconds = base_ms + prompt_tokens * per_prompt_ms + max_tokens * per_output_ms
    base_latency_ms: int = Field(default=200, ge=0)
    per_prompt_token_ms: float = Field(default=2.0, ge=0)
    per_output_token_ms: float = Field(default=8.0, ge=0)

    # Concurrency: at most max_concurrency requests are decoding at once.
    # Excess requests block on the simulator's semaphore, raising waiting count.
    max_concurrency: int = Field(default=4, ge=1)

    # Reject a request that has waited longer than this many seconds for a slot.
    queue_timeout_s: float = Field(default=30.0, gt=0)

    # Cap output tokens so a misbehaving client can't make a single request take forever.
    max_tokens_cap: int = Field(default=2048, gt=0)


settings = Settings()
