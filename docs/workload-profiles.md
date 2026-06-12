# Workload Profiles for Later Suitability Evaluation

This document preserves the service examples that will later become workload
suitability rules. It is not an analyzer feature yet.

The current implementation goal is still:

```text
load test scenarios -> Prometheus metrics -> analyzer report
```

Suitability scoring comes after the CPU/KEDA pipeline and GPU/vLLM validation
are stable.

| Profile | Service example | Request shape | Main scenarios | Metrics to watch |
|---|---|---|---|---|
| `faq_chatbot` | General chatbot / FAQ | short input, short output | `short_prompt`, `mixed_workload` | TTFT, p95 latency, waiting requests |
| `customer_support` | Customer support assistant | short question + policy/order context | `rag_like`, `mixed_workload` | TTFT, queue wait, prompt tokens/request |
| `rag_internal_qa` | Internal document QA | short question + long retrieved context | `rag_like`, `long_input` | TTFT, prompt tokens/request, p99 latency |
| `document_summary` | Meeting note or document summary | long input, short/medium output | `long_input` | TTFT, prefill pressure, memory/GPU memory |
| `long_generation` | Report or marketing text generation | short/medium input, long output | `long_output` | TPOT, output tokens/request, output token rate |
| `coding_assistant` | Code/log analysis assistant | medium/long input, medium/long output | `long_input`, `long_output`, `mixed_workload` | TTFT, TPOT, p99 latency |
| `recommendation_explanation` | Recommendation explanation | structured short/medium context, medium output | `short_prompt`, `mixed_workload` | p95 latency, output token rate |
| `tool_agent` | Tool-calling agent | multiple LLM calls per user request | `mixed_workload`, `burst_traffic`, `sustained_ramp` | p99 latency, queue wait, scale-out lag |

Future report target:

```text
current config: suitable / partially suitable / unsuitable
evidence: metrics that crossed profile-specific thresholds
bottleneck: queue, TTFT/prefill, TPOT/decode, GPU memory, GPU compute, scale-out lag
recommendation: config or infrastructure direction
tradeoff: performance gain vs cost
```
