# Local Codex Handoff

작성일: 2026-05-21 UTC

이 문서는 서버 VM에서 진행한 작업을 로컬 환경의 Codex가 이어서 수행할 수 있도록 현재 상태, 구현된 기능, 검증 결과, 남은 작업을 정리한다.

## 1. Repository State

```text
repository: https://github.com/kkh1212/cloud-virtualization-proj
branch: main
base commit before this handoff work: f85e817 Add LLM ops diagnostics platform MVP
```

서버 작업 디렉토리:

```text
/home/azureuser/llm-ops-platform
```

로컬에서는 다음으로 받으면 된다.

```bash
git clone https://github.com/kkh1212/cloud-virtualization-proj.git
cd cloud-virtualization-proj
```

이미 clone 되어 있으면:

```bash
git checkout main
git pull origin main
```

## 2. 구현 완료된 확장 작업

GPU 서버 이관 전에 CPU/mock 환경에서 운영 진단 플랫폼을 더 완성하기 위한 확장 작업이 들어갔다.

### KEDA queue autoscaling

추가된 항목:

```text
k8s/keda/mock-llm-queue-scaledobject.yaml
scripts/use-cpu-hpa.sh
scripts/use-keda-queue.sh
```

설계:

```text
CPU HPA와 KEDA는 동시에 같은 Deployment를 제어하지 않는다.
CPU baseline 실험 전에는 scripts/use-cpu-hpa.sh를 실행한다.
KEDA queue 실험 전에는 scripts/use-keda-queue.sh를 실행한다.
KEDA는 sum(mock_llm_requests_waiting)을 기준으로 scale한다.
threshold는 20, replica 범위는 2~8이다.
```

### Experiment comparison

추가된 항목:

```text
analyzer/compare.py
analyzer/tests/test_compare.py
```

사용법:

```bash
analyzer/.venv/bin/python -m analyzer.compare \
  --before "$CPU_RUN" \
  --after "$KEDA_RUN"
```

출력:

```text
comparison.md
comparison.json
```

비교 항목:

```text
avg latency
p95 latency peak
p99 latency peak
error rate peak
throughput avg/peak
max waiting
desired/ready replicas max
triggered rules added/removed
```

### Cost estimation

추가된 항목:

```text
analyzer/cost.py
analyzer/config/cost.yaml
analyzer/tests/test_cost.py
```

사용법:

```bash
analyzer/.venv/bin/python -m analyzer.main --run "$RUN_DIR" --cost-profile custom
```

비용 profile은 클라우드 과금 API를 호출하지 않는다. `analyzer/config/cost.yaml`에 AWS/GCP/Azure/custom 템플릿이 있고, 실제 단가는 사용자가 직접 채운다.

report.md에는 `## 6. 비용 추정` 섹션이 추가되었다. 기존 진단/개선 섹션 번호는 각각 7/8로 이동했다.

### CI

추가된 항목:

```text
.github/workflows/ci.yml
analyzer/requirements-dev.txt
```

CI에서 실행하는 검증:

```text
analyzer tests
mock-llm tests
python compileall
Kubernetes YAML parse
mock-llm Docker build
```

### GPU migration checklist

추가된 항목:

```text
docs/gpu-migration.md
```

포함 내용:

```text
NVIDIA driver
NVIDIA device plugin
DCGM exporter
vLLM 또는 GPU inference server
GPU PromQL 활성화
GPU rule threshold 재검증
CPU/KEDA/GPU 비교 절차
```

## 3. 문서 업데이트 위치

로컬 Codex는 다음 문서를 먼저 읽으면 된다.

```text
README.md section 19~20
docs/runbook.md Phase 10~13
docs/experiment-plan.md CPU HPA vs KEDA queue autoscaling
docs/architecture.md KEDA 확장
docs/gpu-migration.md
```

## 4. 서버에서 검증한 명령

서버에서 아래 검증을 통과했다.

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
# 21 passed

mock-llm/.venv/bin/pytest mock-llm/tests -v
# 4 passed

analyzer/.venv/bin/python -m analyzer.compare --help
# OK

analyzer/.venv/bin/python -m analyzer.main --help
# --cost-profile 확인

