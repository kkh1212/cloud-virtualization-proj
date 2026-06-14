from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from analyzer.schemas import Report
from analyzer.session import build_session, render_markdown

BASE = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _phase_report(verdict, score, bottleneck, p95, ttft, missing=None) -> Report:
    report = Report(
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
    if report.workload_fit is not None and missing:
        report.workload_fit["missing_required_metrics"] = missing
    return report


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
    assert "한계 도달" in md


def _write_ladder_session(tmp_path):
    # increasing-load ladder: safe through 20 vus, degrades at 40, breaks at 80
    phases = [
        ("common_baseline", "common_baseline", None, _phase_report("suitable", 100.0, None, 1.0, 0.3)),
        ("target_baseline", "target_baseline", None, _phase_report("suitable", 100.0, None, 2.0, 0.5)),
        ("stress", "conc_10", 10, _phase_report("suitable", 100.0, None, 2.5, 0.6)),
        ("stress", "conc_20", 20, _phase_report("suitable", 90.0, None, 2.8, 0.8)),
        ("stress", "conc_40", 40, _phase_report("partially_suitable", 50.0, "queue", 4.0, 1.2)),
        ("stress", "conc_80", 80, _phase_report("unsuitable", 0.0, "queue", 9.0, 2.5)),
    ]
    manifest_phases = []
    for index, (group, role, load, report) in enumerate(phases, start=1):
        dirname = f"{index:02d}-{group}-{role}"
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "report.json").write_text(report.model_dump_json(), encoding="utf-8")
        manifest_phases.append(
            {"group": group, "role": role, "scenario": "x", "dir": dirname, "env": "-", "load": load}
        )
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "workload": "support_chat",
                "level": "standard",
                "load_unit": "vus",
                "created_iso": "2026-06-12T12:00:00Z",
                "prometheus_url": "http://localhost:9090",
                "phases": manifest_phases,
            }
        ),
        encoding="utf-8",
    )


def test_capacity_knee_detected(tmp_path):
    _write_ladder_session(tmp_path)
    cap = build_session(tmp_path)["capacity"]
    assert cap["load_unit"] == "vus"
    assert cap["rungs_evaluated"] == 4
    assert cap["safe"]["load"] == 20          # last suitable in the leading prefix
    assert cap["knee"]["load"] == 40          # first degraded rung
    assert cap["knee"]["verdict"] == "partially_suitable"
    assert cap["break"]["load"] == 80         # first unsuitable rung
    assert cap["limiting_bottleneck"] == "queue"


def test_capacity_section_renders(tmp_path):
    _write_ladder_session(tmp_path)
    md = render_markdown(build_session(tmp_path))
    assert "## 5. 부하 한계(용량) 판정" in md
    assert "20 vus" in md          # safe capacity shown
    assert "## 6. 결과 해석" in md


def test_measurement_failed_is_not_safe_or_break(tmp_path):
    phases = [
        ("stress", "doc_4k", 4000, _phase_report("suitable", 100.0, None, 1.0, 0.2)),
        (
            "stress",
            "doc_32k",
            32000,
            _phase_report(
                "measurement_failed",
                None,
                "measurement",
                None,
                None,
                missing=["p95_latency", "ttft_p95"],
            ),
        ),
    ]
    manifest_phases = []
    for index, (group, role, load, report) in enumerate(phases, start=1):
        dirname = f"{index:02d}-{group}-{role}"
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "report.json").write_text(report.model_dump_json(), encoding="utf-8")
        manifest_phases.append(
            {"group": group, "role": role, "scenario": "x", "dir": dirname, "env": "-", "load": load}
        )
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "workload": "doc_summary",
                "level": "standard",
                "load_unit": "input_tokens",
                "created_iso": "2026-06-12T12:00:00Z",
                "phases": manifest_phases,
            }
        ),
        encoding="utf-8",
    )
    session = build_session(tmp_path)
    cap = session["capacity"]
    assert session["overall_verdict"] == "measurement_failed"
    assert cap["safe"]["load"] == 4000
    assert cap["knee"] is None
    assert cap["break"] is None
    assert cap["measurement_failed"]["load"] == 32000
    md = render_markdown(session)
    assert "## 3. 측정 실패 phase" in md
    assert "p95_latency, ttft_p95" in md
