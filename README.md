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
analyzer/.venv/bin/pip install -r analyzer/requirements.txt
```

테스트:

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
```

fixture가 포함된 상태라면 정상적으로 다음처럼 나와야 합니다.

```text
16 passed
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
16 passed in 0.21s
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
fixture replay 포함 analyzer 테스트 16 passed
```

현재 프로젝트는 "LLM 서비스 운영 진단 플랫폼 MVP"로서 끝까지 실행 가능한 상태입니다. 다음 단계는 CPU HPA의 한계를 개선하기 위한 queue 기반 autoscaling 실험입니다.
