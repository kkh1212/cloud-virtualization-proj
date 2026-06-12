"""Right-sizing & autoscaling recommendation engine.

Turns observed load (snapshot), triggered rules (diagnosis) and the SLO verdict
into concrete, advisory config recommendations: container CPU/memory requests,
autoscaler mode/threshold/maxReplicas and per-pod concurrency. The 'current'
baseline and tuning knobs live in analyzer/config/recommend.yaml.

Every recommendation is advisory — nothing is applied. The intended loop is:
recommend -> apply by hand -> re-run experiment -> compare (Phase 5).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from analyzer.schemas import MetricSnapshot, TimeSeries


class RecommendConfigError(ValueError):
    pass


def load_recommend_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("current"), dict):
        raise RecommendConfigError(
            f"recommend config {path} must contain a 'current' mapping"
        )
    data.setdefault("tuning", {})
    return data


def build_recommendations(
    snapshot: MetricSnapshot,
    diagnosis: list,
    slo_evaluation: dict[str, Any] | None,
    config: dict[str, Any],
    workload_fit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = config.get("current", {})
    tuning = config.get("tuning", {})
    triggered = {r.rule_id for r in diagnosis if r.triggered}
    slo_breached = bool(slo_evaluation and slo_evaluation.get("met") is False)

    recs: list[dict[str, Any]] = []
    _recommend_cpu_request(recs, snapshot, current, tuning)
    _recommend_memory_request(recs, snapshot, current, tuning)
    _recommend_autoscaler(recs, snapshot, current, triggered, slo_breached)
    _recommend_concurrency(recs, snapshot, current, tuning, triggered)
    _apply_workload_awareness(recs, workload_fit)
    return recs


# Computed-rec target -> coarse category, matched against the workload bottleneck so
# the most relevant computed recommendation surfaces first.
def _rec_category(target: str) -> str:
    t = target.lower()
    if "autoscaler" in t or "keda" in t or "concurrency" in t:
        return "queue"
    if "cpu" in t:
        return "compute"
    if "memory" in t:
        return "memory"
    return "other"


_BOTTLENECK_REC_MATCH: dict[str, set[str]] = {
    "queue": {"queue"},
    "gpu_memory": {"memory"},
    "gpu_compute": {"compute"},
    "latency": {"queue", "compute"},
    "prefill": set(),   # relieved by playbook advice only (context/top_k/...)
    "decode": set(),    # relieved by playbook advice only (max_tokens/streaming/...)
}


def _apply_workload_awareness(
    recs: list[dict[str, Any]],
    workload_fit: dict[str, Any] | None,
) -> None:
    """Prioritize the bottleneck-relevant computed recs and append the workload's
    advisory playbook (config that the analyzer cannot compute, e.g. top_k/max_tokens)."""
    for rec in recs:
        rec.setdefault("priority", "normal")
    if not workload_fit:
        return

    bottleneck = workload_fit.get("bottleneck")
    match = _BOTTLENECK_REC_MATCH.get(bottleneck, set()) if bottleneck else set()
    for rec in recs:
        if bottleneck and _rec_category(rec.get("target", "")) in match:
            rec["priority"] = "high"

    advice = (workload_fit.get("recommendations") or {}).get(bottleneck, []) if bottleneck else []
    workload = workload_fit.get("workload")
    for text in advice:
        recs.append(
            {
                "target": f"[{bottleneck}] 워크로드 권장",
                "current": "-",
                "recommended": text,
                "rationale": f"워크로드 '{workload}' 적합성 기반 advisory (계산값 아님).",
                "priority": "high",
                "advisory": True,
            }
        )

    # Stable sort: high-priority first, original order preserved within each group.
    recs.sort(key=lambda r: 0 if r.get("priority") == "high" else 1)


def _recommend_cpu_request(recs, snapshot, current, tuning) -> None:
    cpu = _series(snapshot, "cpu_usage_ratio")
    cur_m = _float(current.get("cpu_request_millicores"))
    if cpu.length() == 0 or cur_m <= 0:
        return
    headroom = _float(tuning.get("cpu_headroom"), 1.3)
    floor_m = _float(tuning.get("cpu_request_floor_millicores"), 50)
    sig = _float(tuning.get("significant_change_pct"), 20) / 100.0

    peak_usage_m = cpu.max() * cur_m
    rec_m = max(floor_m, math.ceil(peak_usage_m * headroom))
    if abs(rec_m - cur_m) / cur_m < sig:
        return
    direction = "하향(over-provisioned)" if rec_m < cur_m else "상향(under-provisioned)"
    recs.append(
        {
            "target": "container.requests.cpu",
            "current": f"{int(cur_m)}m",
            "recommended": f"{int(rec_m)}m",
            "rationale": (
                f"CPU peak 사용률 {cpu.max():.2f}x → 피크 사용량 ≈ {peak_usage_m:.0f}m, "
                f"× headroom {headroom} = {int(rec_m)}m. {direction}."
            ),
        }
    )


def _recommend_memory_request(recs, snapshot, current, tuning) -> None:
    mem = _series(snapshot, "memory_bytes")
    cur_mib = _float(current.get("memory_request_mib"))
    if mem.length() == 0 or cur_mib <= 0:
        return
    ready = _series(snapshot, "replicas_ready")
    replicas = max(round(ready.mean()), 1) if ready.length() else 1
    headroom = _float(tuning.get("memory_headroom"), 1.3)
    floor_mib = _float(tuning.get("memory_request_floor_mib"), 128)
    sig = _float(tuning.get("significant_change_pct"), 20) / 100.0

    total_peak_mib = mem.max() / (1024 * 1024)
    per_pod_peak_mib = total_peak_mib / replicas
    rec_mib = max(floor_mib, math.ceil(per_pod_peak_mib * headroom))
    if abs(rec_mib - cur_mib) / cur_mib < sig:
        return
    direction = "하향" if rec_mib < cur_mib else "상향"
    recs.append(
        {
            "target": "container.requests.memory",
            "current": f"{int(cur_mib)}Mi",
            "recommended": f"{int(rec_mib)}Mi",
            "rationale": (
                f"per-pod 메모리 peak ≈ {per_pod_peak_mib:.0f}Mi "
                f"(총 {total_peak_mib:.0f}Mi / {replicas} replica) × headroom {headroom}. {direction}."
            ),
        }
    )


def _recommend_autoscaler(recs, snapshot, current, triggered, slo_breached) -> None:
    waiting = _series(snapshot, "requests_waiting")
    desired = _series(snapshot, "replicas_desired")
    max_waiting = waiting.max() if waiting.length() else 0.0

    if "hpa_limitation" in triggered:
        recs.append(
            {
                "target": "autoscaler.mode",
                "current": "CPU HPA",
                "recommended": "KEDA queue (scripts/use-keda-queue.sh)",
                "rationale": (
                    "hpa_limitation 발동: CPU 는 낮은데 큐가 적체 → CPU 기반 HPA 가 "
                    "스케일아웃을 못 함. 큐 기반 KEDA 오토스케일 권장."
                ),
            }
        )

    cur_thr = _float(current.get("keda_queue_threshold"))
    max_conc = _float(current.get("max_concurrency"), 1)
    queue_pressure = "queue_bottleneck" in triggered or "hpa_limitation" in triggered
    if cur_thr > 0 and queue_pressure and (slo_breached or max_waiting > cur_thr):
        rec_thr = min(int(cur_thr), max(int(max_conc * 2), int(round(cur_thr / 2))))
        if rec_thr < cur_thr:
            recs.append(
                {
                    "target": "keda.scaledObject.threshold (mock_llm_requests_waiting)",
                    "current": str(int(cur_thr)),
                    "recommended": str(rec_thr),
                    "rationale": (
                        f"관측 max_waiting={max_waiting:.0f}, 큐 적체로 지연/SLO 악화 → "
                        "threshold 하향으로 조기 스케일아웃."
                    ),
                }
            )

    rep_max = _float(current.get("replicas_max"))
    if (
        rep_max > 0
        and desired.length()
        and desired.max() >= rep_max
        and (slo_breached or "queue_bottleneck" in triggered)
    ):
        recs.append(
            {
                "target": "autoscaler.maxReplicaCount",
                "current": str(int(rep_max)),
                "recommended": str(int(math.ceil(rep_max * 1.5))),
                "rationale": (
                    f"desired replicas 가 상한({int(rep_max)})에 도달했고 SLO/큐 미해소 → "
                    "상한 상향 필요."
                ),
            }
        )


def _recommend_concurrency(recs, snapshot, current, tuning, triggered) -> None:
    # Only when the queue is the bottleneck AND CPU is not — adding concurrency
    # to a CPU-bound pod makes things worse.
    if "queue_bottleneck" not in triggered or "cpu_bottleneck" in triggered:
        return
    cpu = _series(snapshot, "cpu_usage_ratio")
    low_cpu = _float(tuning.get("low_cpu_ratio"), 0.5)
    if cpu.length() and cpu.max() >= low_cpu:
        return
    cur_conc = int(_float(current.get("max_concurrency")))
    if cur_conc <= 0:
        return
    factor = _float(tuning.get("concurrency_scale_factor"), 2)
    recs.append(
        {
            "target": "env.MOCK_LLM_MAX_CONCURRENCY",
            "current": str(cur_conc),
            "recommended": str(int(cur_conc * factor)),
            "rationale": (
                "큐 대기는 높지만 CPU 여유가 있음 → per-pod 동시 처리량 상향으로 큐 완화 가능."
            ),
        }
    )


def _series(snapshot: MetricSnapshot, name: str) -> TimeSeries:
    return snapshot.series.get(name, TimeSeries(name=name, points=[]))


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
