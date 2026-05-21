# Runbook — llm-ops-platform

이 문서는 **새 VM에서 처음 환경을 만들 때부터 분석 리포트가 나올 때까지**의 절차를 모아둔다.
현재 MVP Phase 0~9 완료 상태를 기준으로 유지한다.

---

## Phase 0 — 인프라 설치

### 사전 조건
- Ubuntu 22.04+ (Debian 계열). 다른 배포판은 검증되지 않음.
- `sudo` 권한이 있는 일반 사용자 (root 직접 사용 비권장).
- 네트워크 접속 가능 (apt repo, get.k3s.io, dl.k6.io, get-helm-3 다운로드).

### 1. 설치
```bash
bash scripts/install-infra.sh
```
- **idempotent**: 이미 설치된 도구는 자동 skip.
- 설치 도구: Docker, k3s (`--disable traefik --write-kubeconfig-mode 644`), kubectl, helm, k6.
- `sudo` 가 필요한 명령은 `[SUDO]` 표시와 함께 출력됨.

설치 직후 **Docker 그룹 권한**이 적용되지 않으면 다음 중 하나 실행:
```bash
newgrp docker        # 현재 셸에 즉시 반영
# 또는 로그아웃 후 재로그인
```

### 2. 검증
설치가 끝나면 스크립트가 마지막에 검증 명령을 안내한다. 한 번 더 정리:
```bash
docker --version
docker run --rm hello-world             # 정상 동작 확인
kubectl get nodes -o wide               # STATUS=Ready 인지 확인
kubectl top pods -A                     # metrics-server 동작 확인 (k3s 기본 제공)
helm version --short
k6 version
```
모두 통과해야 Phase 1 로 진행 가능.

### 3. 문제 해결
| 증상 | 확인 명령 |
|------|-----------|
| `kubectl` 가 cluster 에 못 붙음 | `ls -l /etc/rancher/k3s/k3s.yaml ~/.kube/config` |
| `kubectl top` 가 metrics 못 가져옴 | `kubectl -n kube-system get deploy metrics-server` |
| k3s 가 Ready 가 안 됨 | `sudo systemctl status k3s` / `sudo journalctl -u k3s -n 50 --no-pager` |
| `docker run` permission denied | `newgrp docker` 또는 재로그인 |

### 4. 재실험 / 정리
```bash
bash scripts/teardown.sh                    # 기본: 네임스페이스 + helm release 만 제거 (cluster/이미지 유지)
bash scripts/teardown.sh --all  --yes       # 위 + mock-llm:dev 이미지 + reports/ 제거
bash scripts/teardown.sh --nuke --yes       # 위 + k3s 자체 제거 (완전 초기화)
```
파괴적 모드(`--all`, `--nuke`)는 실수 방지를 위해 `--yes` 가 필수다. 기본 모드는 `--yes` 없이 그대로 실행 가능.

### 5. 향후 고려사항 (Phase 0 범위 외)

MVP 에서는 의도적으로 **버전 핀 고정 / 체크섬 검증을 적용하지 않는다.** 처음 환경을 구축하는 단계에서 핀이 오히려 호환성 함정이 되기 쉽기 때문. 다음 트리거 발생 시 재검토:

- **k3s 버전 핀**: 프로젝트가 특정 Kubernetes API 버전 / kube-state-metrics 호환 매트릭스에 의존하기 시작하면 `INSTALL_K3S_VERSION=vX.Y.Z+k3sN` 환경변수로 고정. (`scripts/install-infra.sh` 의 TODO 주석 참조.)
- **helm 버전 핀 + 체크섬**: 운영 환경 또는 CI 에 들어갈 때 적용. `get-helm-3` 의 `DESIRED_VERSION=vX.Y.Z` 환경변수 + 별도 sha256 검증. (`scripts/install-infra.sh` 의 TODO 주석 참조.)
- **kubectl 버전 핀**: `dl.k8s.io/release/stable.txt` 대신 명시적 버전 사용.
- **mock-llm 이미지 재현성**: `mock-llm/Dockerfile` 의 `python:3.12-slim` 을 digest (`@sha256:...`) 로 핀 + `requirements.txt` 를 `pip-compile` 로 락파일화. CI 도입 또는 운영 이미지 발행 시점에 적용. (`docker save | k3s ctr import` 워크플로우에서도 동일 빌드 결과를 보장하기 위해.)
- **kube-prometheus-stack 재현성**: chart 버전 핀 + values 의 image tag digest 핀. 이번 Phase 5 에서는 MVP 안정화를 우선하고, 운영/CI 도입 시점에 적용.

