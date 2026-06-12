from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analyzer.rules import (
    CpuBottleneck,
    HpaLimitation,
    QueueBottleneck,
    ScaleOutLag,
)
from analyzer.rules._gpu_compute import GpuCompute
from analyzer.rules._gpu_memory import GpuMemory
from analyzer.rules._gpu_scheduling import GpuScheduling
from analyzer.main import _build_report
from analyzer.schemas import MetricSnapshot, TimeSeries


BASE = datetime(2026, 5, 12, 7, 0, tzinfo=timezone.utc)


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


def test_queue_bottleneck_triggers():
    snap = snapshot(
        requests_waiting=ts("requests_waiting", [0, 2, 8, 9]),
        p95_latency=ts("p95_latency", [0.4, 1.0, 2.5, 3.0]),
    )

    result = QueueBottleneck().evaluate(
        snap,
        {"waiting_min": 5, "p95_min_seconds": 2.0},
    )

    assert result.triggered is True
    assert result.evidence["max_waiting"] == 9


def test_queue_bottleneck_no_trigger_when_latency_low():
    snap = snapshot(
        requests_waiting=ts("requests_waiting", [0, 8, 9]),
        p95_latency=ts("p95_latency", [0.3, 0.8, 1.2]),
    )

    result = QueueBottleneck().evaluate(
        snap,
        {"waiting_min": 5, "p95_min_seconds": 2.0},
    )

    assert result.triggered is False


def test_rule_applies_false_for_empty_series():
    snap = snapshot(
        requests_waiting=ts("requests_waiting", []),
        p95_latency=ts("p95_latency", [3.0]),
    )

    assert QueueBottleneck().applies(snap) is False
    assert GpuCompute().applies(snap) is False


def test_gpu_scheduling_skips_without_gpu_metrics():
    snap = snapshot(
        replicas_desired=ts("replicas_desired", [4, 4, 4]),
        replicas_ready=ts("replicas_ready", [2, 2, 2]),
        pod_pending_count=ts("pod_pending_count", [1, 1, 1]),
    )

    assert GpuScheduling().applies(snap) is False


def test_gpu_compute_triggers_with_gpu_metric_fixture():
    snap = snapshot(
        gpu_utilization=ts("gpu_utilization", [0.90, 0.95, 0.92]),
        p95_latency=ts("p95_latency", [1.0, 2.4, 2.8]),
    )

    assert GpuCompute().applies(snap) is True

    result = GpuCompute().evaluate(
        snap,
        {"gpu_util_min": 0.85, "p95_min_seconds": 2.0},
    )

    assert result.triggered is True
    assert result.evidence["mean_gpu_utilization"] > 0.85


def test_gpu_memory_triggers_with_gpu_metric_fixture():
    snap = snapshot(
        gpu_memory_used_ratio=ts("gpu_memory_used_ratio", [0.88, 0.91, 0.93]),
        p95_latency=ts("p95_latency", [1.5, 2.5, 2.7]),
    )

    assert GpuMemory().applies(snap) is True

    result = GpuMemory().evaluate(
        snap,
        {"gpu_mem_ratio_min": 0.85, "p95_min_seconds": 2.0},
    )

    assert result.triggered is True
    assert result.evidence["mean_gpu_memory_used_ratio"] > 0.85


def test_cpu_bottleneck_triggers():
    snap = snapshot(
        cpu_usage_ratio=ts("cpu_usage_ratio", [0.9, 0.95, 0.92]),
        p95_latency=ts("p95_latency", [1.7, 1.9, 2.0]),
    )

    result = CpuBottleneck().evaluate(
        snap,
        {"cpu_ratio_min": 0.85, "p95_min_seconds": 1.5},
    )

    assert result.triggered is True


def test_cpu_bottleneck_no_trigger_when_cpu_low():
    snap = snapshot(
        cpu_usage_ratio=ts("cpu_usage_ratio", [0.25, 0.35, 0.40]),
        p95_latency=ts("p95_latency", [2.0, 2.3, 2.1]),
    )

    result = CpuBottleneck().evaluate(
        snap,
        {"cpu_ratio_min": 0.85, "p95_min_seconds": 1.5},
    )

    assert result.triggered is False


