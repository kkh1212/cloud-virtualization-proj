from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analyzer.main import _build_report
from analyzer.recommend import build_recommendations
from analyzer.report import render_markdown
from analyzer.schemas import MetricSnapshot, RuleResult, TimeSeries

BASE = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)

CONFIG = {
    "current": {
        "replicas_min": 2,
        "replicas_max": 8,
        "cpu_request_millicores": 200,
        "cpu_limit_millicores": 1000,
        "memory_request_mib": 256,
        "memory_limit_mib": 512,
        "max_concurrency": 4,
        "hpa_cpu_target_pct": 60,
        "keda_queue_threshold": 20,
    },
    "tuning": {},
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


def rule(rule_id: str) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        triggered=True,
        severity="warning",
        evidence={},
        suggestion="x",
    )


def _by_target(recs: list[dict]) -> dict[str, dict]:
    return {r["target"]: r for r in recs}


def test_cpu_request_downsized_when_overprovisioned():
    snap = snapshot(cpu_usage_ratio=ts("cpu_usage_ratio", [0.05, 0.04]))
    recs = _by_target(build_recommendations(snap, [], None, CONFIG))
    assert "container.requests.cpu" in recs
    # peak 0.05*200=10m, *1.3=13m, floored to 50m
    assert recs["container.requests.cpu"]["recommended"] == "50m"
    assert recs["container.requests.cpu"]["current"] == "200m"


def test_no_cpu_recommendation_when_well_sized():
    # ratio ~0.77 -> rec ≈ current 200m, change below significance threshold
    snap = snapshot(cpu_usage_ratio=ts("cpu_usage_ratio", [0.77]))
    recs = _by_target(build_recommendations(snap, [], None, CONFIG))
    assert "container.requests.cpu" not in recs


def test_hpa_limitation_recommends_keda_mode():
    snap = snapshot(requests_waiting=ts("requests_waiting", [50]))
    recs = _by_target(build_recommendations(snap, [rule("hpa_limitation")], None, CONFIG))
    assert recs["autoscaler.mode"]["recommended"].startswith("KEDA")


def test_queue_bottleneck_with_spare_cpu_raises_concurrency():
    snap = snapshot(
        requests_waiting=ts("requests_waiting", [40]),
        cpu_usage_ratio=ts("cpu_usage_ratio", [0.1]),
    )
    recs = _by_target(build_recommendations(snap, [rule("queue_bottleneck")], None, CONFIG))
    assert recs["env.MOCK_LLM_MAX_CONCURRENCY"]["recommended"] == "8"  # 4 * 2


def test_cpu_bound_queue_does_not_raise_concurrency():
    snap = snapshot(
        requests_waiting=ts("requests_waiting", [40]),
        cpu_usage_ratio=ts("cpu_usage_ratio", [0.9]),
    )
    recs = _by_target(
        build_recommendations(snap, [rule("queue_bottleneck"), rule("cpu_bottleneck")], None, CONFIG)
    )
    assert "env.MOCK_LLM_MAX_CONCURRENCY" not in recs


def test_replica_max_raised_when_capped_and_breaching():
    snap = snapshot(
        replicas_desired=ts("replicas_desired", [8, 8]),
        requests_waiting=ts("requests_waiting", [30]),
    )
    recs = _by_target(build_recommendations(snap, [rule("queue_bottleneck")], None, CONFIG))
    assert recs["autoscaler.maxReplicaCount"]["recommended"] == "12"  # ceil(8*1.5)


def test_healthy_run_yields_no_recommendations():
    assert build_recommendations(snapshot(), [], None, CONFIG) == []


def test_report_renders_recommendation_section():
    snap = snapshot()
    report = _build_report(
        "burst_traffic",
        snap,
        [],
        recommendations=[
            {
                "target": "container.requests.cpu",
                "current": "200m",
                "recommended": "50m",
                "rationale": "over-provisioned",
            }
        ],
    )
    md = render_markdown(report)
    assert "## 9. 권장 설정" in md
    assert "## 10. 개선 방향" in md
    assert "container.requests.cpu" in md