analyzer/.venv/bin/python -c "from pathlib import Path; import yaml; paths=sorted(Path('k8s').rglob('*.yaml')); [list(yaml.safe_load_all(p.read_text(encoding='utf-8'))) for p in paths]; print('OK', len(paths))"
# OK 8

bash -n scripts/use-cpu-hpa.sh
bash -n scripts/use-keda-queue.sh
# OK

docker build -t mock-llm:ci mock-llm/
# OK
```

## 5. 아직 실제로 실행하지 않은 것

아래는 코드/문서/테스트는 준비됐지만 실제 cluster에서 아직 실행하지 않은 검증이다.

```text
KEDA Helm install
CPU HPA baseline run 생성
KEDA queue autoscaling run 생성
CPU_RUN vs KEDA_RUN comparison.md 생성
KEDA run fixture 캡처
KEDA threshold 20이 적절한지 실측 튜닝
```

## 6. 로컬에서 이어서 할 작업

### Step 1: 최신 코드 받기

```bash
git checkout main
git pull origin main
```

### Step 2: Python 환경 준비

```bash
python3 -m venv analyzer/.venv
analyzer/.venv/bin/pip install -r analyzer/requirements-dev.txt

python3 -m venv mock-llm/.venv
mock-llm/.venv/bin/pip install -r mock-llm/requirements-dev.txt
```

### Step 3: 기본 검증

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
mock-llm/.venv/bin/pytest mock-llm/tests -v
```

### Step 4: KEDA 설치

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda -n keda --create-namespace
kubectl -n keda rollout status deploy/keda-operator
```

### Step 5: CPU HPA baseline run

```bash
bash scripts/use-cpu-hpa.sh
bash scripts/run-experiment.sh burst_traffic
CPU_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$CPU_RUN" --cost-profile custom
```

### Step 6: KEDA queue run

```bash
bash scripts/use-keda-queue.sh
bash scripts/run-experiment.sh burst_traffic
KEDA_RUN=$(ls -dt reports/burst_traffic-* | head -1)
analyzer/.venv/bin/python -m analyzer.main --run "$KEDA_RUN" --cost-profile custom
```

### Step 7: 비교 리포트 생성

```bash
analyzer/.venv/bin/python -m analyzer.compare \
  --before "$CPU_RUN" \
  --after "$KEDA_RUN"

cat "$KEDA_RUN/comparison.md"
```

볼 것:

```text
KEDA_RUN에서 desired/ready replicas max가 2보다 커지는지
max waiting이 CPU baseline보다 줄었는지
p95/p99 latency peak가 줄었는지
hpa_limitation이 removed 또는 완화됐는지
cost per 1K requests/tokens가 어떻게 변했는지
```

### Step 8: KEDA fixture 캡처 선택

KEDA run이 재현 가능하게 안정되면 fixture로 보존한다.

```bash
analyzer/.venv/bin/python -m analyzer.tools.capture_fixtures \
  --run "$KEDA_RUN" \
  --output analyzer/tests/fixtures/burst_keda_queue

analyzer/.venv/bin/pytest analyzer/tests -v
```

fixture를 commit할지는 결과가 안정적인지 확인한 뒤 결정한다.

## 7. 다음 큰 단계

KEDA 비교가 끝나면 GPU 서버에서 `docs/gpu-migration.md` 순서로 진행한다.

GPU 단계의 핵심은 다음이다.

```text
DCGM exporter 설치
GPU PromQL non-empty 확인
analyzer/config/metrics.yaml GPU metric 활성화
gpu_compute / gpu_memory / gpu_scheduling rule 실측 검증
CPU HPA vs KEDA vs GPU run 비교
```

## 8. 주의사항

- `.claude/`, `.venv/`, `.pytest_cache/`, `reports/`는 git ignore 대상이다.
- `analyzer/config/cost.yaml`의 단가는 전부 예시 0.0이다. 실제 비교 전에 값을 채워야 한다.
- KEDA와 CPU HPA는 동시에 켜지 않는다. 반드시 mode switch script를 사용한다.
- GPU rule은 GPU metric이 없으면 계속 skip되는 것이 정상이다.
