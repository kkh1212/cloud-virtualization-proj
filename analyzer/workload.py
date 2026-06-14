"""Workload load-criteria judgment for the chosen workload.

Single source of workload truth is analyzer/config/workload-profiles.yaml, mirroring
the SLO/cost-profile mechanism. build_workload_fit() compares observed metrics against
the workload's per-metric thresholds and produces a suitable / partially_suitable /
unsuitable verdict + a 0-100 score + the dominant bottleneck category.

A threshold whose underlying metric is absent from the snapshot is skipped (not
failed) — same gating philosophy as Rule.required_metrics and slo.py. This lets GPU
thresholds (gpu_utilization, gpu_memory_used_ratio) skip cleanly on the mock pipeline
and activate automatically once vLLM/DCGM metrics are present.

Workload profiles may additionally declare required_metrics. Those are not optional:
if any required metric is absent, the run is marked measurement_failed so empty
Prometheus/k6 data cannot look like a passing workload phase.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from analyzer.schemas import MetricSnapshot, RuleResult, TimeSeries

# Logical metric -> bottleneck category. Drives the "dominant bottleneck" label and
# links the failed metric to the recommendation playbook in workload-profiles.yaml.
_BOTTLENECK_BY_METRIC: dict[str, str] = {
    "requests_waiting": "queue",
    "queue_wait_p95": "queue",
    "ttft_p95": "prefill",
    "prompt_tokens_p95": "prefill",
    "tpot_p95": "decode",
    "output_tokens_p95": "decode",
    "output_token_rate": "decode",
    "gpu_memory_used_ratio": "gpu_memory",
    "kv_cache_ratio": "gpu_memory",
    "memory_bytes": "gpu_memory",
    "gpu_utilization": "gpu_compute",
    "p95_latency": "latency",
    "p99_latency": "latency",
}


class WorkloadConfigError(ValueError):
    pass


def load_workload_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("profiles"), dict):
        raise WorkloadConfigError(
            f"workload config {path} must contain a profiles mapping"
        )
    return data


def workload_slo_profile(workload_name: str, config: dict[str, Any]) -> str | None:
    """The slo.yaml profile this workload maps to, for SLO auto-selection."""
    profile = _profile(workload_name, config)
    value = profile.get("slo_profile")
    return str(value) if value else None


def build_workload_fit(
    snapshot: MetricSnapshot,
    workload_name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    profile = _profile(workload_name, config)
    thresholds = profile.get("thresholds", {}) or {}
    required_metrics = [str(metric) for metric in profile.get("required_metrics", []) or []]
    missing_required = [
        metric for metric in required_metrics if _series_missing(snapshot, metric)
    ]

    checks: list[dict[str, Any]] = []
    for metric, spec in thresholds.items():
        check = _evaluate_threshold(snapshot, metric, spec or {})
        if check is not None:
            checks.append(check)

    evaluated = [c for c in checks if c["met"] is not None]
    passed = [c for c in evaluated if c["met"]]
    failed = [c for c in evaluated if not c["met"]]
    skipped = len(thresholds) - len(evaluated)

    critical_failed = [c for c in failed if c.get("critical")]

    if missing_required:
        verdict: str | None = "measurement_failed"
        score: float | None = None
    elif not evaluated:
        verdict: str | None = None
        score: float | None = None
    else:
        if critical_failed:
            verdict = "unsuitable"
        elif not failed:
            verdict = "suitable"
        elif passed:
            verdict = "partially_suitable"
        else:
            verdict = "unsuitable"
        total_weight = sum(c["weight"] for c in evaluated)
        passed_weight = sum(c["weight"] for c in passed)
        score = round(passed_weight / total_weight * 100.0, 1) if total_weight else None

    return {
        "workload": workload_name,
        "description": profile.get("description", ""),
        "request_shape": profile.get("request_shape", ""),
        "slo_profile": profile.get("slo_profile"),
        "verdict": verdict,
        "score": score,
        "bottleneck": "measurement" if missing_required else _dominant_bottleneck(failed),
        "recommendations": profile.get("recommendations", {}) or {},
        "required_metrics": required_metrics,
        "missing_required_metrics": missing_required,
        "checks": checks,
        "counts": {
            "evaluated": len(evaluated),
            "passed": len(passed),
            "failed": len(failed),
            "skipped": skipped,
            "missing_required": len(missing_required),
        },
    }


def workload_fit_result(workload_fit: dict[str, Any]) -> RuleResult | None:
    """Turn a non-passing verdict into a diagnosis entry (optional flow into improvements)."""
    verdict = workload_fit.get("verdict")
    if verdict not in ("measurement_failed", "partially_suitable", "unsuitable"):
        return None
    failed = [c for c in workload_fit.get("checks", []) if c["met"] is False]
    evidence = {
        c["metric"]: {"target": c["target"], "observed": round(c["observed"], 6)}
        for c in failed
    }
    missing = workload_fit.get("missing_required_metrics") or []
    if missing:
        evidence["missing_required_metrics"] = missing
    severity = "critical" if verdict == "unsuitable" else "warning"
    workload = workload_fit.get("workload")
    bottleneck = workload_fit.get("bottleneck")
    verdict_label = {
        "measurement_failed": "측정 실패",
        "partially_suitable": "주의",
        "unsuitable": "한계 도달",
    }.get(verdict, verdict)
    if verdict == "measurement_failed":
        suggestion = (
            f"워크로드 '{workload}' 부하 기준 측정 실패. "
            f"누락 지표: {', '.join(missing) if missing else 'unknown'}. "
            "Prometheus scrape, vLLM metric 이름, k6 실패 여부를 확인하고 재실험하세요."
        )
    else:
        suggestion = (
            f"워크로드 '{workload}' 부하 기준 {verdict_label}"
            + (f" (주요 병목: {bottleneck})." if bottleneck else ".")
            + " 권장(playbook)을 참고해 동일 ladder 재실행으로 변화를 확인하세요."
        )
    return RuleResult(
        rule_id="workload_fit",
        triggered=True,
        severity=severity,
        evidence=evidence,
        suggestion=suggestion,
    )


def _evaluate_threshold(
    snapshot: MetricSnapshot,
    metric: str,
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    if "max" in spec:
        direction, target = "max", _number(spec["max"], metric)
        default_agg = "peak"
    elif "min" in spec:
        direction, target = "min", _number(spec["min"], metric)
        default_agg = "mean"
    else:
        raise WorkloadConfigError(
            f"threshold for '{metric}' must define 'max' or 'min'"
        )

    agg = str(spec.get("agg", default_agg))
    weight = _number(spec.get("weight", 1), f"{metric}.weight")

    series = snapshot.series.get(metric)
    if _series_missing(snapshot, metric):
        # Metric not collected in this run -> skip (not a failure).
        return {
            "metric": metric,
            "direction": direction,
            "target": target,
            "observed": None,
            "agg": agg,
            "met": None,
            "weight": weight,
        }

    observed = _aggregate(series, agg)
    met = observed <= target if direction == "max" else observed >= target
    critical = _critical_breach(observed, direction, target, spec) if not met else False
    return {
        "metric": metric,
        "direction": direction,
        "target": target,
        "observed": observed,
        "agg": agg,
        "met": met,
        "critical": critical,
        "weight": weight,
    }


def _aggregate(series: TimeSeries, agg: str) -> float:
    if agg == "mean":
        return series.mean()
    if agg == "min":
        return series.min()
    return series.max()  # "peak" / default


def _dominant_bottleneck(failed: list[dict[str, Any]]) -> str | None:
    """The bottleneck category of the worst-overshoot failed metric."""
    if not failed:
        return None

    def overshoot(check: dict[str, Any]) -> float:
        target = check["target"]
        observed = check["observed"]
        if target == 0:
            return float("inf")
        if check["direction"] == "max":
            return (observed - target) / target
        return (target - observed) / target

    worst = max(failed, key=overshoot)
    return _BOTTLENECK_BY_METRIC.get(worst["metric"], "latency")


def _critical_breach(
    observed: float,
    direction: str,
    target: float,
    spec: dict[str, Any],
) -> bool:
    """Optional hard break threshold for capacity testing.

    A normal max/min miss means the phase is degraded. A critical miss means the
    workload has reached a break point even if other metrics are still healthy.
    """
    if direction == "max":
        if "critical_max" in spec:
            return observed >= _number(spec["critical_max"], "critical_max")
        if "critical_multiplier" in spec:
            return observed >= target * _number(spec["critical_multiplier"], "critical_multiplier")
    else:
        if "critical_min" in spec:
            return observed <= _number(spec["critical_min"], "critical_min")
        if "critical_multiplier" in spec:
            multiplier = _number(spec["critical_multiplier"], "critical_multiplier")
            return multiplier != 0 and observed <= target / multiplier
    return False


def _series_missing(snapshot: MetricSnapshot, metric: str) -> bool:
    series = snapshot.series.get(metric)
    return series is None or series.length() == 0


def _profile(workload_name: str, config: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("profiles", {})
    if workload_name not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise WorkloadConfigError(
            f"unknown workload '{workload_name}'. available: {available}"
        )
    return profiles[workload_name] or {}


def _number(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise WorkloadConfigError(f"workload field {field} must be numeric") from exc
