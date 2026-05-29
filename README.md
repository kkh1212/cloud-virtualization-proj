# 0522 작업 내용 - KEDA 실측 검증 및 closed-loop 튜닝

2026-05-22 우분투 k3s 클러스터에서 Phase 1~5 실측 검증을 진행했다. 핵심 결론은 **CPU HPA는 LLM queue 병목을 잡지 못했고, KEDA queue autoscaling과 추천값 기반 튜닝은 latency/error/waiting queue를 크게 줄였다**는 것이다.

## 0522-1. Phase 1~4 검증 요약

- Phase 1 LLM serving metric 검증 완료: `TTFT p95`, `inter-token latency p95`, `batch size`, `KV-cache usage proxy`가 mock-llm `/metrics` -> Prometheus -> analyzer report까지 정상 연결됐다.
- `short_prompt` 결과: `TTFT p95 (peak)=0.243s`, `inter-token latency p95 (peak)=0.010s`, `max batch size=3`, `KV-cache 사용률 avg=0.409`.
- Phase 2 신규 k6 시나리오 검증 완료: `mixed_workload`는 574 requests, `sustained_ramp`는 15,842 requests로 정상 실행됐다.
- `mixed_workload`는 평균 latency 1.978s, p95 6.375s, p99 9.275s로 long-tail 패턴을 보였다.
- `sustained_ramp`는 CPU HPA 환경에서 max waiting 1198, p95/p99 30s, `queue_bottleneck` + `hpa_limitation`을 재현했다.
- Phase 3 SLO 검증 완료: `strict`, `long_context`, `default` profile에서 `## 7. SLO 판정`과 `slo_breach` 진단이 정상 렌더링됐다.
- Phase 4 추천 검증 완료: `## 9. 권장 설정`에서 KEDA 전환, threshold 하향, maxReplicaCount 상향, max concurrency 상향이 실측 기반으로 제안됐다.

## 0522-2. Phase 5 CPU HPA vs KEDA 실측 비교

CPU HPA baseline과 KEDA queue autoscaling을 같은 `sustained_ramp` 부하로 비교했다.

| 항목 | CPU HPA baseline | KEDA queue autoscaling | 해석 |
|---|---:|---:|---|
| avg latency | 21.611s | 5.460s | KEDA 적용 후 74.7% 감소 |
| p95 latency peak | 30.000s | 28.134s | 일부 개선, SLO는 아직 위반 |
| p99 latency peak | 30.000s | 29.627s | 일부 개선, SLO는 아직 위반 |
| error rate peak | 29.055 err/s | 0.000 err/s | 에러 제거 |
| max waiting | 1198 | 396 | queue 66.9% 감소 |
| desired replicas max | 2 | 8 | KEDA가 queue를 보고 scale-out |
| ready replicas max | 2 | 7 | Pod 준비 지연이 일부 존재 |
| triggered 변화 | `hpa_limitation` 존재 | `hpa_limitation` 제거, `scale_out_lag` 추가 | 병목이 autoscaler 미작동에서 scale-out lag로 이동 |

결론: KEDA는 CPU HPA보다 명확히 개선됐지만, 기본값만으로는 tail latency SLO를 완전히 만족하지 못했다.

## 0522-3. 추천값 적용 후 closed-loop 튜닝 결과

KEDA run의 추천 중 성능에 직접 관련된 값만 적용했다.

```text
MOCK_LLM_MAX_CONCURRENCY: 4 -> 8
KEDA threshold: 20 -> 10
KEDA maxReplicaCount: 8 -> 12
recommend.yaml current 동기화
```

KEDA 기본값 대비 tuned run 비교:

| 항목 | KEDA 기본값 | Tuned KEDA | 해석 |
|---|---:|---:|---|
| avg latency | 5.460s | 2.099s | 61.6% 감소 |
| p95 latency peak | 28.134s | 7.738s | 72.5% 감소 |
| p99 latency peak | 29.627s | 9.548s | 67.8% 감소 |
| error rate peak | 0.000 err/s | 0.000 err/s | 에러 0 유지 |
| throughput avg | 23.513 req/s | 23.958 req/s | 소폭 증가 |
| max waiting | 396 | 91 | 77.0% 감소 |
| desired replicas max | 8 | 9 | 더 이른 scale-out 확인 |

결론: analyzer 추천값 일부를 적용한 뒤 실제 실험에서 latency와 waiting queue가 추가로 크게 감소했다. 즉 **recommendation -> manifest 반영 -> 재실험 -> comparison 검증** closed-loop가 성립했다.

## 0522-4. 남은 병목과 다음 작업

- Tuned run에서도 `p95_latency_seconds=7.738s`, `p99_latency_seconds=9.548s`로 default SLO는 아직 위반이다.
- `scale_out_lag`가 계속 남아 있으므로 Pod startup/readiness 지연, `minReplicaCount` 상향, KEDA `pollingInterval` 조정, 추가 maxReplicaCount 튜닝을 검토해야 한다.
- mock 환경에서는 CPU 사용률이 비현실적으로 낮아 CPU request 추천이 낮게 나올 수 있다. 실제 vLLM/GPU 환경에서 다시 검증해야 한다.
- 안정된 tuned run을 `analyzer/tests/fixtures/sustained_keda_tuned/`로 캡처했고, fixture replay 포함 analyzer 테스트는 `35 passed`를 확인했다.

---

# cloud-virtualization-proj

Kubernetes 기반 **LLM 서비스 운영 진단 플랫폼** MVP입니다.

이 프로젝트는 LLM 추론 서비스에서 자주 생기는 운영 문제를 실험으로 재현하고, Prometheus 메트릭을 분석해서 "왜 느려졌는지"를 리포트로 설명하는 것을 목표로 합니다.

현재 구현된 MVP 흐름은 다음과 같습니다.

```text
k6 load test
  -> mock LLM service
  -> Kubernetes Service / Deployment / HPA
  -> Prometheus metrics
  -> analyzer rule engine
  -> report.md + report.json
```

GitHub 저장소:

```text
https://github.com/kkh1212/cloud-virtualization-proj
```

## 1. 프로젝트가 하는 일

LLM 서비스는 CPU 사용률만 보고는 병목을 정확히 알기 어렵습니다. 요청은 밀려 있고 latency는 높지만, CPU는 낮아서 HPA가 scale-out하지 않는 상황이 생길 수 있습니다.

이 프로젝트는 그 상황을 작은 Kubernetes 실험 환경에서 재현합니다.

핵심 질문은 다음과 같습니다.

```text
부하가 들어왔을 때 서비스가 느려진 이유가 무엇인가?
CPU가 부족한가?
queue가 쌓였는가?
HPA가 scale-out하지 못했는가?
Pod 준비가 늦었는가?
Prometheus 메트릭으로 그 근거를 설명할 수 있는가?
```