이 항목들은 MVP 동작이 안정화된 뒤(Phase 8 분석기 완성 후) 한 번에 처리하는 것이 효율적이다.

---

## Phase 2 — Docker 이미지 빌드 / 실행

### 1. 빌드
```bash
cd /home/azureuser/llm-ops-platform
docker build -t mock-llm:dev mock-llm/
```
- 멀티스테이지: `builder` 단계에서 `/opt/venv` 에 의존성을 설치하고, `runtime` 단계는 venv 만 복사 → pip / 빌드 도구 / 캐시가 최종 이미지에 들어가지 않음.
- 비루트 실행: UID/GID 1001 의 `mockllm` 유저로 동작.
- `mock-llm/.dockerignore` 가 `tests/`, `requirements-dev.txt`, `pytest.ini`, dev 캐시류, `*.md`, `.git/` 를 build context 에서 제외.

### 2. 로컬 실행 (k3s 배포 전 사전 검증)
```bash
docker run --rm -p 8000:8000 --name mock-llm-test mock-llm:dev
```
다른 터미널에서 Phase 1 의 검증 명령을 그대로 재사용:
```bash
curl -s -X POST localhost:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}],"max_tokens":50}' | jq
curl -s localhost:8000/metrics | grep mock_llm
curl -s localhost:8000/healthz
```
중지: `Ctrl-C` 또는 `docker stop mock-llm-test`.

### 3. 환경변수로 동작 조정
`MOCK_LLM_*` prefix 의 모든 설정은 `-e` 로 주입 가능. 예:
```bash
docker run --rm -p 8000:8000 \
  -e MOCK_LLM_MAX_CONCURRENCY=2 \
  -e MOCK_LLM_BASE_LATENCY_MS=500 \
  mock-llm:dev
```
설정 키와 기본값은 [mock-llm/app/config.py](../mock-llm/app/config.py) 참조.

### 4. 자주 마주치는 문제
| 증상 | 원인/조치 |
|------|-----------|
| `permission denied while trying to connect to the Docker daemon socket` | `newgrp docker` 또는 재로그인 (Phase 0 docker group 적용) |
| 빌드는 되는데 컨테이너가 즉시 종료 | `docker logs mock-llm-test` — uvicorn 이 import 에러를 출력했을 가능성 |
| 컨테이너 안에서 외부 접근 안 됨 | `--host 0.0.0.0` 이 CMD 에 들어가 있는지 확인 (Dockerfile 마지막 줄) |

---

## Phase 3 — k3s 배포

### 1. 이미지를 k3s 컨테이너 런타임에 import
`imagePullPolicy: Never` 이므로 cluster 가 이미지를 끌어올 수 없다. `docker save | k3s ctr import` 워크플로우로 직접 주입:
```bash
cd /home/azureuser/llm-ops-platform
docker build -t mock-llm:dev mock-llm/
docker save mock-llm:dev | sudo k3s ctr images import -
sudo k3s ctr images ls | grep mock-llm    # 검증: docker.io/library/mock-llm:dev 표시
```

### 2. 매니페스트 적용 (네임스페이스 → Deployment → Service 순)
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/mock-llm-deployment.yaml
kubectl apply -f k8s/mock-llm-service.yaml
# 또는 디렉토리 통째로
kubectl apply -f k8s/
```

### 3. 상태 확인
```bash
kubectl -n llm-ops get pods -w                       # READY 2/2 까지 대기
kubectl -n llm-ops get deploy,svc,endpoints
kubectl -n llm-ops describe pod -l app=mock-llm | head -50
kubectl -n llm-ops logs -l app=mock-llm --tail=50
```

Endpoints 가 비어 있으면 Service selector 와 Pod label 불일치 또는 Pod 가 Ready 가 아님.

### 4. 외부에서 접근 — 두 가지 경로

cluster 외부 (호스트 VM 셸) 에서 mock-llm 에 닿는 두 가지 방법이 있다. **용도가 다르므로 혼동하지 말 것:**

#### (A) `kubectl port-forward` — **단일 Pod 스모크 테스트 전용**
```bash
kubectl -n llm-ops port-forward svc/mock-llm 8000:8000 &
PF_PID=$!

