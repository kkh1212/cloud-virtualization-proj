# HANDOFF — 작업 이어받기 노트

> 다른 환경(로컬 머신 등)의 Claude Code 가 이 repo 를 clone 한 뒤 작업을 그대로 이어받기 위한 핸드오프 문서.
> **세션 시작 시 CLAUDE.md → 이 문서 순서로 읽으면 맥락이 복원된다.**
>
> - 마지막 갱신: 2026-05-17 (Azure dev VM → 로컬 이전 시점)
> - 직전 작업 환경: Azure VM (Ubuntu, k3s 단일 노드, GPU 없음)
> - GitHub: `kkh1212/cloud-virtualization-proj` (branch `main`)

---

## 1. 프로젝트 한 줄

Kubernetes(k3s) 기반 **LLM 서비스 운영 진단 플랫폼** MVP.
파이프라인: `k6 부하 → mock-llm(FastAPI) → Prometheus → rule 기반 analyzer → Markdown/JSON 리포트`.
GPU 없는 환경에서 mock 으로 전체 흐름을 완성하고, 추후 vLLM + DCGM exporter 로 교체 시 `analyzer/config/metrics.yaml` 의 GPU 행 주석만 풀면 GPU 룰 3종이 자동 활성화되도록 설계됨.

전체 설계 의도는 [docs/architecture.md](docs/architecture.md), 운영 절차는 [docs/runbook.md](docs/runbook.md).

---

## 2. 현재 상태: MVP + 마무리 작업 완료, end-to-end 실측 검증됨

