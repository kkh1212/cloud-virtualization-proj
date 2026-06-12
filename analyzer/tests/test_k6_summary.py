from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analyzer.main import _build_report, _resolve_metrics_config, _summarize_k6
from analyzer.report import render_markdown
from analyzer.schemas import MetricSnapshot, TimeSeries

BASE = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


def ts(name: str, values: list[float]) -> TimeSeries:
    return TimeSeries(
        name=name,
        points=[
            (BASE + timedelta(seconds=index * 15), value)
            for index, value in enumerate(values)
        ],
    )


def snapshot(**series: TimeSeries) -> MetricSnapshot:
    return MetricSnapshot(
        time_range=(BASE, BASE + timedelta(minutes=2)),
        series=dict(series),
    )


def test_summarize_k6_extracts_user_facing_metrics_and_tags():
    summary = _summarize_k6(
        {
            "metrics": {
                "http_req_duration": {
                    "values": {"med": 120.0, "p(95)": 300.0, "p(99)": 500.0}
                },
                "http_req_failed": {"values": {"rate": 0.02}},
                "checks": {"values": {"rate": 0.98}},
                "http_reqs": {"values": {"count": 1234}},
                "vus": {"values": {"max": 16}},
                "vus_max": {"values": {"value": 32}},
                "http_req_duration{scenario_type:mixed,prompt_type:rag,output_type:medium_output}": {
                    "values": {"p(95)": 900.0}
                },
            }
        }
    )

    assert summary["http_req_duration_p50_ms"] == 120.0
    assert summary["http_req_duration_p95_ms"] == 300.0
    assert summary["http_req_duration_p99_ms"] == 500.0
    assert summary["http_req_failed_rate"] == 0.02
    assert summary["checks_success_rate"] == 0.98
    assert summary["request_count"] == 1234
    assert summary["vus_peak"] == 32
    assert summary["tagged_latency_p95_ms"]["mixed/rag/medium_output"] == 900.0


def test_report_renders_k6_and_extended_llm_metrics():
    snap = snapshot(
        queue_wait_p95=ts("queue_wait_p95", [0.1, 0.2]),
        prompt_tokens_p95=ts("prompt_tokens_p95", [100, 4000]),
        output_tokens_p95=ts("output_tokens_p95", [100, 1000]),
    )
    report = _build_report(
        "mixed_workload",
        snap,
        [],
        k6={
            "http_req_duration_p50_ms": 100.0,
            "http_req_duration_p95_ms": 250.0,
            "http_req_duration_p99_ms": 750.0,
            "http_req_failed_rate": 0.01,
            "checks_success_rate": 0.99,
            "request_count": 42,
            "vus_peak": 8,
            "tagged_latency_p95_ms": {"mixed/rag/medium_output": 800.0},
        },
    )

    md = render_markdown(report)

    assert "| k6 latency p95 | 250.0ms |" in md
    assert "| queue wait p95 (peak) | 0.200s |" in md
    assert "| prompt tokens/request p95 | 4000.000 |" in md
    assert "| mixed/rag/medium_output | 800.0ms |" in md


def test_report_renders_gpu_metrics_when_present():
    snap = snapshot(
        gpu_utilization=ts("gpu_utilization", [0.4, 0.8]),
        gpu_memory_used_ratio=ts("gpu_memory_used_ratio", [0.4, 0.6]),
    )
    report = _build_report("short_prompt", snap, [])

    md = render_markdown(report)

    assert "| GPU utilization avg | 60.00% |" in md
    assert "| GPU memory peak | 60.00% |" in md


def test_resolve_metrics_config_supports_vllm_profiles():
    for profile in (
        "metrics-vllm.yaml",
        "metrics-vllm-nvidia.yaml",
        "metrics-vllm-amd.yaml",
    ):
        path = _resolve_metrics_config(profile)

        assert path.name == profile
        assert path.exists()
