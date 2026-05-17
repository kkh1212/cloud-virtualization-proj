from __future__ import annotations

from analyzer.rules.base import Rule
from analyzer.schemas import MetricSnapshot, RuleResult


class QueueBottleneck(Rule):
    id = "queue_bottleneck"
    required_metrics = ["requests_waiting", "p95_latency"]

    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        waiting = snapshot.series["requests_waiting"]
        p95 = snapshot.series["p95_latency"]
        waiting_min = float(config.get("waiting_min", 5))
        p95_min_seconds = float(config.get("p95_min_seconds", 2.0))
        max_waiting = waiting.max()
        max_p95 = p95.max()
        triggered = max_waiting > waiting_min and max_p95 > p95_min_seconds
        return RuleResult(
            rule_id=self.id,
            triggered=triggered,
            severity="warning" if triggered else "info",
            evidence={
                "max_waiting": max_waiting,
                "max_p95_seconds": max_p95,
                "waiting_min": waiting_min,
                "p95_min_seconds": p95_min_seconds,
            },
            suggestion=(
                "유입 RPS 가 처리 capacity 를 넘어섭니다. "
                "(a) replicas 또는 max_concurrency 상향, "
                "(b) queue 기반 autoscaling 도입, "
                "(c) max_tokens 제한 검토"
            ),
        )
