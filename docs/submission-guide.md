# 프로젝트 제출 및 실행 가이드

이 문서는 프로젝트 코드를 제출하거나, GPU 서버에서 다시 실행할 때 필요한 내용을 정리한 문서이다.  
README는 프로젝트 진행 기록이 많이 포함되어 있으므로, 최종 제출 시에는 이 문서를 함께 참고하면 된다.

## 1. 프로젝트 요약

본 프로젝트는 Kubernetes 기반 LLM 서비스 운영 진단 플랫폼이다.

일반적인 API 부하테스트처럼 RPS, 평균 응답시간, CPU 사용률만 보는 것이 아니라, LLM 서비스 특성을 반영하여 입력 token 수, 출력 token 수, TTFT, TPOT, queue wait, GPU memory, KV cache, Kubernetes 상태를 함께 분석한다.

전체 실행 흐름은 다음과 같다.

```text
k6 부하테스트
-> vLLM 또는 mock LLM 서버
-> Kubernetes Deployment / Service
-> Prometheus metric 수집
-> analyzer 병목 분석
-> Markdown / JSON report 생성
-> 설정 변경 후 재부하테스트
-> before / after 비교 report 생성
```

현재 검증 중심 파이프라인은 Pipeline 1이다.

```text
이미 실행 중인 LLM endpoint 준비
-> 사용자가 서비스 워크로드 선택
-> 워크로드별 부하테스트 실행
-> metric 수집 및 report 생성
-> 병목 확인
-> 사람이 설정 변경
-> 동일 부하로 재테스트
-> 개선 전후 비교
```

Pipeline 2는 일부 구현되어 있다. 워크로드 선택에 따라 vLLM/Kubernetes manifest를 생성하는 구조는 있으나, 운영 환경에 맞는 설정을 완전히 자동으로 선택하고 적용하는 단계는 아직 후속 과제로 둔다.

## 2. 프로젝트 디렉토리 구조

```text
cloud-virtualization-proj/
├── analyzer/
├── loadtests/
├── k8s/
├── mock-llm/
├── scripts/
├── docs/
├── README.md
├── HANDOFF.md
├── AGENTS.md
└── CLAUDE.md
```

각 디렉토리의 역할은 다음과 같다.

`analyzer/`는 분석 엔진이다. Prometheus에서 metric을 조회하고, workload별 기준과 rule을 적용해 병목을 판단한다. `session.py`는 워크로드 단위 리포트를 생성하고, `session_compare.py`는 설정 변경 전후 리포트를 비교한다.

`analyzer/config/`에는 metric mapping, workload profile, SLO, rule, recommendation 설정이 들어 있다. 특히 `workload-profiles.yaml`에는 4가지 서비스 워크로드의 부하 단계, 주요 지표, threshold, 추천 방향이 정의되어 있다.

`loadtests/`에는 k6 부하테스트 시나리오가 들어 있다. `rag_like.js`, `long_input.js`, `long_output.js`, `json_extraction.js` 등이 워크로드별 부하 생성에 사용된다.

`k8s/`에는 Kubernetes manifest가 들어 있다. mock LLM 배포, vLLM GPU 배포, ServiceMonitor, KEDA, Grafana dashboard 관련 yaml이 포함되어 있다.

`mock-llm/`은 GPU 없이도 실험할 수 있는 mock LLM 서버이다. 실제 LLM은 아니지만 latency, queue, token, metric을 흉내 내어 초기 파이프라인 검증에 사용한다.

`scripts/`는 실행 자동화 스크립트 모음이다. GPU stack 설치, vLLM 배포, workload 실행, metric 검증, smoke test 등을 수행한다.

`docs/`는 아키텍처, metric, 실험 계획, GPU 전환, workload 설명, 실행 가이드 문서를 저장한다.

## 3. 대표 서비스 워크로드

본 프로젝트는 LLM 서비스 유형을 4가지 워크로드로 나누어 테스트한다.