### 완료된 Phase (0~9)
| Phase | 산출물 |
|-------|--------|
| 0 | `scripts/install-infra.sh`, `teardown.sh`, `.gitignore` (Docker/k3s/kubectl/helm/k6 idempotent 설치, Ubuntu 전용) |
| 1 | `mock-llm/app/` (FastAPI, asyncio.Semaphore 큐 시뮬, prometheus_client 메트릭) + pytest |
| 2 | `mock-llm/Dockerfile` (멀티스테이지, 비루트 1001) + `.dockerignore` |
| 3 | `k8s/namespace.yaml`, `mock-llm-deployment.yaml`, `mock-llm-service.yaml` (NodePort 30080) |
| 4 | `loadtests/` short_prompt / long_prompt / burst_traffic (+ `BURST_INTENSITY` opt-in) |
| 5 | `k8s/prometheus-values.yaml`, `mock-llm-servicemonitor.yaml` (kube-prometheus-stack) |
| 7 | `k8s/mock-llm-hpa.yaml` (CPU 60%, 의도적 — Rule #4 시연), `scripts/run-experiment.sh` |
| 8 | `analyzer/` 전체 (collector / schemas / rules×7 / report / main) |
| 6 | `k8s/grafana-dashboard-llm-overview.yaml` (6패널) |
| 9 | `docs/architecture.md`, `metrics.md`, `experiment-plan.md` |

### 마무리 작업 (advisor 지적 반영, 모두 완료)
- **B1**: report.py 평균 latency 에 `s` 단위
- **B2**: main.py `estimated_total_requests` 정수화
- **L2**: `hpa_limitation` 에 `duration_min_seconds=30` (transient spike false-positive 차단)
- **L3**: prometheus-values.yaml `storageSpec` 모호함 제거 + 의도 주석
- **L4**: analyzer `--strict` (Prometheus 연결/HTTP 실패 시 exit 2). 빈 결과는 raise 안 함(룰 게이팅 메커니즘)
- **L5**: `analyzer/tools/capture_fixtures.py` + `tests/test_fixture_integration.py` (실측 응답 replay 회귀 테스트)
- **README.md** 추가

### 실측 검증 (2026-05-17, burst_traffic-20260517T110305Z)
- triggered: **queue_bottleneck + hpa_limitation** (적용 4룰 / GPU 3룰 자동 스킵)
- max_waiting 1340, p95 30s(=queue timeout), CPU 0.03x(mock 특성), replicas 2→2(HPA 미발동)
- 3회 실행 모두 ±5% 재현성
- `pytest analyzer/tests` → **15 passed, 1 skipped**(fixture 미캡처)

---

## 3. ⚠️ 알려진 이슈 — 로컬에서 가장 먼저 할 일

**`analyzer/tests/test_fixture_integration.py:130-131` 의 섹션 번호가 report.py 와 불일치.**
- 테스트는 `## 7. 진단`, `## 8. 개선 방향` 을 단언
- 실제 `analyzer/report.py` 출력은 `## 6. 진단`, `## 7. 개선 방향` (정답 — 실측 report.md 가 6/7)
- 지금은 fixture 가 없어 replay 테스트가 **skip** 되어 안 드러나지만, **fixture 를 캡처하는 순간 `test_fixture_replay_end_to_end` 가 이 assert 에서 실패**한다.
- **수정 방법**: test 의 `"## 7. 진단"` → `"## 6. 진단"`, `"## 8. 개선 방향"` → `"## 7. 개선 방향"` 으로 되돌리거나, 의도적으로 섹션 번호를 바꾸려면 report.py 의 헤더도 7/8 로 함께 변경.

---

## 4. 남은 작업

### (a) 사용자 측 1회 작업 — cluster 필요
L5 fixture 캡처는 도구/테스트는 다 들어갔으나 **실제 fixture 한 번 캡처**만 남음 (위 §3 이슈 먼저 해결 후):
```bash
analyzer/.venv/bin/python -m analyzer.tools.capture_fixtures \
  --run reports/burst_traffic-<ts> --output analyzer/tests/fixtures/burst_baseline
analyzer/.venv/bin/pytest analyzer/tests -v   # replay 테스트 PASSED 확인 후 git add
```

### (b) 계획상 확장 — MVP 범위 외 (의도된 deferred)
| 항목 | 트리거 | 작업 |
|------|--------|------|
| GPU 진단 활성화 | GPU VM + vLLM + DCGM exporter | `analyzer/config/metrics.yaml` 의 GPU 3행 주석 해제 → `_gpu_*` 룰 자동 활성화 (코드 수정 0) |
| cost 메트릭 | 클라우드 빌링 API | workload/idle/per-request/per-1k-token. plan D4 보류 |
| 시나리오 자동 비교 | 8번째 질문("설정 A vs B") | 두 report.json diff 도구 |
| 버전 핀/체크섬 | CI 도입 시 | docs/runbook.md "향후 고려사항" 참조 |

---

## 5. 로컬에서 이어가는 방법

### 코드 작업 (OS 무관 — 분석기/룰/테스트)
```bash
git clone https://github.com/kkh1212/cloud-virtualization-proj.git
cd cloud-virtualization-proj
python3 -m venv analyzer/.venv
analyzer/.venv/bin/pip install -r analyzer/requirements.txt
analyzer/.venv/bin/pip install pytest
analyzer/.venv/bin/pytest analyzer/tests -v     # 15 passed, 1 skipped 나오면 정상
```
mock-llm 도 동일하게 `mock-llm/` 에서 venv + pytest 가능.

### cluster 통합 (환경 의존)
- **로컬이 Ubuntu** → `bash scripts/install-infra.sh` 그대로 사용 가능
- **로컬이 macOS / Windows** → `install-infra.sh` 는 **Ubuntu 전용으로 막혀 있음** (preflight 에서 `ID != ubuntu` 면 exit 1). 대안:
  - Docker Desktop + `kind` 또는 `minikube` 로 단일 노드 cluster
  - 이미지 import 워크플로(`docker save | k3s ctr import`)는 kind 면 `kind load docker-image` 로 대체
  - 또는 원격 k3s(이 Azure VM 등)에 `kubectl` 컨텍스트만 연결해서 사용
- cluster 자체는 이 repo 에 따라오지 않는다 — 로컬에서 새로 구축 필요.

### 한 사이클 빠른 재현 (cluster 준비 후)
[docs/runbook.md](docs/runbook.md) 의 Phase 3→5→7→8 순서. 요약:
```bash
docker build -t mock-llm:dev mock-llm/ && docker save mock-llm:dev | sudo k3s ctr images import -
kubectl apply -f k8s/
helm install prom prometheus-community/kube-prometheus-stack -n monitoring --create-namespace -f k8s/prometheus-values.yaml
kubectl apply -f k8s/mock-llm-servicemonitor.yaml -f k8s/mock-llm-hpa.yaml -f k8s/grafana-dashboard-llm-overview.yaml
# 별도 터미널: kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090
bash scripts/run-experiment.sh burst_traffic
analyzer/.venv/bin/python -m analyzer.main --run reports/burst_traffic-<ts>
```

---

## 6. 작업 규칙 (로컬 Claude 가 따를 것)

- **[CLAUDE.md](CLAUDE.md)** — implementer 역할/프로젝트 룰/금지사항 (phase-by-phase, MVP 우선, prometheus_client, pydantic, PromQL 은 metrics.yaml 에만, 리포트는 md+json 동시).
- **[AGENTS.md](AGENTS.md)** — Codex advisory reviewer 룰 (구현 X, 리뷰만).
- **버전 핀/체크섬은 의도적으로 안 함** (MVP). docs/runbook.md "향후 고려사항" 에 트리거 정리.
- **Codex review**: `bash scripts/codex-review.sh --raw --timeout 90 -- "..."` (codex CLI + 로컬 환경에서 bubblewrap sandbox 필요. 없으면 SKIPPED 로 graceful, blocking 아님).
- 메트릭 이름 contract(`mock_llm_*`) 와 logical 이름(metrics.yaml)을 임의로 바꾸지 말 것 — 분석기·대시보드·테스트가 모두 의존.

---

## 7. 핵심 파일 지도

| 알고 싶은 것 | 파일 |
|-------------|------|
| mock LLM 큐/지연 동작 | [mock-llm/app/simulator.py](mock-llm/app/simulator.py) |
| 노출 메트릭 정의 | [mock-llm/app/metrics.py](mock-llm/app/metrics.py) |
| **메트릭 이름 → PromQL 매핑(확장 핵심)** | [analyzer/config/metrics.yaml](analyzer/config/metrics.yaml) |
| **룰 자체 게이팅(GPU 확장 핵심)** | [analyzer/rules/base.py](analyzer/rules/base.py) |
| 룰 임계값 | [analyzer/config/rules.yaml](analyzer/config/rules.yaml) |
| 리포트 렌더링(섹션 번호) | [analyzer/report.py](analyzer/report.py) |
| 분석기 CLI | [analyzer/main.py](analyzer/main.py) |
| 실험 wrapper | [scripts/run-experiment.sh](scripts/run-experiment.sh) |
| K8s 매니페스트 | [k8s/](k8s/) |
| 부하 시나리오 | [loadtests/](loadtests/) |
