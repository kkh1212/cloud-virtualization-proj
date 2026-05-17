from __future__ import annotations

from abc import ABC, abstractmethod

from analyzer.schemas import MetricSnapshot, RuleResult


class Rule(ABC):
    id: str = ""
    required_metrics: list[str] = []

    def applies(self, snapshot: MetricSnapshot) -> bool:
        return all(
            metric in snapshot.series and snapshot.series[metric].length() > 0
            for metric in self.required_metrics
        )

    @abstractmethod
    def evaluate(self, snapshot: MetricSnapshot, config: dict) -> RuleResult:
        ...
