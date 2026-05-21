# GPU Migration Checklist

GPU 서버로 옮기기 전후에 확인할 항목을 정리한다. 현재 CPU/mock MVP는 GPU metric이 없으면 GPU rule이 자동 skip되도록 설계되어 있다.

## 1. GPU 노드 준비

- NVIDIA driver 설치 및 `nvidia-smi` 정상 출력 확인
- Kubernetes node가 Ready인지 확인
- GPU node label/taint 정책 확인
- NVIDIA device plugin 설치

검증 예시:

```bash
kubectl get nodes -o wide
kubectl describe node <gpu-node> | grep -A5 -i nvidia
```

## 2. GPU metric 수집

- DCGM exporter 설치
- Prometheus가 DCGM exporter target을 scrape하는지 확인
- `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_FB_FREE` metric 확인

Prometheus query 예시:

```promql
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_FB_FREE
```

## 3. Inference server 교체

- mock-llm 대신 vLLM 또는 GPU 기반 inference server 배포
- `/metrics` endpoint가 Prometheus에서 scrape되는지 확인
- vLLM KV cache metric이 있으면 `vllm:gpu_cache_usage_perc` 계열 PromQL 확인

## 4. analyzer GPU metric 활성화

`analyzer/config/metrics.yaml`에서 GPU 관련 PromQL 주석을 해제하고 실제 label selector를 GPU 배포에 맞춘다.

활성화 대상:

```text
gpu_utilization
gpu_memory_used_ratio
kv_cache_usage_ratio
```

그 다음 테스트:

```bash
analyzer/.venv/bin/pytest analyzer/tests -v
```

## 5. CPU/KEDA/GPU 결과 비교

권장 비교 순서:

```text
1. CPU HPA baseline run
2. KEDA queue autoscaling run
3. GPU inference run
```

각 run에서 analyzer report를 생성한 뒤 비교한다.

```bash
analyzer/.venv/bin/python -m analyzer.compare \
  --before reports/burst_traffic-cpu-hpa \
  --after reports/burst_traffic-keda

analyzer/.venv/bin/python -m analyzer.compare \
  --before reports/burst_traffic-keda \
  --after reports/burst_traffic-gpu
```

볼 것:

- p95/p99 latency peak 감소
- max waiting 감소
- triggered rule 변화
- GPU utilization / memory pressure rule 활성화 여부
- cost per 1K requests / tokens 변화

## 6. 완료 기준

- Prometheus에서 GPU metric query가 non-empty
- analyzer report의 GPU 상태가 실제 수집 값 기반으로 표시
- GPU rule이 required_metrics gate를 통과
- CPU/KEDA/GPU 비교 리포트 생성
- GPU run fixture 캡처 및 replay 테스트 통과