현재 MVP는 특히 다음 상황을 잘 보여줍니다.

```text
burst traffic 상황에서 requests_waiting이 크게 증가한다.
p95/p99 latency가 상승한다.
CPU 사용률은 낮게 유지된다.
CPU 기반 HPA는 replica를 늘리지 않는다.
analyzer가 queue_bottleneck과 hpa_limitation을 진단한다.
```

## 2. 전체 설계

```text
┌────────────────────┐
│ k6 loadtests       │
│ short_prompt       │
│ long_prompt        │
│ burst_traffic      │
└─────────┬──────────┘
          │ HTTP
          ▼
┌────────────────────────────────────────────┐
│ Kubernetes                                  │
│ namespace: llm-ops                         │
│                                            │
│ Service NodePort 30080                     │
│        │                                   │
│        ▼                                   │
│ mock-llm Deployment                        │
│ replicas=2                                 │
│ FastAPI                                    │
│ /v1/chat/completions                       │
│ /healthz /readyz /metrics                  │
│ asyncio.Semaphore 기반 concurrency 제한     │
│ queue / latency / error metrics 노출        │
│                                            │
│ HPA                                        │
│ CPU utilization 기준 autoscaling            │
└─────────┬──────────────────────────────────┘
          │ scrape
          ▼
┌────────────────────────────────────────────┐
│ kube-prometheus-stack                      │
│ namespace: monitoring                      │
│                                            │
│ Prometheus                                 │
│ ServiceMonitor                             │
│ kube-state-metrics                         │
│ cAdvisor / container metrics               │
│ Grafana dashboard                          │
└─────────┬──────────────────────────────────┘
          │ PromQL query_range
          ▼
┌────────────────────────────────────────────┐
│ analyzer                                   │
│ Prometheus collector                       │
│ MetricSnapshot                             │
│ rule engine                                │
│ Markdown/JSON renderer                     │
└─────────┬──────────────────────────────────┘
          ▼
┌────────────────────────────────────────────┐
│ reports/<scenario>-<timestamp>/            │
│ run.json                                   │
│ k6.log                                     │
│ k6_summary.json                            │
│ report.md                                  │
│ report.json                                │
└────────────────────────────────────────────┘
```

## 3. 디렉토리 구조

| 경로 | 설명 |
|---|---|
| `mock-llm/` | FastAPI 기반 mock LLM 서비스입니다. OpenAI Chat Completions 비슷한 응답을 만들고 `/metrics`를 노출합니다. |
| `mock-llm/app/simulator.py` | latency, token 수, concurrency 제한, queue timeout을 시뮬레이션합니다. |
| `mock-llm/app/metrics.py` | Prometheus metric을 정의합니다. |
| `k8s/` | Namespace, Deployment, Service, HPA, ServiceMonitor, Prometheus values, Grafana dashboard 매니페스트입니다. |
| `loadtests/` | k6 부하 시나리오입니다. `short_prompt`, `long_prompt`, `burst_traffic`이 있습니다. |
| `scripts/install-infra.sh` | Docker, k3s, kubectl, helm, k6 설치 스크립트입니다. |
| `scripts/run-experiment.sh` | 실험 실행 wrapper입니다. k6 실행, 시간 기록, `run.json` 생성을 담당합니다. |
| `scripts/teardown.sh` | 실험 환경 정리 스크립트입니다. |
| `analyzer/` | Prometheus 수집기, rule engine, report generator입니다. |
| `analyzer/config/metrics.yaml` | analyzer가 사용하는 PromQL 정의입니다. Rule 코드는 PromQL을 직접 하드코딩하지 않습니다. |
| `analyzer/config/rules.yaml` | rule threshold 설정입니다. |
| `analyzer/rules/` | 병목 진단 rule 구현입니다. |
| `analyzer/tests/` | analyzer 단위 테스트와 fixture replay 테스트입니다. |
| `analyzer/tests/fixtures/burst_baseline/` | 실제 Prometheus 응답을 캡처한 fixture입니다. |
| `docs/` | architecture, metrics, experiment plan, runbook 문서입니다. |

## 4. 현재 구현된 주요 기능

### mock LLM service

`mock-llm`은 실제 LLM 서버 대신 사용하는 테스트용 서비스입니다.

구현된 기능:

```text
POST /v1/chat/completions
GET  /healthz
GET  /readyz
GET  /metrics
```

동작 방식:

```text
요청이 들어오면 asyncio.Semaphore로 동시 처리 수를 제한한다.
처리 슬롯이 부족하면 requests_waiting이 증가한다.
대기 시간이 길어지면 queue_timeout error가 발생할 수 있다.
prompt 길이와 max_tokens에 따라 latency를 시뮬레이션한다.
Prometheus metric으로 running, waiting, latency, token, error를 노출한다.
```

### Kubernetes 배포

현재 Kubernetes 리소스:

```text
namespace: llm-ops
Deployment: mock-llm
Service: mock-llm, NodePort 30080
HPA: CPU utilization 기준
ServiceMonitor: Prometheus scrape 설정
```

중요한 점:

```text
Service targetPort는 named containerPort인 http를 사용한다.
Service selector와 Deployment label은 app=mock-llm으로 맞춰져 있다.
imagePullPolicy: Never를 사용하므로 k3s에 이미지를 직접 import해야 한다.
HPA는 CPU request가 있어야 동작하므로 Deployment에 CPU request가 설정되어 있다.
```

### Prometheus / Grafana

`kube-prometheus-stack`으로 다음을 설치합니다.

```text
Prometheus
Grafana
kube-state-metrics
node-exporter
Prometheus Operator
```

Prometheus는 다음 계열의 metric을 수집합니다.

```text
mock_llm_*                         mock LLM 자체 metric
kube_deployment_*                  desired/ready replicas
kube_pod_status_phase              Pending Pod 확인
container_cpu_usage_seconds_total  CPU 사용량
container_memory_working_set_bytes memory 사용량
```

### analyzer

analyzer는 Prometheus HTTP API만 사용합니다. Kubernetes API를 직접 조회하지 않습니다.

흐름:

```text
run.json에서 실험 시간 범위 읽기
analyzer/config/metrics.yaml에서 PromQL 읽기
Prometheus /api/v1/query_range 호출
raw payload를 TimeSeries로 변환
MetricSnapshot 생성
Rule.required_metrics가 만족되는 rule만 평가
Report 모델 생성
report.md와 report.json을 같은 Report 모델에서 렌더링
```

현재 rule:

