# Metrics

이 문서는 mock-llm, Kubernetes, 향후 GPU 확장 메트릭의 의미와 분석기 룰 의존성을 정리한다. 분석기의 PromQL 정의는 `analyzer/config/metrics.yaml`이 유일한 기준이다.

## mock-llm 직접 노출

| 논리명 | 메트릭 이름 | 타입 | 라벨 | 의미 | 분석기 사용 룰 |
|---|---|---|---|---|---|
| requests_total | `mock_llm_requests_total` | Counter | - | 총 요청 수 | (참고용) |
| requests_running | `mock_llm_requests_running` | Gauge | - | 현재 처리 중 | queue_bottleneck, hpa_limitation |
| requests_waiting | `mock_llm_requests_waiting` | Gauge | - | 슬롯 대기 | queue_bottleneck, hpa_limitation |
| request_duration_seconds | `mock_llm_request_duration_seconds` | Histogram | - (sum/count/buckets) | 평균/p95/p99 종단 latency | 모든 룰 (p95) |
| prompt_tokens_total | `mock_llm_prompt_tokens_total` | Counter | - | prompt token 누적 | (참고용) |
| output_tokens_total | `mock_llm_output_tokens_total` | Counter | - | output token 누적 | (참고용) |
| errors_total | `mock_llm_errors_total` | Counter | reason | 실패 누적 | (참고용, queue_timeout 가 핵심) |

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
