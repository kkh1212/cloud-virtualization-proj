from __future__ import annotations

from analyzer.rules.base import Rule
from analyzer.schemas import MetricSnapshot, RuleResult


class CpuBottleneck(Rule):
    id = "cpu_bottleneck"
    required_metrics = ["cpu_usage_ratio", "p95_latency"]

    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        cpu_ratio = snapshot.series["cpu_usage_ratio"]
        p95 = snapshot.series["p95_latency"]
        cpu_ratio_min = float(config.get("cpu_ratio_min", 0.85))
        p95_min = float(config.get("p95_min_seconds", 1.5))
        mean_cpu = cpu_ratio.mean()
        max_cpu = cpu_ratio.max()
        max_p95 = p95.max()
        triggered = mean_cpu > cpu_ratio_min and max_p95 > p95_min
        return RuleResult(
            rule_id=self.id,
            triggered=triggered,
            severity="critical" if triggered else "info",
            evidence={
                "mean_cpu_usage_ratio": mean_cpu,
                "max_cpu_usage_ratio": max_cpu,
                "max_p95_seconds": max_p95,
                "cpu_ratio_min": cpu_ratio_min,
                "p95_min_seconds": p95_min,
            },
            suggestion="CPU 자원 부족. cpu requests/limits 상향 또는 replicas 증가",
        )
