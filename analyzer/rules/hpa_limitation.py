from __future__ import annotations

from analyzer.rules.base import Rule
from analyzer.schemas import MetricSnapshot, RuleResult, TimeSeries


class HpaLimitation(Rule):
    id = "hpa_limitation"
    required_metrics = [
        "cpu_usage_ratio",
        "requests_waiting",
        "p95_latency",
        "replicas_desired",
    ]

    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        cpu_ratio = snapshot.series["cpu_usage_ratio"]
        waiting = snapshot.series["requests_waiting"]
        p95 = snapshot.series["p95_latency"]
        desired = snapshot.series["replicas_desired"]

        cpu_ratio_max = float(config.get("cpu_ratio_max", 0.50))
        waiting_min = float(config.get("waiting_min", 5))
        p95_min = float(config.get("p95_min_seconds", 2.0))
        duration_min = float(config.get("duration_min_seconds", 30))

        mean_cpu = cpu_ratio.mean()
        max_waiting = waiting.max()
        max_p95 = p95.max()
        desired_delta = desired.max() - desired.min()
        desired_unchanged = abs(desired_delta) < 1e-9
        # Require waiting > waiting_min to persist for at least duration_min
        # seconds, mirroring scale_out_lag's gap-duration check. Without this
        # a 1s transient spike could trigger the rule and produce noise.
        waiting_duration_seconds = _max_duration_above(waiting, waiting_min)
        triggered = (
            mean_cpu < cpu_ratio_max
            and waiting_duration_seconds >= duration_min
            and max_p95 > p95_min
            and desired_unchanged
        )
        return RuleResult(
            rule_id=self.id,
            triggered=triggered,
            severity="warning" if triggered else "info",
            evidence={
                "mean_cpu_usage_ratio": mean_cpu,
                "max_waiting": max_waiting,
                "max_waiting_duration_seconds": waiting_duration_seconds,
                "max_p95_seconds": max_p95,
                "replicas_desired_min": desired.min(),
                "replicas_desired_max": desired.max(),
                "replicas_desired_delta": desired_delta,
                "cpu_ratio_max": cpu_ratio_max,
                "waiting_min": waiting_min,
                "p95_min_seconds": p95_min,
                "duration_min_seconds": duration_min,
            },
            suggestion="CPU 기준 autoscaling 이 queue 부하를 못 잡습니다. queue-based custom metric 도입 검토",
        )


def _max_duration_above(series: TimeSeries, threshold: float) -> float:
    """Longest contiguous span (seconds) where the series value exceeds threshold."""
    run_start = None
    run_last = None
    max_duration = 0.0
    for ts, value in series.points:
        if value > threshold:
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
