from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from analyzer.schemas import MetricSnapshot, TimeSeries


class CostConfigError(ValueError):
    pass


def load_cost_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("profiles"), dict):
        raise CostConfigError(f"cost config {path} must contain a profiles mapping")
    return data


def build_cost_estimate(
    snapshot: MetricSnapshot,
    profile_name: str,
    cost_config: dict[str, Any],
) -> dict[str, Any]:
    profiles = cost_config.get("profiles", {})
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise CostConfigError(
            f"unknown cost profile '{profile_name}'. available profiles: {available}"
        )

    profile = profiles[profile_name] or {}
    duration_seconds = max(
        (snapshot.time_range[1] - snapshot.time_range[0]).total_seconds(), 0.0
    )
    duration_hours = duration_seconds / 3600.0

    ready = _series(snapshot, "replicas_ready")
    desired = _series(snapshot, "replicas_desired")
    replica_source = ready if ready.length() else desired
    avg_replicas = replica_source.mean() if replica_source.length() else 0.0

    requests = _series(snapshot, "requests_total")
    estimated_requests = (
        int(round(requests.mean() * duration_seconds)) if requests.length() else 0
    )

    prompt_tokens = _rate_total(_series(snapshot, "prompt_token_rate"), duration_seconds)
    output_tokens = _rate_total(_series(snapshot, "output_token_rate"), duration_seconds)
    estimated_tokens = prompt_tokens + output_tokens

    hourly_per_replica = _non_negative_float(
        profile.get("hourly_per_mock_llm_replica", 0.0),
        "hourly_per_mock_llm_replica",
    )
    hourly_cluster_overhead = _non_negative_float(
        profile.get("hourly_cluster_overhead", 0.0),
        "hourly_cluster_overhead",
    )
    hourly_gpu_node = _non_negative_float(
        profile.get("hourly_gpu_node", 0.0),
        "hourly_gpu_node",
    )

    estimated_run_cost = duration_hours * (
        avg_replicas * hourly_per_replica
        + hourly_cluster_overhead
        + hourly_gpu_node
    )

    return {
        "profile": profile_name,
        "currency": str(profile.get("currency", "USD")),
        "duration_hours": duration_hours,
        "avg_billable_replicas": avg_replicas,
        "hourly_per_mock_llm_replica": hourly_per_replica,
        "hourly_cluster_overhead": hourly_cluster_overhead,
        "hourly_gpu_node": hourly_gpu_node,
        "estimated_requests": estimated_requests,
        "estimated_tokens": estimated_tokens,
        "estimated_run_cost": estimated_run_cost,
        "cost_per_1k_requests": _per_1k(estimated_run_cost, estimated_requests),
        "cost_per_1k_tokens": _per_1k(estimated_run_cost, estimated_tokens),
        "notes": profile.get("notes", ""),
    }


def _series(snapshot: MetricSnapshot, name: str) -> TimeSeries:
    return snapshot.series.get(name, TimeSeries(name=name, points=[]))


def _rate_total(series: TimeSeries, duration_seconds: float) -> float:
    if series.length() == 0:
        return 0.0
    return max(series.mean() * duration_seconds, 0.0)


def _per_1k(cost: float, denominator: float | int) -> float | None:
    if denominator <= 0:
        return None
    return cost / float(denominator) * 1000.0


def _non_negative_float(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CostConfigError(f"cost field {field} must be numeric") from exc
    if parsed < 0:
        raise CostConfigError(f"cost field {field} must be non-negative")
    return parsed
