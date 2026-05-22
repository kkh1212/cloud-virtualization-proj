"""SLO evaluation: does a run meet its latency / error targets?

Single source of SLO truth is analyzer/config/slo.yaml (profiles), mirroring
the cost-profile mechanism. build_slo_evaluation() produces the dict rendered
in the report's SLO section; slo_breach_result() turns a breach into a
RuleResult so it also flows into the diagnosis table and improvements list.

A target whose underlying metric is absent from the snapshot is skipped (not
failed) — same gating philosophy as Rule.required_metrics.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from analyzer.schemas import MetricSnapshot, RuleResult, TimeSeries

# SLO target key -> snapshot metric whose peak (max) is the observed value.
# All of these are upper-bound ("lower is better") latency targets.
_LATENCY_TARGETS: tuple[tuple[str, str], ...] = (
    ("p95_latency_seconds", "p95_latency"),
    ("p99_latency_seconds", "p99_latency"),
    ("ttft_p95_seconds", "ttft_p95"),
)


class SLOConfigError(ValueError):
    pass


def load_slo_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("profiles"), dict):
        raise SLOConfigError(f"slo config {path} must contain a profiles mapping")
    return data


def build_slo_evaluation(
    snapshot: MetricSnapshot,
    profile_name: str,
    slo_config: dict[str, Any],
) -> dict[str, Any]:
    profiles = slo_config.get("profiles", {})
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise SLOConfigError(
            f"unknown slo profile '{profile_name}'. available profiles: {available}"
        )

    profile = profiles[profile_name] or {}
    checks: list[dict[str, Any]] = []

    for target_key, metric_name in _LATENCY_TARGETS:
        if target_key not in profile:
            continue
        series = _series(snapshot, metric_name)
        if series.length() == 0:
            continue
        target = _positive_float(profile[target_key], target_key)
        checks.append(_check_upper(target_key, target, series.max()))

    if "error_rate_max" in profile:
        errors = _series(snapshot, "error_rate")
        requests = _series(snapshot, "requests_total")
        if errors.length() and requests.length():
            request_rate = requests.mean()
            observed = errors.mean() / request_rate if request_rate > 0 else 0.0
            target = _non_negative_float(profile["error_rate_max"], "error_rate_max")
            checks.append(_check_upper("error_rate_max", target, observed))

    met: bool | None
    met = all(c["met"] for c in checks) if checks else None

    return {
        "profile": profile_name,
        "met": met,
        "checks": checks,
        "notes": profile.get("notes", ""),
    }


def slo_breach_result(slo_evaluation: dict[str, Any]) -> RuleResult | None:
    """Build a diagnosis entry for any breached SLO check, or None if all met."""
    breaches = [c for c in slo_evaluation.get("checks", []) if not c["met"]]
    if not breaches:
        return None

    worst_overshoot = max(
        ((c["observed"] - c["target"]) / c["target"]) if c["target"] else 0.0
        for c in breaches
    )
    severity = "critical" if worst_overshoot >= 0.5 else "warning"
    evidence = {
        c["metric"]: {"target": c["target"], "observed": round(c["observed"], 6)}
        for c in breaches
    }
    breached_metrics = ", ".join(c["metric"] for c in breaches)
    suggestion = (
        f"SLO 위반({breached_metrics}). 오토스케일링 임계값/replica 상향 또는 "
        "컨테이너 리소스 조정으로 목표 latency/error 예산 내로 복귀 필요."
    )
    return RuleResult(
        rule_id="slo_breach",
        triggered=True,
        severity=severity,
        evidence=evidence,
        suggestion=suggestion,
    )


def _check_upper(metric: str, target: float, observed: float) -> dict[str, Any]:
    margin_pct = ((target - observed) / target * 100.0) if target else None
    return {
        "metric": metric,
        "comparison": "<=",
        "target": float(target),
        "observed": float(observed),
        "met": observed <= target,
        "margin_pct": margin_pct,
    }


def _series(snapshot: MetricSnapshot, name: str) -> TimeSeries:
    return snapshot.series.get(name, TimeSeries(name=name, points=[]))


def _positive_float(value: Any, field: str) -> float:
    parsed = _non_negative_float(value, field)
    if parsed == 0:
        raise SLOConfigError(f"slo field {field} must be greater than zero")
    return parsed


def _non_negative_float(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SLOConfigError(f"slo field {field} must be numeric") from exc
    if parsed < 0:
        raise SLOConfigError(f"slo field {field} must be non-negative")
    return parsed
