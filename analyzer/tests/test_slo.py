from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.main import _build_report
from analyzer.report import render_json, render_markdown
from analyzer.schemas import MetricSnapshot, TimeSeries
from analyzer.slo import (
    SLOConfigError,
    build_slo_evaluation,
    slo_breach_result,
)

BASE = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)

CONFIG = {
    "profiles": {
        "default": {
            "p95_latency_seconds": 3.0,
            "p99_latency_seconds": 5.0,
            "ttft_p95_seconds": 1.0,
            "error_rate_max": 0.05,
        }
    }
}


def ts(name: str, values: list[float], step_seconds: int = 15) -> TimeSeries:
    return TimeSeries(
        name=name,
        points=[
            (BASE + timedelta(seconds=index * step_seconds), value)
            for index, value in enumerate(values)
        ],
    )


def snapshot(**series: TimeSeries) -> MetricSnapshot:
    return MetricSnapshot(
        time_range=(BASE, BASE + timedelta(minutes=2)),
        series=dict(series),
    )


def test_slo_breach_detected_and_severity_critical():
    snap = snapshot(
        p95_latency=ts("p95_latency", [2.0, 5.0]),  # peak 5.0 > 3.0
        p99_latency=ts("p99_latency", [6.0]),        # 6.0 > 5.0
        error_rate=ts("error_rate", [1.0]),
        requests_total=ts("requests_total", [10.0]),  # fraction 0.1 > 0.05
    )

    evaluation = build_slo_evaluation(snap, "default", CONFIG)
    assert evaluation["met"] is False
    breached = {c["metric"] for c in evaluation["checks"] if not c["met"]}
    assert breached == {"p95_latency_seconds", "p99_latency_seconds", "error_rate_max"}

    result = slo_breach_result(evaluation)
    assert result is not None
    assert result.rule_id == "slo_breach"
    assert result.severity == "critical"  # error fraction overshoots target by 100%


def test_slo_all_met_yields_no_breach():
    snap = snapshot(
        p95_latency=ts("p95_latency", [1.0]),
        p99_latency=ts("p99_latency", [2.0]),
        error_rate=ts("error_rate", [0.0]),
        requests_total=ts("requests_total", [10.0]),
    )

    evaluation = build_slo_evaluation(snap, "default", CONFIG)
    assert evaluation["met"] is True
    assert slo_breach_result(evaluation) is None


def test_absent_metric_is_skipped_not_failed():
    # Only p95 present; ttft/p99/error targets have no matching metric -> skipped.
    snap = snapshot(p95_latency=ts("p95_latency", [1.0]))
    evaluation = build_slo_evaluation(snap, "default", CONFIG)
    metrics = {c["metric"] for c in evaluation["checks"]}
    assert metrics == {"p95_latency_seconds"}
    assert evaluation["met"] is True


def test_unknown_profile_raises():
    with pytest.raises(SLOConfigError):
        build_slo_evaluation(snapshot(), "nope", CONFIG)


def test_report_renders_slo_section_and_json():
    snap = snapshot(p95_latency=ts("p95_latency", [5.0]))
    evaluation = build_slo_evaluation(snap, "default", CONFIG)
    report = _build_report("burst_traffic", snap, [], slo=evaluation)

    md = render_markdown(report)
    js = render_json(report)

    assert "## 7. SLO 판정" in md
    assert "BREACH" in md
    assert '"slo"' in js