curl -s -X POST localhost:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}],"max_tokens":50}' | jq
curl -s localhost:8000/metrics | grep mock_llm

kill $PF_PID
```
**주의**: `port-forward svc/...` 는 Service 의 endpoint 중 **하나의 Pod 으로만** 트래픽을 보낸다 (kube-proxy 의 round-robin 을 거치지 않음). 따라서 Phase 4 부하 시나리오를 이 경로로 돌리면 "2 replicas × 4 = 8 slots" 가정이 깨지고 단일 Pod 4 slots 만 부하받는다. **부하 테스트엔 절대 쓰지 말 것.**

#### (B) NodePort 30080 — **본격 부하 테스트 경로 (Phase 4 부터)**
[mock-llm-service.yaml](../k8s/mock-llm-service.yaml) 이 `type: NodePort, nodePort: 30080` 으로 설정돼 있다. kube-proxy 가 진짜 모든 Pod 에 round-robin 분산.
```bash
curl -s -X POST localhost:30080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}],"max_tokens":50}' | jq
curl -s localhost:30080/metrics | grep mock_llm
```
또는 다른 호스트에서: `curl -s -X POST <vm-ip>:30080/v1/chat/completions ...`

### 5. 자주 마주치는 문제
| 증상 | 원인/조치 |
|------|-----------|
| Pod `ImagePullBackOff` / `ErrImageNeverPull` | step 1 의 `k3s ctr images import` 누락 — 다시 실행 |
| Pod `CrashLoopBackOff` | `kubectl -n llm-ops logs <pod>` — uvicorn import / 환경변수 / 권한 (UID 1001) 점검 |
| Endpoints 비어 있음 | Pod label `app=mock-llm` 가 Service selector 와 일치하는지 확인 |
| `kubectl top pods` 가 메트릭 못 가져옴 | metrics-server (k3s 내장) 동작 확인: `kubectl -n kube-system get deploy metrics-server` |
| port-forward 가 즉시 끊김 | Pod 가 다시 시작됐을 가능성 — `get pods` 로 RESTARTS 컬럼 확인 |

### 6. 재배포
이미지를 다시 빌드한 경우, k3s 가 이미 import 된 동일 태그를 캐시하므로 강제 갱신 필요:
```bash
docker build -t mock-llm:dev mock-llm/
docker save mock-llm:dev | sudo k3s ctr images import -
kubectl -n llm-ops rollout restart deploy/mock-llm
kubectl -n llm-ops rollout status deploy/mock-llm
```

### 7. 정리
```bash
bash scripts/teardown.sh         # llm-ops 네임스페이스 + helm 릴리스 제거 (이미지/cluster 보존)
```

---

## Phase 4 — k6 부하 시나리오

Phase 3 의 cluster 가 살아있고 (`kubectl -n llm-ops get pods` → READY 2/2), Service 가 NodePort 30080 으로 노출돼 있다고 가정.

### 1. 시나리오 실행 (terminal A)
**반드시 NodePort (30080) 로 보낸다** — port-forward 는 단일 Pod 으로만 가서 시나리오의 "8 slots" 가정이 깨진다.
```bash
cd /home/azureuser/llm-ops-platform
export BASE_URL=http://localhost:30080

k6 run loadtests/short_prompt.js          # 기본 baseline (constant-vus 6)
k6 run loadtests/long_prompt.js           # 긴 프롬프트 + 큰 max_tokens

# burst_traffic 은 강도 두 단계 (env 변수로 선택):
k6 run loadtests/burst_traffic.js                              # normal: spike 80 RPS, maxVUs 1500
k6 run --env BURST_INTENSITY=high loadtests/burst_traffic.js   # high  : spike 200 RPS, maxVUs 7000
```
다른 VM 에서 같은 cluster 에 부하 보내고 싶으면 `BASE_URL=http://<vm-ip>:30080` 으로.

