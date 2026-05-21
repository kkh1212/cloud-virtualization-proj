from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.compare import build_comparison, render_markdown
from analyzer.schemas import Report, RuleResult

BASE = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def report(scenario: str, waiting: float, p95: float, rules: list[str]) -> Report:
    return Report(
        scenario=scenario,
        time_range=(BASE, BASE + timedelta(minutes=2)),
        summary={"estimated_total_requests": 1000},
        performance={
            "avg_latency_seconds": p95 / 2,
            "p95_latency_peak_seconds": p95,
            "p99_latency_peak_seconds": p95 + 1,
            "error_rate_peak": 1.0,
            "throughput_avg_rps": 10.0,
            "throughput_peak_rps": 20.0,
        },
        llm_state={"max_waiting": waiting},
        k8s_state={"desired_replicas_max": 2.0, "ready_replicas_max": 2.0},
        resource_state={},
        diagnosis=[
            RuleResult(
                rule_id=rule,
                triggered=True,
                severity="warning",
                evidence={},
                suggestion="fix",
            )
            for rule in rules
        ],
        improvements=[],
    )


def write_report(run_dir, rep: Report):
    run_dir.mkdir()
    (run_dir / "report.json").write_text(rep.model_dump_json(), encoding="utf-8")


def test_build_comparison_detects_improvements(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    write_report(before, report("burst_traffic", waiting=100, p95=30, rules=["hpa_limitation"]))
    write_report(after, report("burst_traffic", waiting=20, p95=10, rules=[]))

    comparison = build_comparison(before, after)
    md = render_markdown(comparison)

    assert comparison["scenario"] == "burst_traffic"
    assert "hpa_limitation" in comparison["triggered_rules"]["removed"]
    assert any(row["key"] == "max_waiting" and row["improved"] for row in comparison["metrics"])
    assert "# LLM 운영 진단 비교 리포트" in md


def test_build_comparison_rejects_scenario_mismatch(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    write_report(before, report("short_prompt", waiting=0, p95=1, rules=[]))
    write_report(after, report("burst_traffic", waiting=20, p95=10, rules=[]))

    with pytest.raises(ValueError, match="scenario mismatch"):
        build_comparison(before, after)
