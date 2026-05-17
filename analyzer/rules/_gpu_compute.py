from __future__ import annotations

from analyzer.rules.base import Rule
from analyzer.schemas import MetricSnapshot, RuleResult


class GpuCompute(Rule):
    id = "gpu_compute"
    required_metrics = ["gpu_utilization", "p95_latency"]

    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        gpu_util = snapshot.series["gpu_utilization"]
        p95 = snapshot.series["p95_latency"]
        gpu_util_min = float(config.get("gpu_util_min", 0.85))
        p95_min = float(config.get("p95_min_seconds", 2.0))
        mean_gpu = gpu_util.mean()
        max_p95 = p95.max()
        triggered = mean_gpu > gpu_util_min and max_p95 > p95_min
        return RuleResult(
            rule_id=self.id,
            triggered=triggered,
            severity="critical" if triggered else "info",
            evidence={
                "mean_gpu_utilization": mean_gpu,
                "max_gpu_utilization": gpu_util.max(),
                "max_p95_seconds": max_p95,
                "gpu_util_min": gpu_util_min,
                "p95_min_seconds": p95_min,
            },
            suggestion="GPU 연산 자원 부족. 모델 인스턴스 증설 또는 batching tuning",
        )
