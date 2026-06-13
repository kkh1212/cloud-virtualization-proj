from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from analyzer.provision import (
    ProvisionRequest,
    build_manifests,
    resolve_config,
    write_manifests,
)
from analyzer.workload import WorkloadConfigError, load_workload_config

CFG = load_workload_config(
    Path(__file__).resolve().parents[1] / "config" / "workload-profiles.yaml"
)


def _container(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def test_recommended_profile_uses_initial_config_and_keda_nvidia():
    req = ProvisionRequest(workload="code_assistant", concurrent_users=32)
    resolved = resolve_config(req, CFG, profile="recommended")
    assert resolved["max_model_len"] == 16384  # from initial_config
    assert resolved["autoscaler"] == "keda_queue"
    assert resolved["max_num_seqs"] == 32  # from concurrent_users

    manifests = build_manifests(resolved)
    container = _container(manifests["02-deployment.yaml"])
    assert "nvidia.com/gpu" in container["resources"]["limits"]
    assert "--max-model-len" in container["args"]
    assert "16384" in container["args"]
    assert manifests["02-deployment.yaml"]["spec"]["template"]["spec"]["runtimeClassName"] == "nvidia"
    assert manifests["02-deployment.yaml"]["spec"]["template"]["spec"]["enableServiceLinks"] is False
    assert any(item["name"] == "VLLM_PORT" and item["value"] == "29500" for item in container["env"])
    assert container["readinessProbe"]["httpGet"]["path"] == "/health"
    assert manifests["05-autoscaler.yaml"]["kind"] == "ScaledObject"


def test_run_profile_is_single_gpu_executable():
    req = ProvisionRequest(workload="support_chat")
    resolved = resolve_config(req, CFG, profile="run")
    assert resolved["replicas_min"] == 1
    assert resolved["replicas_max"] == 1
    assert resolved["autoscaler"] == "none"

    manifests = build_manifests(resolved)
    assert "05-autoscaler.yaml" not in manifests
    assert manifests["02-deployment.yaml"]["spec"]["replicas"] == 1
    assert manifests["02-deployment.yaml"]["spec"]["strategy"]["type"] == "Recreate"


def test_override_and_hpa_and_amd():
    req = ProvisionRequest(
        workload="doc_summary",
        gpu_vendor="amd",
        max_model_len=2048,
        autoscaler="hpa_cpu",
    )
    resolved = resolve_config(req, CFG, profile="recommended")
    assert resolved["max_model_len"] == 2048  # override wins over initial_config

    manifests = build_manifests(resolved)
    assert manifests["05-autoscaler.yaml"]["kind"] == "HorizontalPodAutoscaler"
    assert "amd.com/gpu" in _container(manifests["02-deployment.yaml"])["resources"]["limits"]


def test_no_autoscaling_omits_autoscaler():
    req = ProvisionRequest(workload="support_chat", autoscaling=False)
    manifests = build_manifests(resolve_config(req, CFG))
    assert "05-autoscaler.yaml" not in manifests


def test_unknown_workload_raises():
    with pytest.raises(WorkloadConfigError):
        resolve_config(ProvisionRequest(workload="nope"), CFG)


def test_write_manifests_emits_valid_files(tmp_path):
    req = ProvisionRequest(workload="support_chat")
    resolved = resolve_config(req, CFG)
    written = write_manifests(build_manifests(resolved), tmp_path, resolved)
    names = {p.name for p in written}
    assert "00-namespace.yaml" in names
    assert "01-pvc.yaml" in names
    assert "02-deployment.yaml" in names
    assert "03-service.yaml" in names
    assert "04-servicemonitor.yaml" in names
    assert "generated-config.json" in names
    doc = yaml.safe_load((tmp_path / "02-deployment.yaml").read_text(encoding="utf-8"))
    assert doc["kind"] == "Deployment"
    assert doc["metadata"]["namespace"] == "llm-ops"
