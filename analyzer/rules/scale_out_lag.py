from __future__ import annotations

from analyzer.rules.base import Rule
from analyzer.schemas import MetricSnapshot, RuleResult, TimeSeries


class ScaleOutLag(Rule):
    id = "scale_out_lag"
    required_metrics = ["replicas_desired", "replicas_ready", "p95_latency"]

    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        desired = snapshot.series["replicas_desired"]
        ready = snapshot.series["replicas_ready"]
        p95 = snapshot.series["p95_latency"]
        gap_min = float(config.get("desired_minus_ready_min", 1))
        duration_min = float(config.get("duration_min_seconds", 30))
        p95_min = float(config.get("p95_min_seconds", 1.5))

        max_gap_duration = _max_gap_duration_seconds(desired, ready, gap_min)
        max_gap = max(
            (
                _value_at(desired, ts) - _value_at(ready, ts)
                for ts in _timestamps(desired, ready)
            ),
            default=0.0,
        )
        max_p95 = p95.max()
        triggered = max_gap_duration >= duration_min and max_p95 > p95_min
        return RuleResult(
            rule_id=self.id,
            triggered=triggered,
            severity="warning" if triggered else "info",
            evidence={
                "max_desired_minus_ready": max_gap,
                "max_gap_duration_seconds": max_gap_duration,
                "max_p95_seconds": max_p95,
                "desired_minus_ready_min": gap_min,
                "duration_min_seconds": duration_min,
                "p95_min_seconds": p95_min,
            },
            suggestion="신규 Pod readiness probe initialDelay/period 단축, 또는 image preload 검토",
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


def _max_gap_duration_seconds(
    desired: TimeSeries,
    ready: TimeSeries,
    gap_min: float,
) -> float:
    run_start = None
    run_last = None
    max_duration = 0.0
    for ts in _timestamps(desired, ready):
        gap = _value_at(desired, ts) - _value_at(ready, ts)
        if gap >= gap_min:
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