### support_chat

고객지원 RAG 챗봇을 가정한다. 사용자의 짧은 질문과 검색된 문서 context를 함께 입력하고 답변을 생성한다.

주요 부하는 동시 사용자 수 증가이다.

```text
8 VUs -> 32 VUs -> 96 VUs
```

주요 지표는 p95 latency, TTFT, queue wait이다. 실시간 고객 응대 서비스이므로 사용자가 체감하는 응답 지연이 중요하다.

### doc_summary

문서, 회의록, 메신저 대화 요약 서비스를 가정한다. 긴 문서를 입력으로 받아 요약 결과를 생성한다.

주요 부하는 입력 token 길이 증가이다.

```text
2k input tokens -> 4k input tokens -> 8k input tokens
```

주요 지표는 TTFT, prompt token 처리량, GPU memory, KV cache이다. 긴 입력은 prefill 단계와 GPU memory 사용량에 영향을 준다.

### code_assistant

코딩 보조 서비스를 가정한다. 코드 context를 입력으로 받아 코드 생성, 수정, 설명을 수행한다.

주요 부하는 출력 token 길이 증가이다.

```text
256 output tokens -> 768 output tokens -> 1536 output tokens
```

주요 지표는 TPOT, output token rate, p99 latency이다. 긴 코드 생성에서는 decode 단계와 output token 생성 속도가 중요하다.

### json_extraction

구조화 추출 및 분류 서비스를 가정한다. 짧은 입력에서 필요한 정보를 JSON 형태로 추출하거나 유형을 분류한다.

주요 부하는 RPS 증가이다.

```text
25 RPS -> 100 RPS -> 200 RPS
```

주요 지표는 throughput, queue wait, p95/p99 latency이다. 요청 하나는 짧지만 높은 요청 수를 안정적으로 처리해야 한다.

## 4. GPU 서버 실행 방법

아래 명령은 NVIDIA GPU 서버 기준이다. 실제 실험은 RTX A6000 단일 GPU 환경에서 검증하였다.

### 4.1 기본 도구 확인

```bash
cd ~/cloud-virtualization-proj
git pull origin main

nvidia-smi
docker --version
kubectl version --client
helm version
k6 version
```

### 4.2 인프라 설치

처음 만든 VM이라면 다음을 실행한다.

```bash
bash scripts/install-infra.sh
```

이미 Docker, k3s, kubectl, helm, k6가 설치되어 있으면 생략할 수 있다.

### 4.3 GPU stack 설치

```bash
bash scripts/install-gpu-stack.sh --vendor nvidia
```

설치 후 GPU가 Kubernetes resource로 보이는지 확인한다.

```bash
kubectl describe node | grep -A10 -E "Capacity|Allocatable|nvidia.com/gpu"
kubectl get pods -A | grep -E "nvidia|dcgm"
```

정상이라면 node에 `nvidia.com/gpu: 1`이 보여야 한다.

### 4.4 vLLM 배포

기본 모델은 다음을 사용한다.

```text
Qwen/Qwen2.5-0.5B-Instruct
```

기본 배포 명령은 다음과 같다.

```bash
bash scripts/deploy-vllm-gpu.sh \
  --vendor nvidia \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --served-model-name mock \
  --max-model-len 4096 \
  --max-num-seqs 256 \
  --gpu-memory-utilization 0.85
```

배포 확인:

```bash
kubectl -n llm-ops get pods -l app=vllm -o wide
curl -fsS http://localhost:30081/health
```

간단한 vLLM smoke test:

```bash
bash scripts/smoke-vllm.sh --model mock
```

### 4.5 Prometheus port-forward

부하테스트와 analyzer 실행 중에는 Prometheus가 `localhost:9090`으로 열려 있어야 한다.

별도 터미널에서 실행한다.