| Rule | 의미 |
|---|---|
| `queue_bottleneck` | queue가 쌓이고 p95 latency가 높으면 trigger됩니다. |
| `cpu_bottleneck` | CPU 사용률과 latency가 같이 높으면 trigger됩니다. |
| `scale_out_lag` | desired replica가 ready replica보다 오래 앞서면 trigger됩니다. |
| `hpa_limitation` | CPU는 낮은데 queue와 latency가 높고 replica가 늘지 않으면 trigger됩니다. |
| `gpu_compute` | GPU metric이 있을 때 GPU compute 병목을 진단하기 위한 future rule입니다. |
| `gpu_memory` | GPU memory / KV cache 병목을 진단하기 위한 future rule입니다. |
| `gpu_scheduling` | GPU Pod scheduling 문제를 진단하기 위한 future rule입니다. |

GPU 관련 metric이 없으면 GPU rule은 자동으로 비활성 상태가 됩니다.

## 5. 실험 시나리오

| 시나리오 | 실행 명령 | 의도 |
|---|---|---|
| `short_prompt` | `bash scripts/run-experiment.sh short_prompt` | 정상 baseline입니다. 짧은 prompt와 낮은 latency를 기대합니다. |
| `long_prompt` | `bash scripts/run-experiment.sh long_prompt` | 요청 자체가 오래 걸리는 latency-only 상황입니다. queue 병목과 구분하기 위한 시나리오입니다. |
| `burst_traffic` | `bash scripts/run-experiment.sh burst_traffic` | 순간적으로 큰 부하를 넣어 queue 병목과 HPA 한계를 재현합니다. |
| `burst_traffic --high` | `bash scripts/run-experiment.sh burst_traffic --high` | 더 강한 spike로 scale-out lag까지 관찰할 수 있는 확장 시나리오입니다. |

## 6. 처음부터 실행하는 방법

아래 절차는 Ubuntu VM 기준입니다.

### 6.1 저장소 내려받기

```bash
git clone https://github.com/kkh1212/cloud-virtualization-proj.git
cd cloud-virtualization-proj
```

### 6.2 인프라 설치

```bash
bash scripts/install-infra.sh
```

설치되는 도구:

```text
Docker
k3s
kubectl
helm
k6
```

설치 후 Docker 권한이 바로 반영되지 않으면 다음 중 하나를 실행합니다.

```bash
newgrp docker
```

또는 SSH를 다시 접속합니다.

설치 확인:

```bash
docker --version
kubectl get nodes -o wide
helm version --short
k6 version
```

`kubectl get nodes`에서 node가 `Ready`이면 다음 단계로 진행합니다.

### 6.3 mock-llm 이미지 빌드 및 k3s import

```bash
docker build -t mock-llm:dev mock-llm/
docker save mock-llm:dev | sudo k3s ctr images import -
sudo k3s ctr images ls | grep mock-llm
```

이 단계가 필요한 이유:

```text
k8s/mock-llm-deployment.yaml은 imagePullPolicy: Never를 사용한다.
따라서 cluster가 Docker Hub에서 이미지를 pull하지 않는다.
로컬에서 빌드한 mock-llm:dev 이미지를 k3s container runtime에 직접 넣어야 한다.
```

### 6.4 Kubernetes 리소스 배포

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/mock-llm-deployment.yaml
kubectl apply -f k8s/mock-llm-service.yaml
kubectl apply -f k8s/mock-llm-hpa.yaml
```

상태 확인:

```bash
kubectl -n llm-ops get pods,svc,hpa
```

정상 예시:

```text
pod/mock-llm-...   1/1   Running
service/mock-llm   NodePort   ...   8000:30080/TCP
hpa/mock-llm       Deployment/mock-llm
```

서비스 헬스체크:

```bash
curl -fsS http://localhost:30080/healthz && echo OK
```

`OK`가 나오면 mock LLM 서비스가 NodePort로 접근 가능한 상태입니다.

### 6.5 Prometheus / Grafana 설치

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install prom prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f k8s/prometheus-values.yaml
```

Pod 준비 확인:

```bash
kubectl -n monitoring get pods
```

Prometheus 관련 Pod와 Grafana Pod가 `Running` 또는 `Completed` 상태가 될 때까지 기다립니다.

ServiceMonitor와 Grafana dashboard 적용:

```bash
kubectl apply -f k8s/mock-llm-servicemonitor.yaml
kubectl apply -f k8s/grafana-dashboard-llm-overview.yaml
```

### 6.6 analyzer Python 환경 준비

```bash
python3 -m venv analyzer/.venv
analyzer/.venv/bin/pip install -r analyzer/requirements-dev.txt
```

테스트:

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
```

fixture가 포함된 상태라면 정상적으로 다음처럼 나와야 합니다.

```text
21 passed
```

## 7. 실제 사용 흐름

실험할 때는 터미널을 2개 또는 3개 열어두는 것을 추천합니다.

### Terminal 1: 실험 실행 / analyzer 실행

프로젝트 루트에서 실행합니다.

```bash
cd cloud-virtualization-proj
```

또는 VM의 기존 경로라면:

```bash
cd /home/azureuser/llm-ops-platform
```

### Terminal 2: Prometheus port-forward

analyzer가 Prometheus HTTP API를 호출할 수 있게 port-forward를 켜둡니다.

```bash
kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090
```

정상 예시:

```text
Forwarding from 127.0.0.1:9090 -> 9090
Forwarding from [::1]:9090 -> 9090
```

이 터미널은 실험/분석 중에 끄지 않습니다.

### Terminal 3: 부하 중 실시간 관측 선택 사항

부하가 들어가는 동안 queue와 error가 어떻게 움직이는지 볼 수 있습니다.

```bash
watch -n 1 'curl -s http://localhost:30080/metrics \
  | grep -E "^mock_llm_(requests_running|requests_waiting|errors_total)"'
