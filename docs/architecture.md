# Architecture

Kubernetes 기반 LLM 운영 진단 플랫폼은 부하를 만들고, 서비스 상태를 Prometheus로 수집하고, Python 분석기가 룰 기반으로 병목을 설명하는 MVP 파이프라인이다.

```text
┌─ k6 (loadtests/*.js) ─┐         ┌─ kube-prometheus-stack (monitoring) ─┐
│  scenario × 3        │         │  Prometheus  ←  ServiceMonitor       │
└──────────┬───────────┘         │  Grafana     ←  ConfigMap dashboard  │
           │ HTTP                 │  kube-state-metrics + node-exporter  │
           ▼                      └──────────────────┬───────────────────┘
 NodePort 30080                                      │ PromQL
 kube-proxy round-robin                              │ /api/v1/query_range
           │                                         ▼
           ▼                              ┌─ analyzer (Python) ─┐
┌─ mock-llm Deployment (llm-ops, replicas=2) ┐
│  FastAPI + asyncio.Semaphore  /metrics ────┼──→  collector → snapshot
│  /v1/chat/completions  /healthz /readyz    │     ↓
└─────────────┬──────────────────────────────┘     rules/* (필요한 메트릭 있을 때만 평가)
              ▲                                    ↓
              │ HPA (autoscaling/v2)              report.md + report.json
              │  CPU 60% util target               (reports/<scenario>-<ts>/)
              └──── kube-controller-manager
```

## 컴포넌트 책임

| 컴포넌트 | 책임 |
|---|---|
| `loadtests/*.js` | k6 시나리오별 트래픽을 NodePort 30080으로 발생시킨다. |
| `mock-llm` | OpenAI 호환 응답을 흉내 내고 concurrency/queue/latency/error 메트릭을 노출한다. |
| Kubernetes Service | NodePort와 kube-proxy round-robin으로 2개 Pod에 부하를 분산한다. |
| HPA | CPU utilization 60% 기준으로 mock-llm replicas를 2~8 범위에서 조정한다. |
| ServiceMonitor | mock-llm Service의 named port `http`에서 `/metrics`를 스크레이프하도록 Prometheus에 알려준다. |
| Prometheus | mock-llm, kube-state-metrics, cAdvisor 계열 시계열을 저장하고 PromQL API를 제공한다. |
| Grafana | ConfigMap dashboard를 자동 임포트해 실험 중 핵심 지표를 시각화한다. |
| analyzer | Prometheus HTTP API로 시계열을 수집하고 룰 엔진을 실행해 Markdown/JSON 리포트를 만든다. |

## 데이터 흐름

1. 부하 발생: 사용자가 `scripts/run-experiment.sh`를 실행하면 k6가 `short_prompt`, `long_prompt`, `burst_traffic` 중 하나를 NodePort 30080으로 보낸다.
2. mock-llm 처리: Service가 트래픽을 Pod에 분산하고, 각 Pod는 `asyncio.Semaphore`로 동시 처리 슬롯을 제한하며 `/metrics`에 상태를 노출한다.
3. Prometheus 스크레이프: kube-prometheus-stack의 Prometheus가 ServiceMonitor를 통해 `/metrics`를 5초 간격으로 수집하고, Kubernetes 상태 메트릭도 함께 저장한다.
4. 분석기 수집: analyzer collector가 `analyzer/config/metrics.yaml`의 PromQL만 사용해 `/api/v1/query_range`에서 시계열을 가져와 `MetricSnapshot`을 만든다.
5. 리포트 출력: 각 Rule은 `required_metrics`가 모두 있을 때만 평가되고, 같은 `Report` 모델에서 `report.md`와 `report.json`이 동시에 생성된다.

## 확장 경로

| 현재 mock 환경 | 실제 운영 확장 |
|---|---|
| `mock-llm` | vLLM 또는 실제 LLM serving으로 교체하되, 분석기가 의존하는 메트릭 이름 contract를 유지한다. |
| GPU 없음 | DCGM exporter를 추가하고 `analyzer/config/metrics.yaml`의 GPU 행 주석을 해제하면 `gpu_compute`, `gpu_memory`, `gpu_scheduling` 룰이 자동 활성화된다. |
| 기본 룰 7개 | 새 룰은 `analyzer/rules/`에 파일 1개를 추가하고 `analyzer/rules/__init__.py`의 `ALL_RULES`에 등록한다. |
| CPU 기반 HPA | queue 기반 custom metric autoscaling을 추가하면 Rule #4의 한계를 비교 실험할 수 있다. |


## KEDA 확장

CPU HPA baseline과 KEDA queue autoscaling은 같은 Deployment를 동시에 제어하지 않는다. 실험 전 `scripts/use-cpu-hpa.sh` 또는 `scripts/use-keda-queue.sh` 중 하나로 모드를 명시적으로 전환한다. KEDA 모드는 Prometheus query `sum(mock_llm_requests_waiting)`을 기준으로 `mock-llm` Deployment를 2~8 replicas 사이에서 조정한다.

비교는 `analyzer.compare`가 생성하는 `comparison.md` / `comparison.json`을 기준으로 한다.
