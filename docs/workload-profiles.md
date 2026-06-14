# Workload Profiles & Capacity Ladder

This tool is not a generic "send many requests" load tester. It evaluates whether
the current LLM serving config (replicas, max concurrency, autoscaler, GPU memory)
fits the **workload** a service actually runs. Workloads are defined in
`analyzer/config/workload-profiles.yaml` and consumed by `analyzer/workload.py`
(fit judgment) and `scripts/run-workload.sh` (the staged load test).

## 1. The four workloads

Each workload maps to a real service category **and** isolates a distinct serving
bottleneck, so the load test and the recommendation are meaningful.

| Workload | Real service example | Request shape | Dominant bottleneck (test target) | Key metrics |
|---|---|---|---|---|
| `support_chat` | Customer-support RAG chatbot (Intercom Fin, Zendesk AI) | short question + medium RAG context → short/medium output, interactive | **TTFT + queue** under concurrency | `ttft_p95`, `queue_wait_p95`, `p95_latency`, `requests_waiting` |
| `doc_summary` | Doc / meeting / channel summary (Slack AI, Notion AI, M365 Copilot) | long input → medium output | **prefill + GPU memory / KV cache** as input grows | `ttft_p95`, `prompt_tokens_p95`, `gpu_memory_used_ratio`, `kv_cache_ratio` |
| `code_assistant` | Coding assistant (Copilot, Cursor) | fixed code context → increasingly long code output | **decode / TPOT / p99 tail latency** as output grows | `tpot_p95`, `ttft_p95`, `p99_latency`, `output_tokens_p95`, `output_token_rate` |
| `json_extraction` | Extraction / classification / routing (CRM & email automation) | short input → tiny JSON output, high RPS | **throughput / queue** (p99 stability) | `p99_latency`, `queue_wait_p95`, `requests_waiting` |

These four cover the three serving regimes (prefill / decode / queue-throughput)
plus the interactive case. GPU thresholds (`gpu_memory_used_ratio`,
`gpu_utilization`, `kv_cache_ratio`) are skipped on the mock pipeline and activate
automatically when a vLLM/DCGM run supplies them — same gating as the rule engine.

> Cost analysis is intentionally excluded for now. `--cost-profile` is opt-in and
> the session pipeline never passes it, so reports carry no cost section.

## 2. Load is staged into a capacity ladder (not one big burst)

A single fixed burst only answers "did it break this once?" Real capacity planning
needs "how many users until it breaks, and why?" So each workload's `test_plan`
defines a **monotonic load ladder** (`stress`), sized to the workload's weight:

- `support_chat` ramps concurrency aggressively (RAG_VUS 8 → 32 → 96)
- `doc_summary` ramps input length (4k → 16k → 32k tokens)
- `code_assistant` fixes code context/concurrency and ramps output length (128 → 512 → 1024 output tokens)
- `json_extraction` ramps arrival rate (25 → 100 → 200 RPS)

Each rung runs as its own analyzable phase. A common LLM baseline runs first so a
workload is never judged in isolation. `test_plan.load_unit` (`vus | input_tokens |
output_tokens | rps`) labels the rung load. Levels: `quick` (baselines only),
`standard` (fast capacity ladder only), `full` (baselines + ladder + operational
burst/ramp/mixed). `standard` is intentionally shorter because its purpose is to
find the knee/break quickly, not to repeat every baseline.

Normal threshold misses produce `partially_suitable`. Some metrics also define a
hard `critical_*` threshold; crossing that threshold produces `unsuitable`, which
lets the session report mark a real `break` even when other metrics still pass.

## 3. What the report tells you — the capacity knee

`analyzer/session.py` walks the ladder rungs in increasing-load order and reports:

```text
안전 용량(safe)   : 마지막으로 SLO를 통과한 부하
한계 시작(knee)   : 처음으로 SLO가 저하된 부하 (partially_suitable)
붕괴(break)       : 부적합(unsuitable)이 된 부하
한계 병목          : knee에서 지배적인 병목 카테고리 (queue / prefill / decode / gpu_memory ...)
```

i.e. "이만큼 몰리면 안전, 이만큼부터 위험, 이만큼에서 붕괴, 원인은 ○○."

## 4. Closed loop: fix → re-test → compare

1. Run the ladder: `bash scripts/run-workload.sh <workload> --level standard`
2. Read `reports/session-<workload>-<level>-<ts>/session-report.md` → safe/knee/break + limiting bottleneck.
3. Apply the workload's `recommendations` playbook for that bottleneck (each phase
   `report.md` "권장 설정" also lists computed config changes).
4. Re-run the **same ladder** → the knee should move to a higher load, or the
   limiting bottleneck should change. `analyzer/compare` diffs two runs.

Example reads:

```text
support_chat: queue_wait·TTFT가 동시성 40부터 급등하는데 GPU util은 낮음
  → KEDA queue autoscaling / minReplicas 상향 → 재실행 시 knee가 64+로 이동 기대

doc_summary: 입력 16k에서 gpu_memory_used_ratio 0.9 초과 / OOM
  → max_model_len 조정 또는 더 큰 VRAM / concurrency 하향 → 같은 입력서 OOM 소멸 기대

code_assistant: output 512~1024 tokens에서 TPOT·p99 급등
  → max_tokens 제한 / streaming / 긴 코드 생성 요청 분리 → 같은 context에서 p99·TPOT 완화 기대

json_extraction: 100~200 RPS부터 p99·queue 급등
  → replica 상향 / KEDA queue → 더 높은 RPS까지 p99 안정 기대
```

## 5. Pipeline B: workload -> deployable vLLM environment

Pipeline B starts from a service workload and generates Kubernetes manifests for
a fresh vLLM deployment before running the same workload ladder.

```bash
bash scripts/run-pipeline-b.sh support_chat \
  --level standard \
  --gpu-vendor nvidia \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --served-model-name mock
```

`analyzer.provision` creates two profiles by default:

| Profile | Purpose |
|---|---|
| `run` | Single-GPU executable profile. It uses one replica and disables autoscaling so it works on a one-GPU VM. |
| `recommended` | Workload `initial_config` profile. It keeps the recommended replica/autoscaler settings for review or larger clusters. |

Generated manifests are written under `k8s/generated/<workload>/run` and
`k8s/generated/<workload>/recommended`. `run-pipeline-b.sh` applies only the
`run` profile automatically, then launches `run-workload.sh`.

## 6. Session-level before/after comparison

After applying a recommendation and re-running the same ladder, compare the two
session directories:

```bash
analyzer/.venv/bin/python -m analyzer.session_compare \
  --before reports/session-support_chat-standard-<before-ts> \
  --after reports/session-support_chat-standard-<after-ts> \
  --output reports/session-compare-support_chat
```

This writes `session-comparison.md/json` and focuses on `safe`, `knee`,
`break`, limiting bottleneck, and phase-level p95/TTFT deltas.

## 7. Quality metrics (deferred to real models)

Content-quality checks (JSON valid rate, groundedness, syntax/compile, classification
accuracy) require a real model's output and are meaningless against the mock
(`"mock mock ..."`). They belong to the vLLM stage. The infra/serving metrics above
are sufficient to locate the capacity knee and its cause now.