```

HPA와 Pod 상태를 같이 보려면:

```bash
watch -n 2 'kubectl -n llm-ops get hpa,pods'
```

## 8. 실험 한 사이클 실행

### 8.1 burst traffic 실험 실행

Terminal 1에서:

```bash
bash scripts/run-experiment.sh burst_traffic
```

정상 예시:

```text
[INFO] Checking mock-llm Deployment
[INFO] Checking mock-llm NodePort health
[INFO] Running k6 scenario=burst_traffic intensity=normal
[INFO] k6 completed
[INFO] Waiting 30s for Prometheus scrape buffer
[INFO] 다음: analyzer/.venv/bin/python -m analyzer.main --run reports/burst_traffic-20260517T110305Z
```

스크립트가 하는 일:

```text
mock-llm Deployment 존재 확인
NodePort health check
k6 부하 실행
reports/<scenario>-<timestamp>/ 생성
k6.log 저장
k6_summary.json 저장
Prometheus scrape buffer 30초 대기
run.json 생성
```

### 8.2 최신 실험 디렉토리 선택

```bash
RUN_DIR=$(ls -dt reports/burst_traffic-* | head -1)
echo "분석 대상: $RUN_DIR"
```

### 8.3 analyzer 실행

```bash
analyzer/.venv/bin/python -m analyzer.main --run "$RUN_DIR"
```

정상 예시:

```text
wrote reports/burst_traffic-20260517T110305Z/report.md
wrote reports/burst_traffic-20260517T110305Z/report.json
triggered_rules=2
```

`triggered_rules=2`는 이번 burst traffic 실험에서 2개의 진단 rule이 발동했다는 뜻입니다.

### 8.4 리포트 보기

```bash
cat "$RUN_DIR/report.md"
```

## 9. report.md 읽는 방법

리포트는 7개 섹션으로 구성됩니다.

```text
1. 테스트 요약
2. 성능 결과
3. LLM 상태
4. Kubernetes 상태
5. 자원 상태
6. 진단
7. 개선 방향
```

### 9.1 테스트 요약

예시:

```text
총 요청 수(추정): 2753
적용된 진단 룰: 4
Triggered 룰: 2
```

해석:

```text
실험 시간 동안 약 2753개의 요청이 처리되거나 시도되었다.
현재 metric이 충분해서 적용 가능한 rule은 4개였다.
그중 2개가 실제 문제로 판단되었다.
```

### 9.2 성능 결과

예시:

```text
평균 latency: 17.782s
p95 latency peak: 30.000s
p99 latency peak: 30.000s
error rate peak: 23.878
throughput avg: 15.209 req/s
throughput peak: 41.782 req/s
```

해석:

```text
평균 응답 시간이 매우 길다.
p95/p99가 30초까지 올라갔다.
burst traffic 구간에서 queue timeout 또는 지연이 크게 발생했다.
```

### 9.3 LLM 상태

예시:

```text
max running: 8
max waiting: 1340
```

해석:

```text
동시에 처리 중인 요청은 최대 8개였다.
하지만 처리 슬롯을 기다리는 요청은 최대 1340개까지 쌓였다.
즉 처리 capacity보다 유입 요청이 훨씬 많았다.
```

여기서 `max running`이 8 근처인 이유는 현재 mock-llm이 다음 구조이기 때문입니다.

```text
replicas=2
Pod당 max concurrency=4
총 처리 슬롯 약 8
```

### 9.4 Kubernetes 상태

예시:

```text
desired replicas first -> last: 2 -> 2
ready replicas first -> last: 2 -> 2
pending pod max: 0
```

해석:

```text
실험 중 replica 수가 늘어나지 않았다.
Pod는 Pending 상태가 아니었다.
즉 scheduling 문제라기보다는 HPA가 scale-out하지 않은 상황이다.
```

### 9.5 자원 상태

예시:

```text
CPU 평균(request 대비): 0.03x
CPU peak(request 대비): 0.07x
memory avg: 138.80 MiB
GPU: 현재 미수집
```

해석:

```text
CPU는 거의 바쁘지 않았다.
CPU 기반 HPA 입장에서는 scale-out할 이유가 부족했다.
하지만 실제 사용자 관점에서는 queue가 크게 쌓이고 latency가 높았다.
```

이 프로젝트의 핵심 포인트가 여기서 드러납니다.

```text
LLM 서비스 병목은 CPU 사용률만으로 설명되지 않을 수 있다.
queue/concurrency metric을 함께 봐야 한다.
```

### 9.6 진단

이번 검증에서 실제로 나온 진단:

```text
queue_bottleneck
hpa_limitation
```

`queue_bottleneck` 의미:

```text
requests_waiting이 threshold보다 높다.
p95 latency도 threshold보다 높다.
따라서 요청이 처리 슬롯을 기다리며 queue에 쌓이는 병목이다.
```

`hpa_limitation` 의미:

```text
CPU 사용률은 낮다.
queue는 길다.
latency는 높다.
replica 수는 늘지 않았다.
따라서 CPU 기반 HPA가 이 병목을 잡지 못했다.
```

### 9.7 개선 방향

예시:

```text
유입 RPS가 처리 capacity를 넘어섭니다.
replicas 또는 max_concurrency 상향을 검토합니다.
queue 기반 autoscaling 도입을 검토합니다.
CPU 기준 autoscaling이 queue 부하를 못 잡습니다.
```

실제 운영 개선으로 연결하면:

```text
replica 수 증가
Pod당 max concurrency 조정
queue timeout 조정
CPU HPA 대신 queue 기반 custom metric autoscaling
KEDA 또는 Prometheus Adapter 기반 autoscaling
```

## 10. 이번에 검증한 실제 결과

현재 환경에서 다음 실험을 실행했습니다.

```bash
bash scripts/run-experiment.sh burst_traffic
```

생성된 run:

```text
reports/burst_traffic-20260517T110305Z
```

analyzer 결과:

```text
triggered_rules=2
```

핵심 수치:

```text
총 요청 수(추정): 2753
평균 latency: 17.782s
p95 latency peak: 30.000s
p99 latency peak: 30.000s
max running: 8
max waiting: 1340
desired replicas: 2 -> 2
ready replicas: 2 -> 2
CPU 평균(request 대비): 0.03x
CPU peak(request 대비): 0.07x
Triggered rule: queue_bottleneck, hpa_limitation
```

결론:

```text
burst traffic 상황에서 mock LLM 서비스의 queue가 크게 증가했다.
latency도 크게 증가했다.
하지만 CPU 사용률은 낮았다.
CPU 기반 HPA는 replica를 늘리지 않았다.
따라서 현재 병목은 CPU 병목이 아니라 queue/concurrency 병목이다.
```

## 11. fixture capture와 replay 테스트

Prometheus는 retention이 지나면 오래된 실험 데이터를 잃을 수 있습니다. 그래서 실제 Prometheus 응답을 fixture로 저장해두면, 나중에 cluster 없이도 analyzer parsing과 rule 평가를 회귀 테스트할 수 있습니다.

fixture 캡처:

```bash
RUN_DIR=$(ls -dt reports/burst_traffic-* | head -1)

analyzer/.venv/bin/python -m analyzer.tools.capture_fixtures \
  --run "$RUN_DIR" \
  --output analyzer/tests/fixtures/burst_baseline
```

정상 예시:

```text
[OK] requests_total         series=1
[OK] requests_running       series=1
[OK] requests_waiting       series=1
[OK] avg_latency            series=1
[OK] p95_latency            series=1
[OK] p99_latency            series=1
[OK] error_rate             series=1
[OK] prompt_token_rate      series=1
[OK] output_token_rate      series=1
[OK] cpu_usage_ratio        series=1
[OK] memory_bytes           series=1
[OK] replicas_desired       series=1
[OK] replicas_ready         series=1
[OK] pod_pending_count      series=1