### 2. 부하 중 메트릭 관측 (terminal B)
```bash
watch -n 1 'curl -s localhost:30080/metrics \
  | grep -E "^mock_llm_(requests_total|requests_running|requests_waiting|errors_total)"'
```
Pod 단위 자원도 같이:
```bash
watch -n 2 'kubectl -n llm-ops top pods'
```

### 4. 시나리오별 기대 패턴
| 시나리오 | 부하 | 기대되는 메트릭 패턴 |
|----------|------|----------------------|
| `short_prompt` | 6 VUs × 2분, 짧은 prompt + maxTokens=64 | `requests_running` 약 5~6, `requests_waiting` 0~2, p95 < 2s, errors=0 |
| `long_prompt`  | 5 VUs × 2분, prompt 2000자 + maxTokens=512 | `requests_running` ≤ 5 (capacity 미만), `requests_waiting` ≈ 0, **p95 latency 만 높음** (~5s) — short_prompt 와 명확히 다른 시그니처 |
| `burst_traffic`| 10→200 RPS spike, 30s 유지 후 회복 | spike 동안 `requests_waiting` 급증, p95 폭증, `mock_llm_errors_total{reason="queue_timeout"}` 증가. 스파이크 끝나면 0으로 회복 |

이 세 패턴의 **차이** 가 Phase 8 분석기 룰의 입력이 된다 (queue bottleneck vs latency-only vs scale-out lag 구분).

### 5. k6 출력 읽는 법 (요약)
실행 끝나면 k6 가 한 페이지짜리 요약을 출력한다. 핵심 지표:
| 키 | 의미 |
|-----|------|
| `http_reqs` | 총 요청 수 / 평균 RPS |
| `http_req_duration ... p(95)=` | 95 퍼센타일 latency |
| `http_req_failed ... rate=` | 실패율 (status≥400 또는 timeout) |
| `checks ... pass/fail` | 우리가 정의한 응답 검증 (`status is 200`, `has choices[0].message`) |
| `iterations` | VU 의 default 함수가 실행된 횟수 |

threshold 가 깨지면 k6 가 **종료 코드 99** 로 종료한다 (`burst_traffic.js` 의 `http_req_failed: ['rate<0.30']` 은 의도적으로 30% 까지 허용).

### 6. 자주 마주치는 문제
| 증상 | 원인/조치 |
|------|-----------|
| `dial tcp 127.0.0.1:8000: connect: connection refused` | port-forward 미기동 — terminal A 확인 |
| 모든 요청이 503 `queue timeout` | 부하가 capacity 대비 너무 큼. `MOCK_LLM_QUEUE_TIMEOUT_S` 늘리거나 VU 줄이기 |
| `running` 이 capacity 8 을 초과 | Service 가 다른 Deployment 까지 매칭하는지 확인: `kubectl -n llm-ops get endpoints mock-llm` |

---

## Phase 5 — Prometheus 스크레이프

Phase 5 는 `kube-prometheus-stack` 으로 Prometheus, kube-state-metrics, node-exporter, Grafana 를 설치하고 mock-llm 의 `/metrics` 를 ServiceMonitor 로 스크레이프한다.

### 1. Helm repo 추가 및 설치
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install prom prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f k8s/prometheus-values.yaml
```

릴리스 이름은 `prom`, 네임스페이스는 `monitoring` 으로 고정한다. 이번 MVP 에서는 chart `--version` 을 붙이지 않는다. 버전 핀은 위 "향후 고려사항" 에서 다룬다.

### 2. Pod Ready 대기
```bash
kubectl -n monitoring get pods -w
```

Prometheus Operator 가 먼저 뜨고 CRD 가 등록된 뒤 Prometheus/Grafana Pod 가 Ready 상태가 된다. 모든 Pod 가 Ready 될 때까지 기다린다.

### 3. ServiceMonitor 적용
```bash
kubectl apply -f k8s/mock-llm-servicemonitor.yaml
kubectl -n llm-ops get servicemonitor
```

ServiceMonitor 는 `monitoring.coreos.com/v1` CRD 를 사용하므로 반드시 helm install 이후에 적용한다.

### 4. Prometheus UI 및 PromQL 검증
```bash
kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090 &
```

브라우저에서 `http://localhost:9090` 접속 후 Graph 에서 다음 쿼리를 확인한다.

