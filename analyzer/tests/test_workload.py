from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.main import _build_report
from analyzer.report import render_json, render_markdown
from analyzer.schemas import MetricSnapshot, TimeSeries
from analyzer.workload import (
    WorkloadConfigError,
    build_workload_fit,
    workload_fit_result,
    workload_slo_profile,
)

BASE = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)

CONFIG = {
    "profiles": {
        "demo": {
            "description": "demo workload",
            "request_shape": "short in / short out",
            "slo_profile": "strict",
            "recommendations": {
                "queue": ["replica 증가"],
                "latency": ["작은 모델 검토"],
            },
            "thresholds": {
                "ttft_p95": {"max": 1.0, "agg": "peak", "weight": 2},
                "p95_latency": {"max": 3.0, "agg": "peak", "weight": 2},
                "requests_waiting": {"max": 5, "agg": "peak", "weight": 1},
                "output_token_rate": {"min": 15, "agg": "mean", "weight": 1},
                "gpu_utilization": {"min": 0.5, "agg": "mean", "weight": 1},  # GPU -> skip on mock
            },
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
    return MetricSnapshot(time_range=(BASE, BASE + timedelta(minutes=2)), series=dict(series))


def _all_pass() -> MetricSnapshot:
    return snapshot(
        ttft_p95=ts("ttft_p95", [0.3, 0.6]),
        p95_latency=ts("p95_latency", [1.0, 2.0]),
        requests_waiting=ts("requests_waiting", [0, 3]),
        output_token_rate=ts("output_token_rate", [20, 30]),
    )


def test_all_pass_is_suitable_score_100():
    fit = build_workload_fit(_all_pass(), "demo", CONFIG)
    assert fit["verdict"] == "suitable"
    assert fit["score"] == 100.0
    assert fit["bottleneck"] is None
    # gpu_utilization absent -> skipped, not failed
    assert fit["counts"] == {"evaluated": 4, "passed": 4, "failed": 0, "skipped": 1}


def test_one_fail_is_partially_suitable_with_weighted_score():
    snap = snapshot(
        ttft_p95=ts("ttft_p95", [0.5]),
        p95_latency=ts("p95_latency", [8.0]),  # fail (weight 2)
        requests_waiting=ts("requests_waiting", [2]),
        output_token_rate=ts("output_token_rate", [20]),
    )
    fit = build_workload_fit(snap, "demo", CONFIG)
    assert fit["verdict"] == "partially_suitable"
    # passed weight 4 (ttft2 + waiting1 + rate1) / total 6 -> 66.7
    assert fit["score"] == 66.7
    assert fit["bottleneck"] == "latency"


def test_critical_threshold_makes_single_metric_unsuitable():
    config = {
        "profiles": {
            "demo": {
                "thresholds": {
                    "p95_latency": {"max": 3.0, "critical_max": 8.0, "weight": 2},
                    "ttft_p95": {"max": 1.0, "weight": 1},
                }
            }
        }
    }
    fit = build_workload_fit(
        snapshot(
            p95_latency=ts("p95_latency", [9.0]),
            ttft_p95=ts("ttft_p95", [0.2]),
        ),
        "demo",
        config,
    )
    assert fit["verdict"] == "unsuitable"
    failed = [c for c in fit["checks"] if c["met"] is False]
    assert failed[0]["critical"] is True


def test_all_fail_is_unsuitable_score_0():
    snap = snapshot(
        ttft_p95=ts("ttft_p95", [5.0]),
        p95_latency=ts("p95_latency", [9.0]),
        requests_waiting=ts("requests_waiting", [50]),
        output_token_rate=ts("output_token_rate", [1.0]),
    )
    fit = build_workload_fit(snap, "demo", CONFIG)
    assert fit["verdict"] == "unsuitable"
    assert fit["score"] == 0.0
    # waiting overshoot (50-5)/5=9 dominates -> queue bottleneck
    assert fit["bottleneck"] == "queue"


def test_no_evaluable_metric_yields_none_verdict():
    fit = build_workload_fit(snapshot(), "demo", CONFIG)
    assert fit["verdict"] is None
    assert fit["score"] is None
    assert fit["counts"]["evaluated"] == 0


def test_unknown_workload_raises():
    with pytest.raises(WorkloadConfigError):
        build_workload_fit(snapshot(), "nope", CONFIG)


def test_slo_profile_lookup():
    assert workload_slo_profile("demo", CONFIG) == "strict"


def test_workload_fit_result_severity():
    unsuitable = build_workload_fit(
        snapshot(p95_latency=ts("p95_latency", [9.0])), "demo", CONFIG
    )
    result = workload_fit_result(unsuitable)
    assert result is not None
    assert result.rule_id == "workload_fit"
    assert result.severity == "critical"
    # all-pass -> no diagnosis entry
    assert workload_fit_result(build_workload_fit(_all_pass(), "demo", CONFIG)) is None


def test_real_config_profiles_load_and_judge():
    # Sanity check the shipped config parses and judges for every workload.
    from pathlib import Path

    from analyzer.workload import load_workload_config

    cfg = load_workload_config(
        Path(__file__).resolve().parents[1] / "config" / "workload-profiles.yaml"
    )
    for name in cfg["profiles"]:
        fit = build_workload_fit(snapshot(p95_latency=ts("p95_latency", [2.0])), name, cfg)
        assert fit["workload"] == name
        assert "verdict" in fit


def test_report_renders_workload_section_and_json():
    fit = build_workload_fit(
        snapshot(p95_latency=ts("p95_latency", [9.0])), "demo", CONFIG
    )
    report = _build_report("short_prompt", snapshot(), [], workload_fit=fit)
    md = render_markdown(report)
    js = render_json(report)
    assert "## 11. 워크로드 부하 기준 판정" in md
    assert "기준 미통과" in md
    assert '"workload_fit"' in js