fixture saved to analyzer/tests/fixtures/burst_baseline
responses.json: 14 promql entries
```

replay 테스트:

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
```

현재 검증 결과:

```text
21 passed
```

이 의미:

```text
실제 Prometheus raw JSON 응답을 analyzer가 다시 읽을 수 있다.
collector parsing이 정상이다.
rule evaluation이 crash 없이 돈다.
report.md와 report.json 렌더링이 정상이다.
PromQL 오타나 Prometheus payload schema 변화가 생기면 테스트로 잡을 수 있다.
```

## 12. 자주 쓰는 명령 모음

### cluster 상태 확인

```bash
kubectl -n llm-ops get pods,svc,hpa
kubectl -n monitoring get pods
curl -fsS http://localhost:30080/healthz && echo OK
```

### Prometheus port-forward

```bash
kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090
```

### Grafana port-forward

```bash
kubectl -n monitoring port-forward svc/prom-grafana 3000:80
```

브라우저:

```text
http://localhost:3000
```

개발용 기본 계정:

```text
admin / admin
```

대시보드:

```text
LLM Operations - Overview
```

### 최신 burst report 보기

```bash
RUN_DIR=$(ls -dt reports/burst_traffic-* | head -1)
cat "$RUN_DIR/report.md"
```

### analyzer strict mode

```bash
analyzer/.venv/bin/python -m analyzer.main --run "$RUN_DIR" --strict
echo "exit code: $?"
```

`--strict`는 Prometheus 연결 실패나 HTTP 실패를 exit code `2`로 처리합니다.

주의:

```text
Prometheus 연결은 성공했지만 query 결과가 no series인 경우는 strict failure가 아니다.
예전 실험 시간이 Prometheus retention 밖이면 no series가 나올 수 있다.
이 경우 새 실험을 실행하거나 fixture replay를 사용한다.
```

### 테스트 실행

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
```

선택적으로 mock-llm 테스트:

```bash
python3 -m venv mock-llm/.venv
mock-llm/.venv/bin/pip install -r mock-llm/requirements.txt -r mock-llm/requirements-dev.txt
mock-llm/.venv/bin/pytest mock-llm/tests -v
```

## 13. 문제 해결

### `curl http://localhost:30080/healthz`가 실패

확인:

```bash
kubectl -n llm-ops get pods,svc,endpoints
kubectl -n llm-ops logs -l app=mock-llm --tail=100
```

가능한 원인:

```text
Pod가 Running이 아니다.
Service endpoint가 비어 있다.
이미지를 k3s에 import하지 않았다.
Deployment label과 Service selector가 맞지 않는다.
```

### Pod가 `ImagePullBackOff` 또는 `ErrImageNeverPull`

해결:

```bash
docker build -t mock-llm:dev mock-llm/
docker save mock-llm:dev | sudo k3s ctr images import -
kubectl -n llm-ops rollout restart deploy/mock-llm
kubectl -n llm-ops rollout status deploy/mock-llm
```

### analyzer가 모든 PromQL에 `no series` 출력

가능한 원인:

```text
Prometheus port-forward가 꺼져 있다.
Prometheus가 mock-llm을 scrape하지 못하고 있다.
분석하려는 run 시간이 Prometheus retention 밖이다.
```

확인:

```bash
curl -s 'http://localhost:9090/api/v1/query?query=mock_llm_requests_total' \
  | python3 -m json.tool | head -40
```

현재 metric이 있으면 `result` 배열에 값이 나옵니다. 비어 있으면 scrape 상태를 봅니다.

```bash
curl -s 'http://localhost:9090/api/v1/targets?state=active' | python3 -m json.tool | head -80
```

오래된 run이면 새 실험을 실행합니다.

```bash
bash scripts/run-experiment.sh burst_traffic
```

### fixture replay가 실패

의미:

```text
실제 Prometheus 응답 형식과 analyzer parsing이 맞지 않거나,
metrics.yaml PromQL이 기대한 series를 만들지 못했을 가능성이 있다.
```

확인:

```bash
analyzer/.venv/bin/pytest analyzer/tests/test_fixture_integration.py -v
```

실패 메시지에 나온 metric 이름부터 `analyzer/config/metrics.yaml`을 확인합니다.

## 14. 현재 부족한 점

MVP 파이프라인은 끝까지 동작합니다. 다만 실제 운영 도구로 확장하려면 다음 한계가 남아 있습니다.

### 14.1 CPU 기반 HPA 한계

현재 HPA는 CPU 기준입니다.

이번 실험 결과:

```text
CPU 평균: 0.03x
max waiting: 1340
replicas: 2 -> 2
```

즉 queue는 터졌지만 CPU가 낮아서 HPA가 움직이지 않았습니다. LLM inference에서는 CPU보다 queue length, concurrency, GPU utilization, KV cache 같은 metric이 더 직접적인 autoscaling 신호일 수 있습니다.

### 14.2 queue 기반 autoscaling 미구현

아직 다음은 구현되어 있지 않습니다.

```text
Prometheus Adapter 기반 custom metric HPA
KEDA 기반 queue metric autoscaling
mock_llm_requests_waiting 기반 scale-out
queue length per replica 기준 autoscaling
```

### 14.3 GPU 실환경 검증 미완료

GPU rule은 구조만 준비되어 있습니다.

현재 상태:

```text
GPU metric 없음
gpu_compute / gpu_memory / gpu_scheduling rule은 required_metrics 부족으로 skip
```

실제 GPU VM에서는 다음이 필요합니다.

```text
NVIDIA device plugin
DCGM exporter
vLLM 또는 GPU 기반 inference server
GPU metric PromQL 활성화
GPU rule threshold 재검증
```

### 14.4 비용 분석 없음

현재 리포트는 성능과 운영 병목 중심입니다. 아직 비용 분석은 없습니다.

추가하면 좋은 항목:

```text
cost per request
cost per 1K tokens
replica 증가에 따른 비용 변화
GPU 시간당 비용 대비 처리량
```

### 14.5 실험 간 비교 기능 없음

현재 analyzer는 하나의 run을 분석합니다.

아직 없는 기능:

```text
run A vs run B 비교
replicas 2 vs 4 비교
CPU HPA vs queue HPA 비교
max_concurrency 4 vs 8 비교
개선 전/후 report diff
```

### 14.6 CI/CD 미구현

현재는 로컬/VM에서 테스트를 직접 실행합니다.

추가하면 좋은 것:

```text
GitHub Actions
analyzer pytest 자동 실행
mock-llm pytest 자동 실행
YAML manifest lint
Docker build 검증
README command smoke check
```

## 15. 다음에 해야 할 작업

우선순위 기준 추천 작업입니다.