def test_hpa_limitation_triggers_when_cpu_low_queue_high_and_replicas_static():
    # Fixture has 5 points at 15s steps → waiting > 5 holds across
    # BASE+15..BASE+60 (45s), which exceeds duration_min_seconds=30.
    snap = snapshot(
        cpu_usage_ratio=ts("cpu_usage_ratio", [0.25, 0.30, 0.35, 0.40, 0.30]),
        requests_waiting=ts("requests_waiting", [0, 7, 12, 15, 10]),
        p95_latency=ts("p95_latency", [1.2, 2.5, 3.0, 2.8, 2.0]),
        replicas_desired=ts("replicas_desired", [2, 2, 2, 2, 2]),
    )

    result = HpaLimitation().evaluate(
        snap,
        {
            "cpu_ratio_max": 0.50,
            "waiting_min": 5,
            "p95_min_seconds": 2.0,
            "duration_min_seconds": 30,
        },
    )

    assert result.triggered is True
    assert result.evidence["max_waiting_duration_seconds"] >= 30


def test_hpa_limitation_no_trigger_when_hpa_scaled():
    snap = snapshot(
        cpu_usage_ratio=ts("cpu_usage_ratio", [0.25, 0.30, 0.35, 0.40, 0.30]),
        requests_waiting=ts("requests_waiting", [0, 7, 12, 15, 10]),
        p95_latency=ts("p95_latency", [1.2, 2.5, 3.0, 2.8, 2.0]),
        replicas_desired=ts("replicas_desired", [2, 3, 4, 4, 4]),
    )

    result = HpaLimitation().evaluate(
        snap,
        {
            "cpu_ratio_max": 0.50,
            "waiting_min": 5,
            "p95_min_seconds": 2.0,
            "duration_min_seconds": 30,
        },
    )

    assert result.triggered is False


def test_hpa_limitation_no_trigger_when_waiting_spike_too_short():
    # waiting > 5 only at single point BASE+15 → duration ~0s, below 30s threshold.
    # Guards against transient-spike false positives.
    snap = snapshot(
        cpu_usage_ratio=ts("cpu_usage_ratio", [0.25, 0.30, 0.35, 0.40, 0.30]),
        requests_waiting=ts("requests_waiting", [0, 9, 2, 1, 0]),
        p95_latency=ts("p95_latency", [1.2, 2.5, 1.0, 1.0, 1.0]),
        replicas_desired=ts("replicas_desired", [2, 2, 2, 2, 2]),
    )

    result = HpaLimitation().evaluate(
        snap,
        {
            "cpu_ratio_max": 0.50,
            "waiting_min": 5,
            "p95_min_seconds": 2.0,
            "duration_min_seconds": 30,
        },
    )

    assert result.triggered is False


def test_scale_out_lag_triggers_when_ready_lags_for_duration():
    snap = snapshot(
        replicas_desired=ts("replicas_desired", [4, 4, 4, 4]),
        replicas_ready=ts("replicas_ready", [2, 2, 2, 4]),
        p95_latency=ts("p95_latency", [1.0, 2.0, 2.2, 1.0]),
    )

    result = ScaleOutLag().evaluate(
        snap,
        {
            "desired_minus_ready_min": 1,
            "duration_min_seconds": 30,
            "p95_min_seconds": 1.5,
        },
    )

    assert result.triggered is True
    assert result.evidence["max_gap_duration_seconds"] >= 30


def test_scale_out_lag_no_trigger_when_gap_is_short():
    snap = snapshot(
        replicas_desired=ts("replicas_desired", [4, 4, 4, 4]),
        replicas_ready=ts("replicas_ready", [2, 4, 4, 4]),
        p95_latency=ts("p95_latency", [1.0, 2.0, 2.2, 1.0]),
    )

    result = ScaleOutLag().evaluate(
        snap,
        {
            "desired_minus_ready_min": 1,
            "duration_min_seconds": 30,
            "p95_min_seconds": 1.5,
        },
    )

    assert result.triggered is False


def test_report_uses_avg_latency_metric_when_present():
    snap = snapshot(
        requests_total=ts("requests_total", [10, 20]),
        avg_latency=ts("avg_latency", [0.2, 0.4]),
    )

    report = _build_report("short_prompt", snap, [])

    assert report.performance["avg_latency_seconds"] == 0.30000000000000004
