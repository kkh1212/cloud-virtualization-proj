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

TIME_TO_FIRST_TOKEN_SECONDS = Histogram(
    "mock_llm_time_to_first_token_seconds",
    "Time to first output token (prefill latency, TTFT) in seconds.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

INTER_TOKEN_LATENCY_SECONDS = Histogram(
    "mock_llm_inter_token_latency_seconds",
    "Average inter-token latency (time per output token, TPOT) in seconds.",
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0),
)

QUEUE_WAIT_SECONDS = Histogram(
    "mock_llm_queue_wait_seconds",
    "Time spent waiting for a concurrency slot before decoding starts.",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

BATCH_SIZE = Gauge(
    "mock_llm_batch_size",
    "Current decoding batch size (requests decoding concurrently).",
)

KV_CACHE_USAGE_RATIO = Gauge(
    "mock_llm_kv_cache_usage_ratio",
    "Simulated KV-cache utilization (running / max_concurrency). Proxy only; "
    "replace with vLLM's real gpu_cache_usage_perc after GPU migration.",
)

PROMPT_TOKENS_TOTAL = Counter(
    "mock_llm_prompt_tokens_total",
    "Total prompt tokens accepted (whitespace-split estimation).",
)

PROMPT_TOKENS_PER_REQUEST = Histogram(
    "mock_llm_prompt_tokens_per_request",
    "Prompt tokens per request (whitespace-split estimation).",
    buckets=(50, 100, 200, 500, 1000, 2000, 4000, 8000, 16000),
)

OUTPUT_TOKENS_TOTAL = Counter(
    "mock_llm_output_tokens_total",
    "Total output tokens emitted by the simulator.",
)

OUTPUT_TOKENS_PER_REQUEST = Histogram(
    "mock_llm_output_tokens_per_request",
    "Output tokens per request.",
    buckets=(50, 100, 200, 500, 1000, 1500, 2000, 4096),
)

ERRORS_TOTAL = Counter(
    "mock_llm_errors_total",
    "Total number of failed /v1/chat/completions requests.",
    labelnames=("reason",),
)
