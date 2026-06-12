from __future__ import annotations

from analyzer.rules.base import Rule
from analyzer.schemas import MetricSnapshot, RuleResult, TimeSeries


class GpuScheduling(Rule):
    id = "gpu_scheduling"
    required_metrics = [
        "gpu_utilization",
        "replicas_desired",
        "replicas_ready",
        "pod_pending_count",
    ]

    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        desired = snapshot.series["replicas_desired"]
        ready = snapshot.series["replicas_ready"]
        pending = snapshot.series["pod_pending_count"]
        pending_min = float(config.get("pod_pending_min", 1))
        duration_min = float(config.get("duration_min_seconds", 30))

        max_pending_duration = _max_pending_duration_seconds(pending, pending_min)
        max_ready_gap = max(
            (_value_at(desired, ts) - _value_at(ready, ts) for ts in _timestamps(desired, ready)),
            default=0.0,
        )
        triggered = (
            pending.max() >= pending_min
            and max_ready_gap > 0
            and max_pending_duration >= duration_min
        )
        return RuleResult(
            rule_id=self.id,
            triggered=triggered,
            severity="critical" if triggered else "info",
            evidence={
                "max_pending_pods": pending.max(),
                "max_desired_minus_ready": max_ready_gap,
                "max_pending_duration_seconds": max_pending_duration,
                "pod_pending_min": pending_min,
                "duration_min_seconds": duration_min,
            },
            suggestion="GPU scheduling is blocked. Check allocatable GPU resources, node taints, affinity, and device plugin health.",
        )


def _timestamps(*series: TimeSeries):
    return sorted({ts for item in series for ts, _ in item.points})


def _value_at(series: TimeSeries, ts) -> float:
    value = series.first()
    for point_ts, point_value in series.points:
        if point_ts <= ts:
            value = point_value
        else:
            break
    return value


def _max_pending_duration_seconds(pending: TimeSeries, pending_min: float) -> float:
    run_start = None
    run_last = None
    max_duration = 0.0
    for ts, value in pending.points:
        if value >= pending_min:
            if run_start is None:
                run_start = ts
            run_last = ts
            continue
        if run_start is not None and run_last is not None:
            max_duration = max(max_duration, (run_last - run_start).total_seconds())
        run_start = None
        run_last = None
    if run_start is not None and run_last is not None:
        max_duration = max(max_duration, (run_last - run_start).total_seconds())
    return max_duration
