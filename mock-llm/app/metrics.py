"""Prometheus collectors exposed by the mock LLM service.

These metric names are the contract that the analyzer's metrics.yaml binds to
under logical names. Renaming here requires updating analyzer/config/metrics.yaml.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "mock_llm_requests_total",
    "Total number of /v1/chat/completions requests received.",
)

REQUESTS_RUNNING = Gauge(
    "mock_llm_requests_running",
    "Number of requests currently decoding (holding a concurrency slot).",
)

REQUESTS_WAITING = Gauge(
    "mock_llm_requests_waiting",
    "Number of requests waiting to acquire a concurrency slot.",
)

REQUEST_DURATION_SECONDS = Histogram(
    "mock_llm_request_duration_seconds",
    "End-to-end request latency in seconds (queue wait + decoding).",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

PROMPT_TOKENS_TOTAL = Counter(
    "mock_llm_prompt_tokens_total",
    "Total prompt tokens accepted (whitespace-split estimation).",
)

OUTPUT_TOKENS_TOTAL = Counter(
    "mock_llm_output_tokens_total",
    "Total output tokens emitted by the simulator.",
)

ERRORS_TOTAL = Counter(
    "mock_llm_errors_total",
    "Total number of failed /v1/chat/completions requests.",
    labelnames=("reason",),
)