### 1순위: queue 기반 autoscaling 실험

목표:

```text
CPU HPA가 놓친 queue 병목을 custom metric autoscaling으로 줄일 수 있는지 검증한다.
```

예상 작업:

```text
Prometheus Adapter 또는 KEDA 설치
mock_llm_requests_waiting metric을 autoscaling 신호로 연결
queue HPA manifest 추가
burst_traffic 재실행
기존 CPU HPA 결과와 비교
```

기대 결과:

```text
replicas가 2에서 증가한다.
max waiting이 감소한다.
p95 latency peak가 낮아진다.
hpa_limitation rule이 더 이상 trigger되지 않는다.
```

### 2순위: 실험 비교 리포트

목표:

```text
두 개의 reports 디렉토리를 비교해서 개선 효과를 한눈에 보여준다.
```

예시:

```bash
analyzer/.venv/bin/python -m analyzer.compare \
  --before reports/burst_traffic-cpu-hpa \
  --after reports/burst_traffic-queue-hpa
```

보고 싶은 값:

```text
평균 latency 변화
p95 peak 변화
max waiting 변화
error rate 변화
replica 변화
triggered rule 변화
```

### 3순위: GPU VM 확장

목표:

```text
mock LLM을 실제 GPU inference server로 교체하고 GPU 병목 rule을 활성화한다.
```

예상 작업:

```text
NVIDIA device plugin 설치
DCGM exporter 설치
vLLM 배포
analyzer/config/metrics.yaml GPU PromQL 활성화
gpu_compute / gpu_memory / gpu_scheduling rule 검증
```

### 4순위: 비용 metric 추가

목표:

```text
성능 개선이 비용 대비 타당한지 판단한다.
```

예상 출력:

```text
cost per request
cost per 1K tokens
cost per successful request
latency improvement per additional replica
```

### 5순위: CI 추가

목표:

```text
GitHub에 push할 때 analyzer와 mock-llm 테스트가 자동으로 돈다.
```

권장 체크:

```text
analyzer/.venv/bin/pytest analyzer/tests -v
mock-llm/.venv/bin/pytest mock-llm/tests -v
python -m compileall analyzer mock-llm/app
```

## 16. 정리 명령

일반 정리:

```bash
bash scripts/teardown.sh
```

이미지와 reports까지 제거:

```bash
bash scripts/teardown.sh --all --yes
```

k3s까지 제거하는 완전 초기화:

```bash
bash scripts/teardown.sh --nuke --yes
```

`--all`, `--nuke`는 파괴적인 작업이므로 실험 결과를 보존해야 하면 먼저 `reports/`를 따로 확인합니다.

## 17. 참고 문서

| 문서 | 설명 |
|---|---|
| `docs/architecture.md` | 전체 컴포넌트와 데이터 흐름 설명 |
| `docs/metrics.md` | Prometheus metric과 analyzer metric mapping |
| `docs/experiment-plan.md` | 시나리오별 가설과 기대 결과 |
| `docs/runbook.md` | 단계별 설치/운영 절차 |
| `AGENTS.md` | Codex advisory review 규칙 |
| `CLAUDE.md` | Claude Code 작업 규칙 |

## 18. 현재 상태 요약

```text
MVP 파이프라인 구현 완료
Kubernetes 배포 완료
k6 burst traffic 실험 성공
Prometheus 수집 성공
analyzer 리포트 생성 성공
queue_bottleneck / hpa_limitation 진단 성공
실제 Prometheus fixture 캡처 성공
fixture replay 포함 analyzer 테스트 21 passed
```

현재 프로젝트는 "LLM 서비스 운영 진단 플랫폼 MVP"에서 KEDA 비교/비용 추정/CI/GPU 이관 준비까지 확장된 상태입니다. 다음 단계는 실제 클러스터에서 CPU HPA baseline과 KEDA queue autoscaling run을 각각 만들어 비교하는 것입니다.

## 19. GPU 이관 전 확장 기능 사용법

### 19.1 CPU HPA baseline 실험

기존 CPU 기반 HPA를 baseline으로 쓸 때는 먼저 autoscaling 모드를 CPU HPA로 맞춥니다.

```bash
bash scripts/use-cpu-hpa.sh
```

그 다음 burst traffic 실험과 analyzer를 실행합니다.

```bash
bash scripts/run-experiment.sh burst_traffic
CPU_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$CPU_RUN" --cost-profile custom
cat "$CPU_RUN/report.md"
```

볼 것:

```text
Triggered rule: queue_bottleneck, hpa_limitation
replicas desired/ready가 2 근처에 머무는지
max waiting과 p95/p99 latency가 높은지
비용 추정 섹션이 생성됐는지
```

### 19.2 KEDA 설치

KEDA는 Prometheus metric을 autoscaling 신호로 쓰기 위해 필요합니다.

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda -n keda --create-namespace
kubectl -n keda rollout status deploy/keda-operator
```

### 19.3 KEDA queue autoscaling 실험

CPU HPA와 KEDA가 동시에 같은 Deployment를 제어하면 안 됩니다. KEDA 실험 전에는 아래 스크립트로 CPU HPA를 제거하고 KEDA ScaledObject를 적용합니다.

```bash
bash scripts/use-keda-queue.sh
```

적용되는 기준:

```text
metric: sum(mock_llm_requests_waiting)
threshold: 20
minReplicaCount: 2
maxReplicaCount: 8
```

실험 실행:

```bash
bash scripts/run-experiment.sh burst_traffic
KEDA_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$KEDA_RUN" --cost-profile custom
cat "$KEDA_RUN/report.md"
```

볼 것:

```text
desired replicas max가 2보다 커지는지
max waiting이 CPU baseline보다 줄었는지
p95/p99 latency peak가 줄었는지
hpa_limitation이 사라지거나 약해졌는지
```

### 19.4 CPU HPA vs KEDA 비교 리포트

두 run의 report.json이 생성된 뒤 비교합니다.

```bash
analyzer/.venv/bin/python -m analyzer.compare \
  --before "$CPU_RUN" \
  --after "$KEDA_RUN"

cat "$KEDA_RUN/comparison.md"
```

비교 리포트에서 볼 것:

```text
max waiting delta
p95/p99 latency delta
error rate delta
replica max 변화
removed triggered rules
added triggered rules
```

### 19.5 비용 profile 설정

비용 분석은 클라우드 과금 API를 호출하지 않습니다. `analyzer/config/cost.yaml`에 직접 단가를 넣습니다.

예시:

```yaml
profiles:
  custom:
    currency: USD
    hourly_per_mock_llm_replica: 0.05
    hourly_cluster_overhead: 0.10
    hourly_gpu_node: 0.00
