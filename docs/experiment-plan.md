# Experiment Plan

이 문서는 k6 시나리오별 가설, 실행 명령, 기대 메트릭 패턴, 분석 리포트 해석 기준을 정리한다. 모든 실험은 Phase 7의 wrapper를 사용해 `reports/<scenario>-<timestamp>/` 아래에 `run.json`, `k6.log`, `k6_summary.json`, 분석 결과를 누적한다.

## short_prompt

### 가설

안정 baseline이다. 처리 capacity 안에서 요청이 흐르므로 진단 룰 trigger가 없어야 한다.

### 실행

```bash
bash scripts/run-experiment.sh short_prompt
```

### 기대 메트릭

| 항목 | 기대값 |
|---|---|
| running | 5~7 |
| waiting | 0~2 |
| p95 latency | < 2s |
| errors | 0 |

### 기대 분석 리포트

`diagnosis` 섹션에 "no rules triggered" 또는 informative 수준 결과만 나타난다. 이 시나리오는 다른 실험 결과를 비교하기 위한 정상 기준선이다.

## long_prompt

### 가설

legacy combined long 시나리오다. 긴 입력과 긴 출력을 함께 사용해 요청 자체가 오래 걸리는지 확인한다. 신규 실험에서는 prefill/TTFT와 decode/TPOT를 분리하기 위해 `long_input`, `long_output`을 우선 사용한다.

### 실행

```bash
bash scripts/run-experiment.sh long_prompt
```

### 기대 메트릭

| 항목 | 기대값 |
|---|---|
| running | 5 이하 |
| waiting | 거의 0 |
| p95 latency | 약 5s |
| errors | 0 |

### 기대 분석 리포트

`cpu_bottleneck`과 `queue_bottleneck` 모두 미trigger가 기대된다. 현재 룰 셋에서는 단순 latency-only 상황을 "정상 동작"으로 분류한다.

## long_input

### 가설

긴 context가 들어오면 prefill 부담이 커져 TTFT와 prompt tokens/request가 증가한다. 출력 길이는 중간으로 제한해 decode 병목과 분리한다.

### 실행

```bash
bash scripts/run-experiment.sh long_input
```

### 기대 메트릭

| 항목 | 기대값 |
|---|---|
| prompt tokens/request p95 | 4000 이상 |
| TTFT p95 | short_prompt 대비 증가 |
| queue wait p95 | capacity 안에서는 낮음 |
| output tokens/request p95 | long_output보다 낮음 |

## long_output

### 가설

입력은 짧거나 중간이지만 긴 출력을 생성하므로 TPOT, output token throughput, E2E latency가 중요해진다.

### 실행

```bash
bash scripts/run-experiment.sh long_output
```

### 기대 메트릭

| 항목 | 기대값 |
|---|---|
| output tokens/request p95 | 1000 근처 |
| inter-token latency p95 | 안정적으로 유지되어야 함 |
| output token throughput | short_prompt 대비 증가 |
| p95/p99 latency | short_prompt 대비 증가 |

## rag_like

### 가설

짧은 사용자 질문에 긴 검색 context가 붙는 RAG형 요청이다. 사용자 입력은 짧지만 실제 prompt token 수는 길어져 `long_input`과 유사한 TTFT 패턴을 보일 수 있다.

### 실행

```bash
bash scripts/run-experiment.sh rag_like
```

### 기대 메트릭

| 항목 | 기대값 |
|---|---|
| prompt tokens/request p95 | 4000 이상 |
| TTFT p95 | short_prompt 대비 증가 |
| p95/p99 latency | context 길이에 따라 증가 |
| queue wait p95 | 동시성이 capacity 안이면 낮음 |

## burst_traffic (normal)

### 가설

spike 동안 queue가 폭증한다. CPU 기반 HPA는 queue 길이를 직접 보지 않으므로 Rule #4인 `hpa_limitation`도 함께 관찰될 수 있다.

### 실행

```bash
bash scripts/run-experiment.sh burst_traffic
```

### 기대 메트릭

| 항목 | 기대값 |
|---|---|
| waiting | 5 초과 |
| p95 latency | 2s 초과 |
| errors | spike 구간에서 `queue_timeout` 증가 가능 |
| replicas_desired | CPU 반응에 따라 유지 또는 증가 |

### 기대 분석 리포트

`queue_bottleneck` triggered가 기대된다. CPU 평균이 낮고 desired replicas 변화가 없으면 `hpa_limitation`도 triggered 가능하다.

## burst_traffic (high)

### 가설

normal보다 강한 spike로 queue 병목이 더 뚜렷해지고, HPA가 scale-out을 시도할 경우 신규 Pod 준비 지연도 관찰될 수 있다.

### 실행

```bash
bash scripts/run-experiment.sh burst_traffic --high
```

### 기대 메트릭

| 항목 | 기대값 |
|---|---|
| waiting | 크게 증가 |
| p95 latency | 2s 초과 |
| errors | `queue_timeout` 증가 가능 |
| replicas_desired / ready | desired가 ready보다 먼저 증가할 수 있음 |

### 기대 분석 리포트

`queue_bottleneck` triggered가 기대된다. HPA가 scale-out을 시도하고 Pod readiness가 늦으면 `scale_out_lag`도 동시에 triggered 가능하다.

## 확장 실험 아이디어

