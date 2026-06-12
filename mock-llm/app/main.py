"""FastAPI entrypoint for the mock LLM service.

Endpoints:
    POST /v1/chat/completions   OpenAI-compatible (subset)
    GET  /metrics               Prometheus exposition
    GET  /healthz               liveness probe
    GET  /readyz                readiness probe (always ready in MVP)
"""
from __future__ import annotations

import time
import uuid
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app.config import settings
from app.metrics import (
    BATCH_SIZE,
    ERRORS_TOTAL,
    INTER_TOKEN_LATENCY_SECONDS,
    KV_CACHE_USAGE_RATIO,
    OUTPUT_TOKENS_TOTAL,
    OUTPUT_TOKENS_PER_REQUEST,
    PROMPT_TOKENS_TOTAL,
    PROMPT_TOKENS_PER_REQUEST,
    QUEUE_WAIT_SECONDS,
    REQUEST_DURATION_SECONDS,
    REQUESTS_RUNNING,
    REQUESTS_TOTAL,
    REQUESTS_WAITING,
    TIME_TO_FIRST_TOKEN_SECONDS,
)
from app.simulator import LLMSimulator, QueueTimeout

app = FastAPI(title="mock-llm", version="0.1.0")
simulator = LLMSimulator(
    max_concurrency=settings.max_concurrency,
    base_latency_ms=settings.base_latency_ms,
    per_prompt_token_ms=settings.per_prompt_token_ms,
    per_output_token_ms=settings.per_output_token_ms,
    queue_timeout_s=settings.queue_timeout_s,
)


# ---------- request / response (OpenAI subset) ----------
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="mock")
    messages: List[ChatMessage] = Field(min_length=1)
    max_tokens: int = Field(default=64, gt=0)


class ChatChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatChoice]
    usage: Usage


# ---------- helpers ----------
def _sync_gauges() -> None:
    REQUESTS_RUNNING.set(simulator.running)
    REQUESTS_WAITING.set(simulator.waiting)
    BATCH_SIZE.set(simulator.running)
    KV_CACHE_USAGE_RATIO.set(simulator.running / simulator.max_concurrency)


# ---------- endpoints ----------
@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(req: ChatCompletionRequest) -> ChatCompletionResponse:
    REQUESTS_TOTAL.inc()
    max_tokens = min(req.max_tokens, settings.max_tokens_cap)
    prompt = "\n".join(m.content for m in req.messages)

    start = time.monotonic()
    _sync_gauges()
    try:
        result = await simulator.generate(prompt=prompt, max_tokens=max_tokens)
    except QueueTimeout as exc:
        ERRORS_TOTAL.labels(reason="queue_timeout").inc()
        raise HTTPException(status_code=503, detail=f"queue timeout: {exc}")
    except Exception:
        ERRORS_TOTAL.labels(reason="internal").inc()
        raise
    finally:
        REQUEST_DURATION_SECONDS.observe(time.monotonic() - start)
        _sync_gauges()

    PROMPT_TOKENS_TOTAL.inc(result.prompt_tokens)
    OUTPUT_TOKENS_TOTAL.inc(result.output_tokens)
    PROMPT_TOKENS_PER_REQUEST.observe(result.prompt_tokens)
    OUTPUT_TOKENS_PER_REQUEST.observe(result.output_tokens)
    QUEUE_WAIT_SECONDS.observe(result.queue_wait_s)
    TIME_TO_FIRST_TOKEN_SECONDS.observe(result.ttft_s)
    INTER_TOKEN_LATENCY_SECONDS.observe(result.tpot_s)

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
        created=int(time.time()),
        model=req.model,
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=result.output_text),
            )
        ],
        usage=Usage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.output_tokens,
            total_tokens=result.prompt_tokens + result.output_tokens,
        ),
    )


@app.get("/metrics")
def metrics() -> Response:
    _sync_gauges()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/readyz")
def readyz() -> JSONResponse:
    return JSONResponse({"status": "ready"})