```

사용:

```bash
analyzer/.venv/bin/python -m analyzer.main --run "$RUN_DIR" --cost-profile custom
```

리포트의 `비용 추정` 섹션에 다음이 표시됩니다.

```text
estimated run cost
cost per 1K requests
cost per 1K tokens
사용한 cost profile
```

### 19.6 GPU 이관 준비

GPU 서버를 구한 뒤에는 아래 문서를 기준으로 이동합니다.

```text
docs/gpu-migration.md
```

GPU 이관 전 현재 CPU/mock 환경에서 끝내야 하는 검증은 다음입니다.

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
mock-llm/.venv/bin/pytest mock-llm/tests -v
python3 -m compileall analyzer mock-llm/app
```

## 20. 확장 후 상태 요약

```text
CPU HPA baseline 실험 가능
KEDA queue autoscaling 실험 가능
CPU HPA vs KEDA 비교 리포트 가능
클라우드별 수동 비용 profile 기반 비용 추정 가능
GitHub Actions CI 추가
GPU 서버 이관 체크리스트 추가
```

---

## 21. 프로젝트 전체 방향 - 워크로드 맞춤형 LLM 인프라 진단

이 프로젝트의 핵심 목표는 단순히 LLM 서버를 Kubernetes에 올리는 것이 아니라, **사용하려는 LLM 서비스의 워크로드 특성에 현재 인프라 설정이 적합한지 부하 테스트와 메트릭으로 판단하는 것**입니다.

일반적인 웹 서비스와 달리 LLM inference 서비스는 요청의 형태에 따라 병목이 크게 달라집니다.

```text
짧은 프롬프트 + 짧은 응답이 많은 서비스
→ 요청 수, queue, replica 수, autoscaling 반응 속도가 중요

긴 프롬프트 또는 긴 context를 주로 쓰는 서비스
→ TTFT, prefill 비용, KV cache, GPU memory, timeout이 중요

짧은 프롬프트지만 긴 답변을 생성하는 서비스
→ decode 시간, TPOT, output token throughput, p95/p99 latency가 중요

실제 운영처럼 여러 요청 유형이 섞이는 서비스
→ 평균 latency보다 p95/p99 long-tail, 특정 요청군 병목이 중요
```

따라서 모든 LLM 서비스를 "무조건 빠르게" 처리하도록 과하게 설정하는 것이 목표가 아닙니다. 그렇게 하면 비용이 커집니다. 이 프로젝트가 보고 싶은 것은 **서비스의 실제 사용 패턴에 맞게 적당한 성능과 적당한 비용의 균형점을 찾는 것**입니다.

### 21.1 이 프로젝트가 답하려는 질문

부하 테스트 결과를 기반으로 다음 질문에 답하는 것이 목표입니다.

```text
이 서비스는 짧은 요청이 많은 서비스인가, 긴 context가 많은 서비스인가?
현재 replica 수와 max concurrency는 이 workload에 적합한가?
CPU 기반 HPA가 충분한가, queue 기반 KEDA autoscaling이 필요한가?
지연이 생겼을 때 원인은 CPU인가, queue인가, Pod 준비 지연인가?
vLLM/GPU 환경에서는 GPU compute 병목인가, GPU memory/KV cache 압박인가?
설정을 바꾸면 latency는 얼마나 줄고 비용은 얼마나 늘어나는가?
현재 설정이 이 서비스에 과한가, 부족한가, 아니면 적절한가?
```

즉 최종적으로는 다음과 같은 판단을 돕는 플랫폼을 지향합니다.

```text
현재 설정은 short_prompt 중심 서비스에는 적합하지만,
long_context 서비스에서는 TTFT와 KV cache 압박 때문에 부적합하다.

또는

현재 CPU HPA 설정은 평균 CPU가 낮아 scale-out하지 못하므로,
요청 queue가 중요한 이 서비스에는 KEDA queue autoscaling이 더 적합하다.

또는

max concurrency를 높이면 throughput은 늘지만 p99 latency와 GPU memory 사용률이 악화되므로,
이 workload에서는 replica 증가가 더 안전하다.
```

### 21.2 현재 구현된 실험 구조

현재 구현된 MVP/확장 흐름은 다음과 같습니다.

```text
k6 load test
  -> mock-llm 또는 향후 vLLM
  -> Kubernetes Deployment / Service / HPA or KEDA
  -> Prometheus metrics
  -> analyzer rule engine
  -> report.md / report.json
  -> before-after comparison
```

현재는 실제 vLLM/GPU 서버로 가기 전 단계로, `mock-llm`이 LLM 서비스에서 자주 나타나는 운영 현상을 재현합니다.

```text
동시 처리량 제한
요청 대기 queue
queue timeout
TTFT
TPOT
token throughput
batch size
KV-cache usage proxy
p95/p99 latency 증가
```

이 mock 환경을 통해 Kubernetes, Prometheus, Grafana, analyzer, KEDA 비교 리포트가 end-to-end로 동작하는지 먼저 검증했습니다.

### 21.3 부하 시나리오와 워크로드 의미

현재 k6 부하 시나리오는 워크로드 특성별로 나뉘어 있습니다.

| 시나리오 | 의미 | 주로 확인하는 것 |
|---|---|---|
| `short_prompt` | 짧은 질문과 짧은 응답이 많은 baseline 서비스 | 낮은 latency, 낮은 queue, 정상 throughput |
| `long_prompt` | 긴 prompt와 긴 output을 처리하는 서비스 | 요청 자체의 latency, TTFT/TPOT, long-context 부담 |
| `mixed_workload` | 짧은 QA, 긴 요약, 코드 생성 요청이 섞인 실제 운영형 트래픽 | p95/p99 long-tail, 일부 무거운 요청의 영향 |
| `burst_traffic` | 순간적으로 요청이 몰리는 spike 상황 | queue bottleneck, error, HPA/KEDA 반응 한계 |
| `sustained_ramp` | 점진적으로 부하가 올라가는 autoscaling 검증용 상황 | scale-out이 제때 되는지, ready replica가 따라오는지 |

이 시나리오들은 단순히 "서버가 버티는지"만 보는 것이 아니라, **어떤 워크로드에서 어떤 지표가 나빠지는지**를 보기 위한 것입니다.

예를 들어:

```text
short_prompt에서 requests_waiting과 p95가 높다
→ 요청 수에 비해 처리 capacity 또는 autoscaling이 부족할 가능성

long_prompt에서 queue는 낮은데 TTFT와 p95가 높다
→ 요청 자체가 무겁거나 prefill/context 처리 부담이 큰 workload

mixed_workload에서 평균은 괜찮은데 p99가 높다
→ 일부 긴 요청이 long-tail latency를 만들 가능성

sustained_ramp에서 desired replica는 늘지만 ready replica가 늦다
→ scale_out_lag, Pod startup/readiness 또는 image pull 지연 가능성
```