```bash
cd ~/cloud-virtualization-proj

PIDS=$(ss -ltnp 2>/dev/null | grep ':9090' | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)
if [ -n "$PIDS" ]; then kill $PIDS; fi

kubectl -n monitoring port-forward \
  svc/prom-kube-prometheus-stack-prometheus \
  9090:9090
```

### 4.6 metric 검증

다른 터미널에서 실행한다.

```bash
cd ~/cloud-virtualization-proj
bash scripts/verify-vllm-metrics.sh --vendor nvidia
bash scripts/gpu-preflight.sh --vendor nvidia
```

vLLM metric과 GPU metric이 수집되는지 확인한다. DCGM metric이 보이지 않으면 GPU utilization, GPU memory 관련 분석이 비어 있을 수 있다.

## 5. 워크로드별 부하테스트 실행

각 워크로드는 개별 실행할 수 있다.

### support_chat

```bash
bash scripts/run-workload.sh support_chat \
  --level standard \
  --target vllm \
  --gpu-vendor nvidia \
  --model mock
```

### doc_summary

```bash
bash scripts/run-workload.sh doc_summary \
  --level standard \
  --target vllm \
  --gpu-vendor nvidia \
  --model mock
```

### code_assistant

```bash
bash scripts/run-workload.sh code_assistant \
  --level standard \
  --target vllm \
  --gpu-vendor nvidia \
  --model mock
```

### json_extraction

```bash
bash scripts/run-workload.sh json_extraction \
  --level standard \
  --target vllm \
  --gpu-vendor nvidia \
  --model mock
```

실행 결과는 다음 경로에 저장된다.

```text
reports/session-<workload>-standard-<timestamp>/session-report.md
reports/session-<workload>-standard-<timestamp>/session-report.json
```

각 phase별 상세 결과는 session 디렉토리 하위에 저장된다.

```text
reports/session-<workload>-standard-<timestamp>/<phase>/report.md
```

## 6. 리포트 확인 방법

가장 최근 리포트 확인:

```bash
SESSION=$(ls -td reports/session-support_chat-standard-* | head -1)
cat "$SESSION/session-report.md"
```

VS Code에서 열기:

```bash
code "$SESSION/session-report.md"
```

4종 워크로드 요약 확인:

```bash
for W in support_chat doc_summary code_assistant json_extraction; do
  SESSION=$(ls -td reports/session-${W}-standard-* 2>/dev/null | head -1)
  echo
  echo "===== $W ====="
  echo "$SESSION"
  if [ -n "$SESSION" ]; then
    grep -A20 "부하 한계" "$SESSION/session-report.md" || true
    grep -A10 "기준 해석" "$SESSION/session-report.md" || true
  else
    echo "NO SESSION"
  fi
done
```

리포트에서 주로 볼 부분은 다음과 같다.

```text
1. 세션 개요
   전체 결과, score, 병목, safe capacity 확인

2. Phase별 결과
   부하 단계별 p95 latency, TTFT, bottleneck 확인

부하 한계 판정
   safe / knee / break 구간 확인

기준 해석
   이 실행에서 어떤 병목이 관측되었는지 확인
```

## 7. 설정 변경 후 재부하테스트 예시

### 7.1 support_chat latency 개선 예시

초기 설정에서는 RAG context와 출력 길이가 길어 latency 병목이 발생할 수 있다.  
이 경우 context와 output token을 줄여 요청당 처리 부담을 낮춘다.

변경 전 예시:

```text
max_num_seqs=256
RAG_CONTEXT_TOKENS=2000
RAG_MAX_TOKENS=500
```

변경 후 예시:

```text
max_num_seqs=64
RAG_CONTEXT_TOKENS=1000
RAG_MAX_TOKENS=128
```

실행:

```bash
BEFORE_CHAT=$(ls -td reports/session-support_chat-standard-* | head -1)

bash scripts/deploy-vllm-gpu.sh \
  --vendor nvidia \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --served-model-name mock \
  --max-model-len 4096 \
  --max-num-seqs 64 \
  --gpu-memory-utilization 0.85

bash scripts/run-workload.sh support_chat \
  --level standard \
  --target vllm \
  --gpu-vendor nvidia \
  --model mock \
  --env RAG_CONTEXT_TOKENS=1000 \
  --env RAG_MAX_TOKENS=128

AFTER_CHAT=$(ls -td reports/session-support_chat-standard-* | head -1)

analyzer/.venv/bin/python -m analyzer.session_compare \
  --before "$BEFORE_CHAT" \
  --after "$AFTER_CHAT" \
  --output reports/session-compare-support-chat-demo
```

비교 리포트:

```bash
code reports/session-compare-support-chat-demo/session-comparison.md
```

### 7.2 json_extraction queue 개선 예시

초기 설정에서는 200 RPS 구간에서 queue 병목이 발생할 수 있다.  
이 경우 출력 token을 줄이고 vLLM의 동시 처리 폭을 늘려 짧은 요청을 더 많이 처리하도록 조정한다.

변경 전 예시:

```text
max_num_seqs=256
EXTRACT_MAX_TOKENS=64
```

변경 후 예시:

```text
max_num_seqs=512
EXTRACT_MAX_TOKENS=16
```

실행:

```bash
BEFORE_JSON=$(ls -td reports/session-json_extraction-standard-* | head -1)

bash scripts/deploy-vllm-gpu.sh \
  --vendor nvidia \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --served-model-name mock \
  --max-model-len 4096 \
  --max-num-seqs 512 \
  --gpu-memory-utilization 0.85

bash scripts/run-workload.sh json_extraction \
  --level standard \
  --target vllm \
  --gpu-vendor nvidia \
  --model mock \
  --env EXTRACT_MAX_TOKENS=16

AFTER_JSON=$(ls -td reports/session-json_extraction-standard-* | head -1)

analyzer/.venv/bin/python -m analyzer.session_compare \
  --before "$BEFORE_JSON" \
  --after "$AFTER_JSON" \
  --output reports/session-compare-json-demo
```

비교 리포트:

```bash
code reports/session-compare-json-demo/session-comparison.md
```

## 8. 자주 발생한 문제와 해결

### nvidia.com/gpu가 보이지 않는 경우

증상:

```text
kubectl describe node 에 nvidia.com/gpu가 없음
vLLM Pod가 Insufficient nvidia.com/gpu로 Pending
```

확인:

```bash
nvidia-smi
kubectl -n kube-system logs ds/nvidia-device-plugin-daemonset --tail=100
command -v nvidia-container-runtime || true
kubectl describe node | grep -A10 -E "Capacity|Allocatable|nvidia.com/gpu"
```

해결 방향:

```text
NVIDIA driver 확인
NVIDIA container runtime 확인
NVIDIA device plugin 재시작
RuntimeClass 설정 확인
```

현재 스크립트는 GPU runtime 관련 설정을 최대한 자동으로 적용하도록 보완되어 있다.

### Prometheus 9090 포트가 이미 사용 중인 경우

증상:

```text
Unable to listen on port 9090
bind: address already in use
```

해결:

```bash
PIDS=$(ss -ltnp 2>/dev/null | grep ':9090' | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)
if [ -n "$PIDS" ]; then kill $PIDS; fi

kubectl -n monitoring port-forward \
  svc/prom-kube-prometheus-stack-prometheus \
  9090:9090
```

### vLLM Pod가 CrashLoopBackOff인 경우

확인:

```bash
kubectl -n llm-ops get pods -l app=vllm -o wide
kubectl -n llm-ops logs -l app=vllm --tail=200
kubectl -n llm-ops describe pod -l app=vllm
```

주요 원인:

```text
모델 다운로드 실패
GPU runtime 설정 문제
max_model_len 또는 memory 설정 과다
vLLM metric 또는 port 설정 충돌
```

### 오래된 vLLM Pod가 UnexpectedAdmissionError로 남는 경우

