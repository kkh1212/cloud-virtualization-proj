from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class TimeSeries(BaseModel):
    name: str
    points: list[tuple[datetime, float]]

    def max(self) -> float:
        return max((value for _, value in self.points), default=0.0)

    def min(self) -> float:
        return min((value for _, value in self.points), default=0.0)

    def mean(self) -> float:
        if not self.points:
            return 0.0
        return sum(value for _, value in self.points) / len(self.points)

    def last(self) -> float:
        if not self.points:
            return 0.0
        return self.points[-1][1]

    def first(self) -> float:
        if not self.points:
            return 0.0
        return self.points[0][1]

    def length(self) -> int:
        return len(self.points)


class MetricSnapshot(BaseModel):
    time_range: tuple[datetime, datetime]
    series: dict[str, TimeSeries]


class RuleResult(BaseModel):
    rule_id: str
    triggered: bool
    severity: Literal["info", "warning", "critical"]
    evidence: dict[str, Any]
    suggestion: str


class Report(BaseModel):
    scenario: str
    time_range: tuple[datetime, datetime]
    summary: dict[str, Any]
    performance: dict[str, Any]
    llm_state: dict[str, Any]
    k8s_state: dict[str, Any]
    resource_state: dict[str, Any]
    cost: dict[str, Any] | None = None
    slo: dict[str, Any] | None = None
    recommendations: list[dict[str, Any]] = []
    diagnosis: list[RuleResult]
    improvements: list[str]
