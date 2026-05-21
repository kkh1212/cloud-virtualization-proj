from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analyzer.cost import build_cost_estimate
from analyzer.main import _build_report
from analyzer.report import render_json, render_markdown
from analyzer.schemas import MetricSnapshot, TimeSeries

BASE = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


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


def test_build_cost_estimate_from_manual_profile():
    snap = snapshot(
        replicas_ready=ts("replicas_ready", [2, 4, 4]),
        requests_total=ts("requests_total", [10, 20, 30]),
        prompt_token_rate=ts("prompt_token_rate", [100, 100]),
        output_token_rate=ts("output_token_rate", [200, 200]),
    )

    cost = build_cost_estimate(
        snap,
        "custom",
        {
            "profiles": {
                "custom": {
                    "currency": "USD",
                    "hourly_per_mock_llm_replica": 0.30,
                    "hourly_cluster_overhead": 0.10,
                    "hourly_gpu_node": 0.0,
                }
            }
        },
    )

    assert cost["profile"] == "custom"
    assert cost["avg_billable_replicas"] == (2 + 4 + 4) / 3
    assert cost["estimated_requests"] == 2400
    assert cost["estimated_tokens"] == 36000
    assert cost["estimated_run_cost"] > 0
    assert cost["cost_per_1k_requests"] is not None
    assert cost["cost_per_1k_tokens"] is not None


def test_cost_estimate_handles_zero_denominators():
    snap = snapshot(replicas_ready=ts("replicas_ready", [0]))

    cost = build_cost_estimate(
        snap,
        "custom",
        {
            "profiles": {
                "custom": {
                    "currency": "USD",
                    "hourly_per_mock_llm_replica": 1.0,
                }
            }
        },
    )

    assert cost["estimated_requests"] == 0
    assert cost["estimated_tokens"] == 0
    assert cost["cost_per_1k_requests"] is None
    assert cost["cost_per_1k_tokens"] is None


def test_report_renders_cost_section_and_json():
    snap = snapshot(requests_total=ts("requests_total", [1]), replicas_ready=ts("replicas_ready", [1]))
    report = _build_report(
        "burst_traffic",
        snap,
        [],
        cost={
            "profile": "custom",
            "currency": "USD",
            "estimated_run_cost": 0.123456,
            "cost_per_1k_requests": None,
            "cost_per_1k_tokens": None,
            "avg_billable_replicas": 1.0,
            "estimated_tokens": 0.0,
        },
    )

    md = render_markdown(report)
    js = render_json(report)

    assert "## 6. 비용 추정" in md
    assert "custom" in md
    assert '"cost"' in js