정상 Running Pod가 따로 있다면 오래된 Pod는 삭제해도 된다.

```bash
kubectl -n llm-ops get pods -l app=vllm -o wide
kubectl -n llm-ops delete pod <old-pod-name>
```

## 9. 제출 시 포함할 파일

포함 권장:

```text
analyzer/
loadtests/
k8s/
mock-llm/
scripts/
docs/
README.md
HANDOFF.md
AGENTS.md
CLAUDE.md
```

선택 포함:

```text
대표 session-report.md
대표 session-comparison.md
발표용 캡처 이미지
```

제외 권장:

```text
.git/
.venv/
.tmp/
reports/ 전체 원본
.pytest_cache/
__pycache__/
.claude/
.env
*.pem
*.key
```

`reports/`는 실행 결과라서 용량이 커질 수 있다. 전체 제출보다 발표와 보고서에 사용한 대표 리포트만 별도 폴더에 복사해서 제출하는 것이 좋다.

## 10. 현재 미비한 점과 추후 과제

현재 프로젝트는 Pipeline 1 중심으로 검증되었다. 즉, 이미 실행 중인 vLLM 서버에 워크로드별 부하를 주고, metric을 분석하고, 설정 변경 후 재테스트하여 개선 여부를 확인하는 흐름은 구현되어 있다.

다만 다음 항목은 아직 미비하거나 후속 과제로 남아 있다.

첫째, 설정 변경 자동화는 아직 완성되지 않았다. 현재는 analyzer report가 병목과 관련 지표를 보여주고, 사람이 그 결과를 바탕으로 설정을 변경한다. 반복 실험 데이터가 더 쌓이면 병목 유형별 rulebook을 만들어 추천 자동화를 고도화할 수 있다.

둘째, Pipeline 2는 일부만 구현되어 있다. 워크로드별 initial config와 Kubernetes manifest 생성 구조는 있으나, 사용자가 서비스 유형만 선택하면 운영 환경에 맞게 자동 배포하고 최적 설정까지 적용하는 단계는 아직 완전하지 않다.

셋째, Grafana는 핵심 검증 경로가 아니다. Prometheus metric 수집과 analyzer report 생성은 구현되어 있으나, 최종 시연과 검증은 analyzer report 중심으로 수행하였다. Grafana dashboard는 추후 병목 시각화 기능으로 확장할 수 있다.

넷째, OpenCost 기반 비용 분석은 아직 직접 연동되지 않았다. 현재 프로젝트는 성능과 병목 진단 중심이며, 비용까지 함께 고려하는 성능-비용 tradeoff 분석은 추후 과제이다.

다섯째, 현재 GPU 실험은 단일 GPU, 단일 vLLM Pod 중심이다. multi-GPU, multi-node, multi-replica scale-out 실험과 GPU autoscaling 검증은 후속 단계로 남아 있다.

여섯째, 응답 품질 평가는 포함하지 않았다. 본 프로젝트는 latency, token throughput, queue, GPU memory 등 운영 지표 중심으로 평가한다. 실제 서비스에서는 JSON 형식 유효성, 코드 실행 가능성, 요약 품질, 답변 정확성 같은 품질 지표도 추가로 평가해야 한다.

## 11. 최종 제출 전 체크리스트

```text
[ ] README와 docs 문서가 정상적으로 열리는지 확인
[ ] .env, pem, key 파일이 포함되지 않았는지 확인
[ ] reports 전체를 제출하지 않는 경우 대표 리포트만 별도로 복사
[ ] GPU 실험 결과 캡처 또는 report.md 경로 정리
[ ] 실행 명령어가 docs/submission-guide.md에 포함되어 있는지 확인
[ ] Grafana/OpenCost를 현재 완료 기능으로 과장해서 설명하지 않았는지 확인
[ ] 실제 실험 모델명이 Qwen/Qwen2.5-0.5B-Instruct로 일치하는지 확인
```
