"""Pipeline B — workload-driven initial config generator.

Given a target workload (analyzer/config/workload-profiles.yaml `initial_config`)
plus user target conditions (model / GPU / RPS / token budgets), generate the
initial Kubernetes + LLM-serving manifests for a fresh deployment:

  - Deployment (vLLM container args: --max-model-len / --gpu-memory-utilization /
    --max-num-seqs / --served-model-name, GPU requests/limits, readiness/liveness probes)
  - Service (NodePort)
  - Autoscaler (HPA CPU or KEDA queue, per the workload's initial_config)

Output is written as manifest files (default k8s/generated/<workload>/). Nothing is
applied to a cluster unless --apply is passed explicitly. The full B optimization
loop (deploy -> load -> analyze -> recommend -> redeploy -> before/after) is out of
scope this round and reuses the Pipeline A analyzer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from analyzer.workload import WorkloadConfigError, load_workload_config

BASE_DIR = Path(__file__).resolve().parent

_GPU_RESOURCE = {"nvidia": "nvidia.com/gpu", "amd": "amd.com/gpu"}
_DEFAULT_IMAGE = {"nvidia": "vllm/vllm-openai:v0.11.2", "amd": "rocm/vllm:latest"}


class ProvisionRequest(BaseModel):
    """User-supplied target conditions; unset fields fall back to initial_config."""

    workload: str
    model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    served_model_name: str = "default"
    backend: Literal["vllm", "tgi", "ollama"] = "vllm"
    gpu_vendor: Literal["nvidia", "amd"] = "nvidia"
    gpu_count: int = 1
    namespace: str = "llm-ops"
    image: str | None = None
    # Overrides (None -> use workload initial_config)
    max_model_len: int | None = None
    gpu_memory_utilization: float | None = None
    replicas_min: int | None = None
    replicas_max: int | None = None
    autoscaler: Literal["keda_queue", "hpa_cpu", "none"] | None = None
    # Target conditions (recorded for traceability; lightly used as heuristics)
    expected_rps: float | None = None
    concurrent_users: int | None = None
    target_p95_seconds: float | None = None
    max_output_tokens: int | None = None
    context_len: int | None = None
    autoscaling: bool = True


def resolve_config(req: ProvisionRequest, workload_cfg: dict[str, Any]) -> dict[str, Any]:
    profiles = workload_cfg.get("profiles", {})
    if req.workload not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise WorkloadConfigError(
            f"unknown workload '{req.workload}'. available: {available}"
        )
    init = (profiles[req.workload] or {}).get("initial_config", {}) or {}
    replicas = init.get("replicas", {}) or {}

    autoscaler = req.autoscaler or init.get("autoscaler", "keda_queue")
    if not req.autoscaling:
        autoscaler = "none"

    # max_model_len: explicit override > initial_config > derived from context budget.
    max_model_len = (
        req.max_model_len
        or init.get("max_model_len")
        or ((req.context_len or 0) + (req.max_output_tokens or 0) or 4096)
    )

    # max_num_seqs: serving concurrency, from expected concurrent users (heuristic).
    max_num_seqs = max(8, min(256, req.concurrent_users or 16))

    return {
        "workload": req.workload,
        "namespace": req.namespace,
        "backend": req.backend,
        "gpu_vendor": req.gpu_vendor,
        "gpu_count": req.gpu_count,
        "image": req.image or _DEFAULT_IMAGE[req.gpu_vendor],
        "model": req.model,
        "served_model_name": req.served_model_name,
        "max_model_len": int(max_model_len),
        "max_num_seqs": int(max_num_seqs),
        "gpu_memory_utilization": float(
            req.gpu_memory_utilization
            if req.gpu_memory_utilization is not None
            else init.get("gpu_memory_utilization", 0.90)
        ),
        "max_tokens": init.get("max_tokens"),
        "request_timeout_seconds": init.get("request_timeout_seconds", 60),
        "replicas_min": int(req.replicas_min or replicas.get("min", 1)),
        "replicas_max": int(req.replicas_max or replicas.get("max", 4)),
        "autoscaler": autoscaler,
        # Echoed target conditions (traceability only)
        "target_conditions": {
            "expected_rps": req.expected_rps,
            "concurrent_users": req.concurrent_users,
            "target_p95_seconds": req.target_p95_seconds,
            "max_output_tokens": req.max_output_tokens,
            "context_len": req.context_len,
        },
    }


def build_manifests(resolved: dict[str, Any]) -> dict[str, dict[str, Any]]:
    manifests = {
        "00-deployment.yaml": _deployment(resolved),
        "01-service.yaml": _service(resolved),
    }
    autoscaler = _autoscaler(resolved)
    if autoscaler is not None:
        manifests["02-autoscaler.yaml"] = autoscaler
    return manifests


def _labels(resolved: dict[str, Any]) -> dict[str, str]:
    return {
        "app": "vllm",
        "app.kubernetes.io/name": "vllm",
        "app.kubernetes.io/part-of": "llm-ops-platform",
        "llm-ops/workload": resolved["workload"],
    }


def _container_args(resolved: dict[str, Any]) -> list[str]:
    backend = resolved["backend"]
    if backend == "vllm":
        return [
            "--host", "0.0.0.0",
            "--port", "8000",
            "--model", resolved["model"],
            "--served-model-name", resolved["served_model_name"],
            "--max-model-len", str(resolved["max_model_len"]),
            "--max-num-seqs", str(resolved["max_num_seqs"]),
            "--gpu-memory-utilization", str(resolved["gpu_memory_utilization"]),
        ]
    # TODO: TGI / Ollama are unvalidated this round — emit a best-effort arg stub.
    if backend == "tgi":
        return [
            "--model-id", resolved["model"],
            "--max-total-tokens", str(resolved["max_model_len"]),
        ]
    return ["serve", resolved["model"]]  # ollama best-effort


def _probe(path: str = "/health", **kw: Any) -> dict[str, Any]:
    probe = {"httpGet": {"path": path, "port": "http"}, "timeoutSeconds": 5}
    probe.update(kw)
    return probe


def _deployment(resolved: dict[str, Any]) -> dict[str, Any]:
    vendor = resolved["gpu_vendor"]
    gpu_key = _GPU_RESOURCE[vendor]
    labels = _labels(resolved)
    pod_labels = {**labels, "gpu.vendor": vendor}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "vllm", "namespace": resolved["namespace"], "labels": labels},
        "spec": {
            "replicas": resolved["replicas_min"],
            "selector": {"matchLabels": {"app": "vllm"}},
            "template": {
                "metadata": {
                    "labels": pod_labels,
                    "annotations": {
                        "prometheus.io/scrape": "true",
                        "prometheus.io/path": "/metrics",
                        "prometheus.io/port": "8000",
                    },
                },
                "spec": {
                    "tolerations": [
                        {"key": gpu_key, "operator": "Exists", "effect": "NoSchedule"}
                    ],
                    "containers": [
                        {
                            "name": "vllm",
                            "image": resolved["image"],
                            "imagePullPolicy": "IfNotPresent",
                            "args": _container_args(resolved),
                            "ports": [
                                {"name": "http", "containerPort": 8000, "protocol": "TCP"}
                            ],
                            "resources": {
                                "requests": {"cpu": "2", "memory": "8Gi"},
                                "limits": {
                                    "cpu": "8",
                                    "memory": "24Gi",
                                    gpu_key: str(resolved["gpu_count"]),
                                },
                            },
                            "startupProbe": _probe(periodSeconds=10, failureThreshold=120),
                            "readinessProbe": _probe(periodSeconds=10, failureThreshold=3),
                            "livenessProbe": _probe(
                                initialDelaySeconds=30, periodSeconds=20, failureThreshold=3
                            ),
                        }
                    ],
                },
            },
        },
    }


def _service(resolved: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "vllm", "namespace": resolved["namespace"], "labels": _labels(resolved)},
        "spec": {
            "type": "NodePort",
            "selector": {"app": "vllm"},
            "ports": [
                {
                    "name": "http",
                    "port": 8000,
                    "targetPort": "http",
                    "nodePort": 30081,
                    "protocol": "TCP",
                }
            ],
        },
    }


def _autoscaler(resolved: dict[str, Any]) -> dict[str, Any] | None:
    mode = resolved["autoscaler"]
    if mode == "none":
        return None
    if mode == "hpa_cpu":
        return {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": "vllm", "namespace": resolved["namespace"]},
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "vllm",
                },
                "minReplicas": resolved["replicas_min"],
                "maxReplicas": resolved["replicas_max"],
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {"type": "Utilization", "averageUtilization": 60},
                        },
                    }
                ],
            },
        }
    # keda_queue (default): scale on the vLLM waiting-requests queue.
    return {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {"name": "vllm", "namespace": resolved["namespace"]},
        "spec": {
            "scaleTargetRef": {"name": "vllm"},
            "minReplicaCount": resolved["replicas_min"],
            "maxReplicaCount": resolved["replicas_max"],
            "triggers": [
                {
                    "type": "prometheus",
                    "metadata": {
                        "serverAddress": "http://prom-kube-prometheus-stack-prometheus.monitoring:9090",
                        "metricName": "vllm_num_requests_waiting",
                        "query": "sum(vllm:num_requests_waiting)",
                        "threshold": "10",
                    },
                }
            ],
        },
    }


def write_manifests(
    manifests: dict[str, dict[str, Any]],
    out_dir: Path,
    resolved: dict[str, Any],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, doc in manifests.items():
        path = out_dir / name
        path.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        written.append(path)
    config_path = out_dir / "generated-config.json"
    config_path.write_text(
        json.dumps(resolved, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    written.append(config_path)
    return written


def main() -> int:
    args = _parse_args()
    req = ProvisionRequest(
        workload=args.workload,
        model=args.model,
        served_model_name=args.served_model_name,
        backend=args.backend,
        gpu_vendor=args.gpu,
        gpu_count=args.gpu_count,
        namespace=args.namespace,
        image=args.image,
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_mem_util,
        replicas_min=args.replicas_min,
        replicas_max=args.replicas_max,
        autoscaler=args.autoscaler,
        expected_rps=args.expected_rps,
        concurrent_users=args.concurrent_users,
        target_p95_seconds=args.target_p95,
        max_output_tokens=args.max_output_tokens,
        context_len=args.context_len,
        autoscaling=not args.no_autoscaling,
    )
    try:
        workload_cfg = load_workload_config(BASE_DIR / "config" / "workload-profiles.yaml")
        resolved = resolve_config(req, workload_cfg)
    except WorkloadConfigError as exc:
        print(f"[provision] {exc}", file=sys.stderr)
        return 2

    manifests = build_manifests(resolved)
    out_dir = Path(args.out) if args.out else (BASE_DIR.parent / "k8s" / "generated" / req.workload)
    written = write_manifests(manifests, out_dir, resolved)
    for path in written:
        print(f"wrote {path}")

    if args.apply:
        print("[provision] applying to cluster (kubectl apply)…")
        return subprocess.call(["kubectl", "apply", "-f", str(out_dir)])

    print(
        "\n적용 전 dry-run 검증:\n"
        f"  kubectl apply --dry-run=client -f {out_dir}\n"
        "실제 적용은 --apply 를 명시할 때만 수행됩니다."
    )
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate initial K8s/LLM manifests for a workload.")
    p.add_argument("--workload", required=True, help="analyzer/config/workload-profiles.yaml key")
    p.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    p.add_argument("--served-model-name", default="default")
    p.add_argument("--backend", choices=["vllm", "tgi", "ollama"], default="vllm")
    p.add_argument("--gpu", choices=["nvidia", "amd"], default="nvidia")
    p.add_argument("--gpu-count", type=int, default=1)
    p.add_argument("--namespace", default="llm-ops")
    p.add_argument("--image", default=None)
    p.add_argument("--max-model-len", type=int, default=None)
    p.add_argument("--gpu-mem-util", type=float, default=None)
    p.add_argument("--replicas-min", type=int, default=None)
    p.add_argument("--replicas-max", type=int, default=None)
    p.add_argument("--autoscaler", choices=["keda_queue", "hpa_cpu", "none"], default=None)
    p.add_argument("--expected-rps", type=float, default=None)
    p.add_argument("--concurrent-users", type=int, default=None)
    p.add_argument("--target-p95", type=float, default=None)
    p.add_argument("--max-output-tokens", type=int, default=None)
    p.add_argument("--context-len", type=int, default=None)
    p.add_argument("--no-autoscaling", action="store_true")
    p.add_argument("--out", default=None, help="Output dir. Default: k8s/generated/<workload>")
    p.add_argument("--apply", action="store_true", help="Apply to cluster (opt-in; default is file-only)")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
