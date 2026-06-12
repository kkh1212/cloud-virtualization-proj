# Metrics

이 문서는 mock-llm, Kubernetes, 향후 GPU 확장 메트릭의 의미와 분석기 룰 의존성을 정리한다. 분석기의 PromQL 정의는 `analyzer/config/metrics.yaml`이 유일한 기준이다.

## mock-llm 직접 노출

| 논리명 | 메트릭 이름 | 타입 | 라벨 | 의미 | 분석기 사용 룰 |
|---|---|---|---|---|---|
| requests_total | `mock_llm_requests_total` | Counter | - | 총 요청 수 | (참고용) |
| requests_running | `mock_llm_requests_running` | Gauge | - | 현재 처리 중 | queue_bottleneck, hpa_limitation |
| requests_waiting | `mock_llm_requests_waiting` | Gauge | - | 슬롯 대기 | queue_bottleneck, hpa_limitation |
| request_duration_seconds | `mock_llm_request_duration_seconds` | Histogram | - (sum/count/buckets) | 평균/p95/p99 종단 latency | 모든 룰 (p95) |
| queue_wait_seconds | `mock_llm_queue_wait_seconds` | Histogram | - | concurrency slot을 얻기 전 대기 시간 | (참고용, queue 분석) |
| prompt_tokens_total | `mock_llm_prompt_tokens_total` | Counter | - | prompt token 누적 | (참고용) |
| prompt_tokens_per_request | `mock_llm_prompt_tokens_per_request` | Histogram | - | 요청별 prompt token 분포 | (참고용, workload 검증) |
| output_tokens_total | `mock_llm_output_tokens_total` | Counter | - | output token 누적 | (참고용) |
| output_tokens_per_request | `mock_llm_output_tokens_per_request` | Histogram | - | 요청별 output token 분포 | (참고용, workload 검증) |
| errors_total | `mock_llm_errors_total` | Counter | reason | 실패 누적 | (참고용, queue_timeout 가 핵심) |

## k6 사용자 관점 지표

`run.json`의 `k6_summary_path`가 가리키는 `k6_summary.json`에서 analyzer가 외부 사용자 관점의 지표를 함께 렌더링한다.

| 항목 | 의미 |
|---|---|
| k6 latency p50/p95/p99 | HTTP 요청 전체 지연 시간 |
| k6 failed rate | HTTP 실패율 |
| k6 checks success rate | 응답 구조 검증 성공률 |
| k6 request count | k6가 보낸 요청 수 |
| k6 VU peak | 부하 생성 중 최대 VU |
| tag별 p95 latency | `prompt_type`, `output_type`, `scenario_type` 기준 요청 유형별 latency |

tag별 p95는 k6 summary에 submetric이 있을 때만 표시된다. `mixed_workload`는 tag별 high-ceiling threshold를 넣어 short/RAG/long-output 지표가 summary에 남도록 한다.

## Kubernetes 측

kube-state-metrics, node-exporter, cAdvisor 계열 메트릭을 PromQL로 가공해 분석기에 전달한다.

| 논리명 | 메트릭 | 분석기 사용 |
|---|---|---|
| cpu_usage_ratio | `sum(rate(container_cpu_usage_seconds_total{namespace="llm-ops",pod=~"mock-llm-.*",container="mock-llm"}[1m])) / sum(kube_pod_container_resource_requests{namespace="llm-ops",pod=~"mock-llm-.*",container="mock-llm",resource="cpu"})` | cpu_bottleneck, hpa_limitation |
| memory_bytes | `container_memory_working_set_bytes{container="mock-llm"}` | (참고용) |
| replicas_desired/ready | `kube_deployment_spec_replicas` / `kube_deployment_status_replicas_ready` | scale_out_lag, hpa_limitation |
| pod_pending_count | `kube_pod_status_phase{phase="Pending"}` | gpu_scheduling |

## GPU 확장

현재 환경은 GPU가 없으므로 미수집 상태다. DCGM exporter와 vLLM 계열 메트릭을 추가하면 `Rule.required_metrics` 게이트를 통과해 GPU 룰이 활성화된다.

| 논리명 | 메트릭 | 활성화 룰 |
|---|---|---|
| gpu_utilization | `DCGM_FI_DEV_GPU_UTIL` | gpu_compute |
| gpu_memory_used_ratio | `DCGM_FI_DEV_FB_USED / total` | gpu_memory |
| kv_cache_usage_ratio | `vllm:gpu_cache_usage_perc` | gpu_memory |

## 새 메트릭 추가 절차

1. `analyzer/config/metrics.yaml`에 논리명과 PromQL 행을 추가한다.
2. 기존 룰에서 쓰는 메트릭이면 해당 Rule의 `required_metrics`에 논리명을 등록한다.
3. 새 판단이 필요하면 `analyzer/rules/`에 Rule 파일을 추가한다.
4. 새 Rule 클래스를 `analyzer/rules/__init__.py`의 `ALL_RULES`에 추가한다.
5. analyzer 테스트를 추가하고 `analyzer/.venv/bin/pytest analyzer/tests -v`로 검증한다.

## GPU/vLLM metric profiles

GPU runs use vendor-specific metric profiles instead of the mock metric profile.
`scripts/run-experiment.sh --target vllm --gpu-vendor nvidia|amd` writes the
chosen profile into `run.json`, so `python -m analyzer.main --run <dir>` can
pick it up automatically.

Profiles:

| Profile | Use case |
|---|---|
| `metrics-vllm.yaml` | backward-compatible NVIDIA/DCGM alias |
| `metrics-vllm-nvidia.yaml` | vLLM + NVIDIA DCGM exporter |
| `metrics-vllm-amd.yaml` | vLLM + AMD Device Metrics Exporter |

Primary vLLM metrics:

| Logical name | Prometheus source |
|---|---|
| requests_total | `vllm:request_success_total` |
| requests_running | `vllm:num_requests_running` |
| requests_waiting | `vllm:num_requests_waiting` |
| p95/p99 latency | `vllm:e2e_request_latency_seconds_bucket` |
| TTFT p95 | `vllm:time_to_first_token_seconds_bucket` |
| TPOT p95 | `vllm:time_per_output_token_seconds_bucket` |
| queue wait p95 | `vllm:request_queue_time_seconds_bucket` |
| prompt tokens/request p95 | `vllm:request_prompt_tokens_bucket` |
| output tokens/request p95 | `vllm:request_generation_tokens_bucket` |
| KV cache ratio | `vllm:gpu_cache_usage_perc` |

Primary NVIDIA/DCGM metrics:

| Logical name | Prometheus source |
|---|---|
| gpu_utilization | `DCGM_FI_DEV_GPU_UTIL / 100` |
| gpu_memory_used_ratio | `DCGM_FI_DEV_FB_USED / (used + free)` |

Primary AMD Device Metrics Exporter metrics:

| Logical name | Prometheus source |
|---|---|
| gpu_utilization | `GPU_GFX_ACTIVITY / 100` |
| gpu_memory_used_ratio | `GPU_USED_VRAM / GPU_TOTAL_VRAM` |

The first GPU profiles intentionally assume a single GPU validation server. In a
multi-node GPU cluster, GPU metrics should later be narrowed by node/GPU labels
so the report only analyzes the vLLM-serving GPU.
