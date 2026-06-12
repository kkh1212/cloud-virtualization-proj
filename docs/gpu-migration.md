# GPU / vLLM Migration Runbook

This stage is for the GPU server validation run after the pre-GPU mock pipeline
is complete.

The project keeps the same experiment flow:

```text
k6 -> OpenAI-compatible LLM server -> Prometheus -> analyzer report
```

The GPU stage replaces `mock-llm` with vLLM and adds GPU vendor metrics.

## Scope

Implemented for the first GPU server run:

- NVIDIA GPU stack path with NVIDIA device plugin and DCGM exporter
- AMD/ROCm stack path with ROCm device plugin and AMD Device Metrics Exporter
- vendor-specific vLLM Kubernetes manifests
- vLLM model cache PVC and optional Hugging Face token Secret
- `run-experiment.sh --target vllm --gpu-vendor nvidia|amd`
- vendor-specific analyzer metric profiles
- run evidence capture under each report directory

Still deferred:

- GPU Pod autoscaling
- multi-GPU / multi-node scheduling policy
- OpenCost integration
- final workload suitability scoring

## 1. Host Prerequisites

On the GPU server:

```bash
docker --version
kubectl get nodes -o wide
helm version --short
k6 version
```

For NVIDIA:

```bash
nvidia-smi
```

For AMD/ROCm:

```bash
rocm-smi
rocminfo
```

The primary success path is NVIDIA. AMD is prepared for validation, but consumer
Radeon cards such as RX6600 are best-effort because vLLM/ROCm compatibility is
much stronger on ROCm-supported Linux GPUs and Instinct-class cards.

## 2. Install Base Infra

If the server is fresh:

```bash
bash scripts/install-infra.sh
```

Deploy the existing monitoring stack and mock resources as usual before moving
to the GPU overlay.

## 3. Install GPU Stack

NVIDIA:

```bash
bash scripts/install-gpu-stack.sh --vendor nvidia
```

This installs or updates:

- NVIDIA device plugin
- DCGM exporter
- DCGM ServiceMonitor for Prometheus

Check:

```bash
kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu
kubectl -n kube-system get pods | grep nvidia
kubectl -n gpu-monitoring get pods,svc
```

AMD:

```bash
bash scripts/install-gpu-stack.sh --vendor amd
```

This installs or updates:

- AMD ROCm device plugin
- AMD Device Metrics Exporter

Check:

```bash
kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.amd\.com/gpu
kubectl -n kube-system get pods | grep -E 'amd|rocm'
kubectl -n kube-amd-gpu get pods,svc
```

## 4. Deploy vLLM

Default small validation model:

```bash
bash scripts/deploy-vllm-gpu.sh --vendor nvidia
```

or:

```bash
bash scripts/deploy-vllm-gpu.sh --vendor amd
```

The default model is:

```text
Qwen/Qwen2.5-0.5B-Instruct
```

The served OpenAI API model name defaults to:

```text
mock
```

That keeps the existing k6 payloads compatible.

For a larger model:

```bash
bash scripts/deploy-vllm-gpu.sh \
  --vendor nvidia \
  --model Qwen/Qwen2.5-7B-Instruct \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85
```

For gated Hugging Face models, export `HF_TOKEN` before deploying. The deploy
script writes it into the optional `hf-token` Kubernetes Secret.

Check:

```bash
kubectl -n llm-ops get deploy,svc,pod -l app=vllm
curl -fsS http://localhost:30081/health
```

## 5. Prometheus Checks

Port-forward Prometheus:

```bash
kubectl -n monitoring port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090
```

Smoke test:

```bash
bash scripts/gpu-preflight.sh --vendor nvidia
bash scripts/gpu-preflight.sh --vendor amd
```

Important vLLM queries:

```promql
vllm:num_requests_waiting
vllm:num_requests_running
vllm:request_queue_time_seconds_bucket
vllm:time_to_first_token_seconds_bucket
vllm:time_per_output_token_seconds_bucket
vllm:gpu_cache_usage_perc
```

Important NVIDIA queries:

```promql
DCGM_FI_DEV_GPU_UTIL
DCGM_FI_DEV_FB_USED
DCGM_FI_DEV_FB_FREE
```

Important AMD queries:

```promql
GPU_GFX_ACTIVITY
GPU_USED_VRAM
GPU_TOTAL_VRAM
```

## 6. Run GPU-backed Experiments

Start with the smallest scenario:

```bash
bash scripts/run-experiment.sh short_prompt --target vllm --gpu-vendor nvidia
```

For AMD:

```bash
bash scripts/run-experiment.sh short_prompt --target vllm --gpu-vendor amd
```

Then run the LLM-shaped scenarios:

```bash
bash scripts/run-experiment.sh long_input --target vllm --gpu-vendor nvidia
bash scripts/run-experiment.sh long_output --target vllm --gpu-vendor nvidia
bash scripts/run-experiment.sh rag_like --target vllm --gpu-vendor nvidia
bash scripts/run-experiment.sh mixed_workload --target vllm --gpu-vendor nvidia
```

Generate a report:

```bash
analyzer/.venv/bin/python -m analyzer.main --run reports/<scenario-timestamp>
```

`run-experiment.sh --target vllm` writes this into `run.json`:

```json
{
  "target": "vllm",
  "gpu_vendor": "nvidia",
  "model": "mock",
  "base_url": "http://localhost:30081",
  "metrics_config_path": "metrics-vllm-nvidia.yaml",
  "vllm_image": "vllm/vllm-openai:v0.11.2"
}
```

AMD runs use `metrics-vllm-amd.yaml`.

## 7. Evidence Captured Per Run

For vLLM targets, each report directory includes:

```text
cluster/nodes.txt
cluster/pods.txt
cluster/vllm-describe.txt
cluster/vllm-logs.txt
cluster/events.txt
cluster/gpu-plugin-pods.txt
cluster/gpu-exporter-pods.txt
prometheus/gpu-smoke.json
prometheus/vllm-smoke.json
```

Use these files to distinguish:

- vLLM Pod failed to schedule
- GPU resource was not advertised to Kubernetes
- model download/loading failed
- Prometheus did not scrape vLLM
- Prometheus did not scrape the GPU exporter
- analyzer PromQL profile does not match scraped metric names

## 8. What Success Looks Like

The GPU run is ready for comparison when:

- `kubectl get nodes` shows `nvidia.com/gpu` or `amd.com/gpu`
- vLLM health endpoint returns 200
- Prometheus has non-empty vLLM metrics
- Prometheus has non-empty GPU exporter metrics
- analyzer report includes k6 latency and vLLM internal metrics
- analyzer report includes GPU utilization and GPU memory rows
- GPU rules activate only when GPU metrics are present

## 9. First Comparison

Recommended order:

```text
1. mock + CPU HPA + sustained_ramp
2. mock + KEDA queue + sustained_ramp
3. vLLM + GPU + baseline scenarios
```

Comparison command:

```bash
analyzer/.venv/bin/python -m analyzer.compare \
  --before reports/<mock-run> \
  --after reports/<vllm-run>
```

The first GPU comparison should focus on evidence, not final suitability:

- p95/p99 latency
- queue wait p95
- TTFT p95
- TPOT p95
- prompt/output tokens per request
- prompt/output token throughput
- GPU utilization
- GPU memory pressure
- KV cache usage