```promql
sum(rate(mock_llm_requests_total[1m]))
histogram_quantile(0.95, sum(rate(mock_llm_request_duration_seconds_bucket[1m])) by (le))
mock_llm_requests_waiting
```

### 5. Targets 상태 확인

Prometheus UI 의 `Status > Targets` 에서 `serviceMonitor/llm-ops/mock-llm/0` 상태가 `UP` 인지 확인한다.

### 6. Grafana UI 확인
```bash
kubectl -n monitoring port-forward svc/prom-grafana 3000:80 &
```

브라우저에서 `http://localhost:3000` 접속 후 `admin` / `admin` 으로 로그인한다. 대시보드 임포트는 Phase 6 에서 진행한다.

### 7. 자주 마주치는 문제
| 증상 | 원인/조치 |
|------|-----------|
| ServiceMonitor 미픽업 | `prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false` 설정과 ServiceMonitor label (`release=prom`) 확인 |
| Targets DOWN | Pod readiness, Service endpoints (`kubectl -n llm-ops get endpoints mock-llm`), NetworkPolicy 확인 |
| PromQL 결과 비어있음 | `/metrics` 자체에 카운터가 있는지 `curl -s localhost:30080/metrics | grep mock_llm` 로 직접 확인 |

---

## Phase 6 — Grafana 대시보드

Phase 5 의 Grafana 사이드카가 `grafana_dashboard: "1"` label 이 붙은 ConfigMap 을 자동 임포트한다.

```bash
kubectl apply -f k8s/grafana-dashboard-llm-overview.yaml
sleep 30
kubectl -n monitoring port-forward svc/prom-grafana 3000:80 &
```

브라우저에서 `http://localhost:3000/d/llm-overview` 로 접속한다. 초기 로그인은 개발용 기본값 `admin` / `admin` 이다.

---

## Phase 7 — HPA + 실험 wrapper

CPU 기반 HPA 는 queue 병목을 일부러 놓치는 케이스를 관찰하기 위한 설정이다. queue 기반 autoscaling 이 아니라는 점이 분석기 Rule #4 의 핵심 입력이다.

```bash
kubectl apply -f k8s/mock-llm-hpa.yaml
kubectl -n llm-ops get hpa -w
```

실험은 NodePort 30080 과 Prometheus port-forward 가 떠 있다고 가정한다.

```bash
kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090 &
bash scripts/run-experiment.sh short_prompt
bash scripts/run-experiment.sh long_prompt
bash scripts/run-experiment.sh burst_traffic
bash scripts/run-experiment.sh burst_traffic --high
```

`reports/<scenario>-<timestamp>/run.json` 에 시간 범위와 `k6_exit_code` 가 기록된다. k6 threshold 실패로 exit code 가 0 이 아니어도 `k6_summary.json` 이 생성됐다면 분석 가능한 실험 결과로 취급한다.

---

## Phase 8 — 분석기 + 리포트

분석기는 Kubernetes API 를 직접 보지 않고 Prometheus HTTP API 만 사용한다. PromQL 은 `analyzer/config/metrics.yaml` 에만 정의하고, Rule 은 논리 메트릭 이름만 사용한다.

```bash
python3 -m venv analyzer/.venv
analyzer/.venv/bin/pip install -r analyzer/requirements-dev.txt
analyzer/.venv/bin/python -m analyzer.main --run reports/burst_traffic-<timestamp>
cat reports/burst_traffic-<timestamp>/report.md
```

검증:

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
```

`--strict` 옵션을 켜면 Prometheus 연결/HTTP 실패 시 빈 리포트가 아니라 **exit 2** 로 실패한다. 디버깅 / CI 용:

```bash
analyzer/.venv/bin/python -m analyzer.main --run reports/burst_traffic-<ts> --strict
```

### Fixture 캡처 + replay (회귀 방지, 한 번만 캡처하면 됨)

Prometheus 가 실제로 반환한 raw 응답을 `analyzer/tests/fixtures/<name>/` 에 저장해두면, `pytest` 가 그 fixture 를 자동으로 픽업해 **schema 드리프트 / PromQL 오타 / 룰 evaluate 크래시** 를 회귀로 잡는다.

```bash
# 실험 한 번 실행 + 그 시간 범위의 모든 PromQL 응답을 캡처
bash scripts/run-experiment.sh burst_traffic
analyzer/.venv/bin/python -m analyzer.tools.capture_fixtures \
  --run reports/burst_traffic-<timestamp> \
  --output analyzer/tests/fixtures/burst_baseline

