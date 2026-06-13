"""Pipeline B workload-driven manifest generator.

Pipeline B starts from a service workload and emits Kubernetes manifests for a
fresh vLLM deployment. It intentionally supports two profiles:

- run: single-GPU validation profile that is safe on a one-GPU VM.
- recommended: workload initial_config profile for a larger production-shaped
  deployment.

Use --profile both to write both sets. When --apply is used with --profile both,
only the run profile is applied by default.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from analyzer.workload import WorkloadConfigError, load_workload_config

BASE_DIR = Path(__file__).resolve().parent

Profile = Literal["run", "recommended"]

_GPU_RESOURCE = {"nvidia": "nvidia.com/gpu", "amd": "amd.com/gpu"}
_DEFAULT_IMAGE = {"nvidia": "vllm/vllm-openai:v0.11.2", "amd": "vllm/vllm-openai-rocm:latest"}
_RUNTIME_CLASS = {"nvidia": "nvidia", "amd": "amd"}


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
    # Overrides (None -> use workload initial_config).
    max_model_len: int | None = None
    gpu_memory_utilization: float | None = None
    replicas_min: int | None = None
    replicas_max: int | None = None
    autoscaler: Literal["keda_queue", "hpa_cpu", "none"] | None = None
    # Target conditions (recorded for traceability; lightly used as heuristics).
    expected_rps: float | None = None
    concurrent_users: int | None = None
    target_p95_seconds: float | None = None
    max_output_tokens: int | None = None
    context_len: int | None = None
    autoscaling: bool = True


def resolve_config(
    req: ProvisionRequest,
    workload_cfg: dict[str, Any],
    profile: Profile = "recommended",
) -> dict[str, Any]:
    profiles = workload_cfg.get("profiles", {})
    if req.workload not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise WorkloadConfigError(
            f"unknown workload '{req.workload}'. available: {available}"
        )
    init = (profiles[req.workload] or {}).get("initial_config", {}) or {}
    replicas = init.get("replicas", {}) or {}

    autoscaler = req.autoscaler or init.get("autoscaler", "keda_queue")
    replicas_min = int(req.replicas_min or replicas.get("min", 1))
    replicas_max = int(req.replicas_max or replicas.get("max", replicas_min))

    if not req.autoscaling:
        autoscaler = "none"

    if profile == "run":
        # The project GPU validation path starts with a single GPU. Multiple
        # vLLM replicas would leave new Pods Pending because each Pod requests
        # one full GPU, so the executable profile is intentionally conservative.
        autoscaler = "none"
        replicas_min = 1
        replicas_max = 1

    max_model_len = (
        req.max_model_len
        or init.get("max_model_len")
        or ((req.context_len or 0) + (req.max_output_tokens or 0) or 4096)
    )

    max_num_seqs = max(8, min(256, req.concurrent_users or 16))

    return {
        "profile": profile,
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
        "replicas_min": replicas_min,
        "replicas_max": replicas_max,
        "autoscaler": autoscaler,
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
        "00-namespace.yaml": _namespace(resolved),
        "01-pvc.yaml": _pvc(resolved),
        "02-deployment.yaml": _deployment(resolved),
        "03-service.yaml": _service(resolved),
        "04-servicemonitor.yaml": _servicemonitor(resolved),
    }
    autoscaler = _autoscaler(resolved)
    if autoscaler is not None:
        manifests["05-autoscaler.yaml"] = autoscaler
    return manifests


def _labels(resolved: dict[str, Any]) -> dict[str, str]:
    return {
        "app": "vllm",
        "app.kubernetes.io/name": "vllm",
        "app.kubernetes.io/part-of": "llm-ops-platform",
        "llm-ops/workload": resolved["workload"],
        "llm-ops/profile": resolved["profile"],
    }


def _namespace(resolved: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": resolved["namespace"]},
    }


def _pvc(resolved: dict[str, Any]) -> dict[str, Any]:
    labels = _labels(resolved)
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": "vllm-model-cache",
            "namespace": resolved["namespace"],
            "labels": labels,
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "30Gi"}},
        },
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
    if backend == "tgi":
        return [
            "--model-id", resolved["model"],
            "--max-total-tokens", str(resolved["max_model_len"]),
        ]
    return ["serve", resolved["model"]]


def _probe(path: str = "/health", **kw: Any) -> dict[str, Any]:
    probe = {"httpGet": {"path": path, "port": "http"}, "timeoutSeconds": 5}
    probe.update(kw)
    return probe


def _deployment(resolved: dict[str, Any]) -> dict[str, Any]:
    vendor = resolved["gpu_vendor"]
    gpu_key = _GPU_RESOURCE[vendor]
    labels = _labels(resolved)
    pod_labels = {**labels, "gpu.vendor": vendor}
    pod_spec: dict[str, Any] = {
        "enableServiceLinks": False,
        "runtimeClassName": _RUNTIME_CLASS[vendor],
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
                "env": [
                    {"name": "GPU_VENDOR", "value": vendor},
                    {"name": "HF_HOME", "value": "/models"},
                    {"name": "TRANSFORMERS_CACHE", "value": "/models"},
                    {"name": "VLLM_NO_USAGE_STATS", "value": "1"},
                    {"name": "VLLM_PORT", "value": "29500"},
                    {
                        "name": "HF_TOKEN",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": "hf-token",
                                "key": "token",
                                "optional": True,
                            }
                        },
                    },
                    {
                        "name": "HUGGING_FACE_HUB_TOKEN",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": "hf-token",
                                "key": "token",
                                "optional": True,
                            }
                        },
                    },
                ],
                "resources": {
                    "requests": {"cpu": "2", "memory": "8Gi"},
                    "limits": {
                        "cpu": "8",
                        "memory": "24Gi",
                        gpu_key: str(resolved["gpu_count"]),
                    },
                },
                "volumeMounts": [
                    {"name": "model-cache", "mountPath": "/models"},
                    {"name": "shm", "mountPath": "/dev/shm"},
                ],
                "startupProbe": _probe(periodSeconds=10, failureThreshold=120),
                "readinessProbe": _probe(periodSeconds=10, failureThreshold=3),
                "livenessProbe": _probe(
                    initialDelaySeconds=30, periodSeconds=20, failureThreshold=3
                ),
            }
        ],
        "volumes": [
            {"name": "model-cache", "persistentVolumeClaim": {"claimName": "vllm-model-cache"}},
            {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}},
        ],
    }
    if vendor == "amd":
        pod_spec["containers"][0]["securityContext"] = {
            "capabilities": {"add": ["SYS_PTRACE"]},
            "seccompProfile": {"type": "Unconfined"},
        }

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "vllm", "namespace": resolved["namespace"], "labels": labels},
        "spec": {
            "replicas": resolved["replicas_min"],
            "strategy": {"type": "Recreate"},
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
                "spec": pod_spec,
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


def _servicemonitor(resolved: dict[str, Any]) -> dict[str, Any]:
    labels = _labels(resolved)
    labels["release"] = "prom"
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "ServiceMonitor",
        "metadata": {"name": "vllm", "namespace": resolved["namespace"], "labels": labels},
        "spec": {
            "selector": {"matchLabels": {"app": "vllm"}},
            "namespaceSelector": {"matchNames": [resolved["namespace"]]},
            "endpoints": [
                {
                    "port": "http",
                    "path": "/metrics",
                    "interval": "5s",
                    "scrapeTimeout": "4s",
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


def _profile_values(profile: Literal["run", "recommended", "both"]) -> list[Profile]:
    return ["run", "recommended"] if profile == "both" else [profile]


def _default_out_dir(workload: str, profile: Profile) -> Path:
    return BASE_DIR.parent / "k8s" / "generated" / workload / profile


def _output_dir(args: argparse.Namespace, workload: str, profile: Profile) -> Path:
    if args.out and args.profile == "both":
        return Path(args.out) / profile
    if args.out:
        return Path(args.out)
    return _default_out_dir(workload, profile)


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
        resolved_by_profile = {
            profile: resolve_config(req, workload_cfg, profile=profile)
            for profile in _profile_values(args.profile)
        }
    except WorkloadConfigError as exc:
        print(f"[provision] {exc}", file=sys.stderr)
        return 2

    apply_dir: Path | None = None
    for profile, resolved in resolved_by_profile.items():
        out_dir = _output_dir(args, req.workload, profile)
        written = write_manifests(build_manifests(resolved), out_dir, resolved)
        print(f"[provision] profile={profile} out={out_dir}")
        for path in written:
            print(f"wrote {path}")
        if profile == "run":
            apply_dir = out_dir

    if args.apply:
        if apply_dir is None:
            apply_dir = _output_dir(args, req.workload, "recommended")
        print(f"[provision] applying executable profile: {apply_dir}")
        return subprocess.call(["kubectl", "apply", "-f", str(apply_dir)])

    print("\nValidate generated manifests with:")
    if args.profile == "both":
        base = Path(args.out) if args.out else BASE_DIR.parent / "k8s" / "generated" / req.workload
        print(f"  kubectl apply --dry-run=client -f {base / 'run'}")
        print(f"  kubectl apply --dry-run=client -f {base / 'recommended'}")
    else:
        out = Path(args.out) if args.out else _default_out_dir(req.workload, args.profile)
        print(f"  kubectl apply --dry-run=client -f {out}")
    print("Use --apply to apply the run profile to the current cluster.")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate initial K8s/LLM manifests for a workload.")
    p.add_argument("--workload", required=True, help="analyzer/config/workload-profiles.yaml key")
    p.add_argument("--profile", choices=["run", "recommended", "both"], default="both")
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
    p.add_argument("--out", default=None, help="Output dir. Default: k8s/generated/<workload>/<profile>")
    p.add_argument("--apply", action="store_true", help="Apply the executable run profile")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