### 21.4 analyzer가 보는 주요 지표

analyzer는 Prometheus의 시계열을 읽어 리포트를 만듭니다.

성능 지표:

```text
avg_latency
p95_latency
p99_latency
error_rate
throughput
```

LLM 서비스 지표:

```text
requests_running
requests_waiting
TTFT p95
TPOT p95
prompt token throughput
output token throughput
batch size
KV-cache usage proxy
```

Kubernetes / autoscaling 지표:

```text
replicas_desired
replicas_ready
pod_pending_count
cpu_usage_ratio
memory_bytes
```

향후 vLLM/GPU 환경에서 추가할 지표:

```text
GPU utilization
GPU memory used ratio
vLLM KV cache usage
vLLM waiting/running requests
vLLM token throughput
```

이 지표 조합으로 analyzer는 단순히 "느리다"가 아니라, 왜 느린지에 대한 근거를 제시합니다.

```text
queue_bottleneck
→ requests_waiting과 p95 latency가 함께 높음

hpa_limitation
→ CPU는 낮은데 queue와 latency는 높고 replica가 늘지 않음

scale_out_lag
→ desired replica는 늘었지만 ready replica가 늦게 따라옴

cpu_bottleneck
→ CPU 사용률과 latency가 함께 높음

gpu_compute / gpu_memory / gpu_scheduling
→ GPU metric이 연결된 뒤 실제 GPU 병목 판단에 사용
```

### 21.5 사용하는 도구와 오픈소스 역할

| 도구 / 오픈소스 | 현재 역할 |
|---|---|
| Kubernetes / k3s | LLM 서비스 컨테이너를 Pod/Deployment/Service/HPA로 관리하는 실험 환경 |
| Docker | mock-llm 이미지를 빌드하고 k3s runtime에 import |
| k6 | 짧은 프롬프트, 긴 프롬프트, burst, sustained ramp 등 부하 생성 |
| FastAPI | 현재 `mock-llm` API 서버 구현 |
| prometheus-client | mock-llm의 `/metrics` 노출 |
| kube-prometheus-stack | Prometheus, Grafana, kube-state-metrics, node-exporter 설치 |
| Prometheus | mock-llm, Kubernetes, container metric 수집 및 PromQL 제공 |
| Grafana | 실험 중 latency, queue, replica, resource 지표 시각화 |
| kube-state-metrics | Deployment replica, Pod phase 등 Kubernetes 상태 지표 제공 |
| metrics-server | CPU HPA resource metric 동작 확인에 필요 |
| KEDA | `mock_llm_requests_waiting` 같은 queue metric 기반 autoscaling |
| Python analyzer | Prometheus query_range 수집, rule 평가, Markdown/JSON 리포트 생성 |
| vLLM | 향후 실제 LLM inference server로 교체할 대상 |
| DCGM exporter | NVIDIA GPU 환경에서 GPU utilization/memory 지표 수집 예정 |
| OpenCost | 향후 Kubernetes 비용 지표를 수집해 cost per request/token 분석에 활용 예정 |

현재 비용 기능은 OpenCost 직접 연동 전 단계입니다. `analyzer/config/cost.yaml`의 수동 단가 profile을 기반으로 다음 값을 추정합니다.

```text
estimated run cost
cost per 1K requests
cost per 1K tokens
avg billable replicas
```

### 21.6 워크로드별 적합성 판단 방향

앞으로 analyzer는 단일 SLO 판정뿐 아니라, 워크로드 유형별 적합성 평가로 확장할 수 있습니다.

예시 profile:

```text
short_interactive
long_context
decode_heavy
mixed_production
burst_sensitive
```

각 profile은 서로 다른 기준을 가질 수 있습니다.

| 워크로드 profile | 중요한 기준 | 부적합 신호 |
|---|---|---|
| `short_interactive` | p95 latency, queue, throughput, cost/request | requests_waiting 증가, p95 급등, scale-out 지연 |
| `long_context` | TTFT, KV cache, GPU memory, timeout | TTFT 초과, memory/KV 압박, p99 악화 |
| `decode_heavy` | TPOT, output token throughput, p99 latency | TPOT 상승, output throughput 한계 |
| `mixed_production` | p95/p99 long-tail, error rate, 비용 | 평균은 정상이나 p99가 높음 |
| `burst_sensitive` | spike 흡수력, KEDA 반응 속도, ready replica 지연 | burst 중 queue timeout, scale_out_lag |

이 방향의 최종 리포트는 다음처럼 읽히는 것이 목표입니다.

```text
워크로드 유형: long_context
판정: 현재 설정은 부분 부적합

근거:
- TTFT p95가 목표를 초과
- p99 latency가 long_context SLO를 초과
- KV cache/GPU memory 압박 가능성

권장:
- 더 큰 VRAM GPU 사용 검토
- max concurrency 하향 또는 replica 분리
- max_model_len / max_num_batched_tokens 조정
- long-context 전용 Deployment 분리
```

또는:

```text
워크로드 유형: short_interactive
판정: CPU HPA 설정은 부적합, KEDA queue autoscaling 권장

근거:
- CPU 사용률은 낮음
- requests_waiting은 높음
- p95 latency가 상승
- replicas_desired가 증가하지 않음

권장:
- queue 기반 KEDA autoscaling 사용
- KEDA threshold 하향
- minReplicaCount 상향
- maxReplicaCount 상향
```

### 21.7 현재 상태와 다음 목표

현재까지는 다음을 검증했습니다.

```text
mock-llm 기반 LLM-like workload 재현
k6 부하 테스트
Prometheus/Grafana 수집 및 시각화
CPU HPA baseline 실험
KEDA queue autoscaling 실험
CPU HPA vs KEDA 비교 리포트
SLO 판정
추천 설정 생성
수동 비용 profile 기반 비용 추정
fixture replay 기반 analyzer 테스트
```

다음 단계는 실제 운영 환경에 더 가까워지는 것입니다.

```text
mock-llm을 vLLM으로 교체
vLLM Prometheus metric을 analyzer/config/metrics.yaml에 연결
GPU metric exporter 연결
GPU compute / memory / scheduling rule 실측 검증
OpenCost 기반 실제 비용 metric 연결
워크로드 profile별 적합성 평가 섹션 추가
설정 A/B 비교를 통해 성능-비용 tradeoff를 더 명확히 표시
```

정리하면 이 프로젝트는 **LLM 서비스를 Kubernetes에서 운영할 때, 현재 인프라와 설정이 사용하려는 워크로드에 맞는지 부하 테스트로 검증하고, 병목과 개선 방향을 리포트로 설명하는 플랫폼**을 목표로 합니다.
