from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from analyzer.schemas import Report
from analyzer.session import build_session, render_markdown

BASE = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _phase_report(verdict, score, bottleneck, p95, ttft) -> Report:
    return Report(
        scenario="x",
        time_range=(BASE, BASE + timedelta(minutes=2)),
        summary={},
        performance={"p95_latency_peak_seconds": p95},
        llm_state={"ttft_p95_peak_seconds": ttft},
        k8s_state={},
        resource_state={},
        workload_fit=(
            None
            if verdict is None
            else {"workload": "rag_internal_qa", "verdict": verdict, "score": score, "bottleneck": bottleneck}
        ),
        diagnosis=[],
        improvements=[],
    )


def _write_session(tmp_path):
    phases = [
        ("common_baseline", "common_baseline", _phase_report("suitable", 100.0, None, 1.0, 0.3)),
        ("target_baseline", "target_baseline", _phase_report("suitable", 100.0, None, 3.0, 1.0)),
        ("stress", "context_4k", _phase_report("partially_suitable", 50.0, "prefill", 8.0, 2.5)),
        ("stress", "context_8k", _phase_report("unsuitable", 0.0, "prefill", 16.0, 4.0)),
    ]
    manifest_phases = []
    for index, (group, role, report) in enumerate(phases, start=1):
        dirname = f"{index:02d}-{group}-{role}"
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "report.json").write_text(
            report.model_dump_json(), encoding="utf-8"
        )
        manifest_phases.append(
            {"group": group, "role": role, "scenario": "x", "dir": dirname, "env": "-"}
        )
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "workload": "rag_internal_qa",
                "level": "standard",
                "created_iso": "2026-06-12T12:00:00Z",
                "prometheus_url": "http://localhost:9090",
                "phases": manifest_phases,
            }
        ),
        encoding="utf-8",
    )


def test_overall_verdict_is_worst_stress(tmp_path):
    _write_session(tmp_path)
    session = build_session(tmp_path)
    # worst stress phase is unsuitable -> overall unsuitable
    assert session["overall_verdict"] == "unsuitable"
    assert session["overall_score"] == 0.0
    assert "prefill" in session["bottlenecks"]
    # stress p95 16.0 vs baseline 1.0 -> ×16
    assert session["baseline_weight"]["p95_ratio_vs_baseline"] == 16.0


def test_render_markdown_has_sections(tmp_path):
    _write_session(tmp_path)
    md = render_markdown(build_session(tmp_path))
    assert "# LLM 워크로드 세션 리포트" in md
    assert "## 2. Phase별 결과" in md
    assert "부적합" in md