# 그 다음 pytest 가 burst_baseline 픽업
analyzer/.venv/bin/pytest analyzer/tests -v
# → test_fixture_replay_end_to_end[burst_baseline] 가 추가로 실행됨
```

캡처 결과 (`responses.json` + `_meta.json`) 는 git 에 커밋해두면 다른 환경에서도 같은 회귀 테스트가 동작한다.

---

## Phase 9 — 문서

시스템 구조, 메트릭 카탈로그, 실험 해석 기준은 아래 문서를 기준으로 본다.

- [architecture.md](architecture.md)
- [metrics.md](metrics.md)
- [experiment-plan.md](experiment-plan.md)

---

## 완료 체크리스트

- [x] Phase 1: Mock LLM 로컬 실행 (uvicorn + curl + pytest)
- [x] Phase 2: Docker 이미지 빌드 / 실행
- [x] Phase 3: k3s 배포 (`kubectl apply -f k8s/`)
- [x] Phase 4: k6 부하 시나리오 실행
- [x] Phase 5: Prometheus 스크레이프 (kube-prometheus-stack)
- [x] Phase 7: HPA + 실험 wrapper
- [x] Phase 8: 분석기 + 리포트
- [x] Phase 6: Grafana 대시보드
- [x] Phase 9: 문서 보강
- [x] Phase 10: KEDA queue autoscaling manifest / mode switch scripts
- [x] Phase 11: 비교 리포트 CLI
- [x] Phase 12: 비용 추정 profile
- [x] Phase 13: GPU 이관 체크리스트

MVP 는 `bash scripts/run-experiment.sh burst_traffic` 으로 end-to-end 재현 가능하다. 확장 검증은 Phase 10 이후 절차에 따라 CPU HPA baseline 과 KEDA run 을 각각 생성한 뒤 비교한다.

---

## Phase 10 — KEDA queue autoscaling

CPU 기반 HPA baseline과 queue 기반 KEDA autoscaling을 분리해서 실험한다. 두 autoscaler가 동시에 `mock-llm` Deployment를 제어하면 안 된다.

### 1. KEDA 설치

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda -n keda --create-namespace
kubectl -n keda rollout status deploy/keda-operator
```

### 2. CPU HPA baseline 모드

```bash
bash scripts/use-cpu-hpa.sh
bash scripts/run-experiment.sh burst_traffic
CPU_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$CPU_RUN" --cost-profile custom
```

### 3. KEDA queue autoscaling 모드

```bash
bash scripts/use-keda-queue.sh
bash scripts/run-experiment.sh burst_traffic
KEDA_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$KEDA_RUN" --cost-profile custom
```

KEDA ScaledObject는 다음 metric을 사용한다.

```promql
sum(mock_llm_requests_waiting)
```

기본 threshold는 `20`, replica 범위는 `2~8`이다.

---

## Phase 11 — 비교 리포트

두 실험 결과를 비교한다.

```bash
analyzer/.venv/bin/python -m analyzer.compare \
  --before "$CPU_RUN" \
  --after "$KEDA_RUN"
cat "$KEDA_RUN/comparison.md"
```

주요 확인 항목:

- `max waiting` 감소
- `p95/p99 latency peak` 감소
- `error rate peak` 감소
- `desired/ready replicas max` 증가
- `hpa_limitation` 제거 또는 완화

---

## Phase 12 — 비용 추정

비용 분석은 `analyzer/config/cost.yaml`의 수동 단가 profile을 사용한다. 클라우드 과금 API는 호출하지 않는다.

```bash
analyzer/.venv/bin/python -m analyzer.main --run "$RUN_DIR" --cost-profile custom
```

출력 항목:

- estimated run cost
- cost per 1K requests
- cost per 1K tokens
- avg billable replicas

---

## Phase 13 — GPU 이관 준비

GPU 서버가 준비되면 [gpu-migration.md](gpu-migration.md)를 기준으로 진행한다. CPU/mock 환경에서 먼저 완료할 검증:

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
mock-llm/.venv/bin/pytest mock-llm/tests -v
python3 -m compileall analyzer mock-llm/app
```