- 다양한 `MOCK_LLM_MAX_CONCURRENCY` 값으로 queue 발생 지점을 비교한다.
- 다양한 HPA CPU target 값으로 CPU 기반 autoscaling의 반응성을 비교한다.
- 다양한 queue timeout 값으로 실패율과 p95 latency의 trade-off를 비교한다.
- 결과를 `reports/`에 누적한 뒤 향후 diff 도구로 scenario별 리포트를 비교한다.

## Pre-GPU 실험 매트릭스

GPU/vLLM으로 넘어가기 전에는 mock 환경에서 다음 실험을 최종 기준으로 본다.

| 구분 | 시나리오 | 목적 |
|---|---|---|
| baseline | `short_prompt` | 짧은 요청의 정상 latency/queue 기준선 |
| context-heavy | `long_input` | 긴 입력이 TTFT/prefill에 주는 영향 |
| decode-heavy | `long_output` | 긴 출력이 TPOT/output throughput에 주는 영향 |
| RAG형 | `rag_like` | 짧은 질문 + 긴 context 서비스 패턴 |
| production-like | `mixed_workload` | 요청 유형이 섞일 때 p95/p99 long-tail |
| spike | `burst_traffic` | 순간 queue bottleneck과 recovery |
| autoscaling | `sustained_ramp` | CPU HPA와 KEDA의 scale-out 차이 |

공통 확인 지표:

```text
k6 p50/p95/p99 latency
requests_waiting max
queue wait p95
TTFT p95
TPOT p95
prompt/output tokens per request p95
prompt/output token throughput
error rate
desired/ready replica 변화
```


## CPU HPA vs KEDA queue autoscaling

### 가설

CPU HPA baseline에서는 queue 병목이 커지고 `hpa_limitation`이 trigger된다. KEDA queue autoscaling에서는 `mock_llm_requests_waiting` 기반으로 replica가 증가해 waiting과 latency가 감소한다.

### 실행

```bash
bash scripts/use-cpu-hpa.sh
bash scripts/run-experiment.sh burst_traffic
CPU_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$CPU_RUN" --cost-profile custom

bash scripts/use-keda-queue.sh
bash scripts/run-experiment.sh burst_traffic
KEDA_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$KEDA_RUN" --cost-profile custom

analyzer/.venv/bin/python -m analyzer.compare --before "$CPU_RUN" --after "$KEDA_RUN"
```

### 기대 비교 리포트

| 항목 | 기대 변화 |
|---|---|
| max waiting | 감소 |
| p95/p99 latency peak | 감소 |
| desired/ready replicas max | 증가 |
| hpa_limitation | removed 또는 완화 |
| cost per 1K requests | replica 증가로 상승 가능 |

비용은 성능 개선과 함께 해석한다. replica 증가로 run cost가 늘어도, latency와 failure가 줄면 운영상 더 나은 선택일 수 있다.


## SLO 기반 closed-loop (sustained_ramp / mixed_workload)

### 가설

점진 부하(`sustained_ramp`)에서 CPU HPA는 큐를 직접 보지 못해 SLO(p95/p99/error)를 위반하고, KEDA queue는 `mock_llm_requests_waiting` 기반으로 조기 스케일아웃해 SLO 위반이 감소한다. 분석기의 추천 엔진(`## 9. 권장 설정`)이 제시한 설정을 적용해 재실험하면 위반이 더 줄어든다(closed-loop 검증).

### 실행: CPU baseline → KEDA → 추천 적용 재실험

```bash
# 0) (최초 1회) KEDA 설치
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda -n keda --create-namespace
kubectl -n keda rollout status deploy/keda-operator

# 1) CPU HPA baseline (점진 ramp)
bash scripts/use-cpu-hpa.sh
bash scripts/run-experiment.sh sustained_ramp
CPU_RUN=$(ls -dt reports/sustained_ramp-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$CPU_RUN" --slo-profile default --cost-profile custom

# 2) KEDA queue autoscaling (동일 부하)
bash scripts/use-keda-queue.sh
bash scripts/run-experiment.sh sustained_ramp
KEDA_RUN=$(ls -dt reports/sustained_ramp-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$KEDA_RUN" --slo-profile default --cost-profile custom

# 3) before/after 비교
analyzer/.venv/bin/python -m analyzer.compare --before "$CPU_RUN" --after "$KEDA_RUN"
cat "$KEDA_RUN/comparison.md"

# 4) (closed-loop) KEDA run 의 "## 9. 권장 설정" 추천값을 매니페스트에 반영 후 재실험
#    예: keda threshold / replicas_max / container requests / MOCK_LLM_MAX_CONCURRENCY
#    적용 후 recommend.yaml 의 current 도 동기화하고 다시 run → compare.
```

### 해석 기준

| 출력 | 볼 것 |
|---|---|
| `report.md` `## 7. SLO 판정` | CPU run 은 위반(BREACH), KEDA run 은 충족 또는 위반 항목 감소 |
| `report.md` `## 9. 권장 설정` | KEDA threshold/replica/리소스/concurrency 정량 추천 |
| `comparison.md` | max waiting↓, p95/p99 peak↓, desired/ready replicas max↑, hpa_limitation removed |
| `mixed_workload` | 멀티모달에서 p95/p99 long tail, TTFT/TPOT 가 shape 별로 변동 |
