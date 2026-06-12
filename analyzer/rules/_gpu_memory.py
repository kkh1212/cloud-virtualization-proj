from __future__ import annotations

from analyzer.rules.base import Rule
from analyzer.schemas import MetricSnapshot, RuleResult


class GpuMemory(Rule):
    id = "gpu_memory"
    required_metrics = ["gpu_memory_used_ratio", "p95_latency"]

    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        gpu_memory = snapshot.series["gpu_memory_used_ratio"]
        p95 = snapshot.series["p95_latency"]
        gpu_mem_ratio_min = float(config.get("gpu_mem_ratio_min", 0.85))
        p95_min = float(config.get("p95_min_seconds", 2.0))
        mean_gpu_memory = gpu_memory.mean()
        max_p95 = p95.max()
        triggered = mean_gpu_memory > gpu_mem_ratio_min and max_p95 > p95_min
        return RuleResult(
            rule_id=self.id,
            triggered=triggered,
            severity="critical" if triggered else "info",
            evidence={
                "mean_gpu_memory_used_ratio": mean_gpu_memory,
                "max_gpu_memory_used_ratio": gpu_memory.max(),
                "max_p95_seconds": max_p95,
                "gpu_mem_ratio_min": gpu_mem_ratio_min,
                "p95_min_seconds": p95_min,
            },
            suggestion="GPU memory or KV cache pressure is high. Consider shorter contexts, quantization, or a larger GPU.",
        )
