from __future__ import annotations

import json

import pytest

from analyzer.session_compare import build_session_comparison, render_markdown


def _write_session(path, *, workload="support_chat", safe=20, knee=40, brk=80, score=50):
    path.mkdir()
    payload = {
        "workload": workload,
        "level": "standard",
        "load_unit": "vus",
        "overall_verdict": "partially_suitable",
        "overall_score": score,
        "bottlenecks": ["queue"],
        "capacity": {
            "load_unit": "vus",
            "rungs_evaluated": 4,
            "safe": {"load": safe, "role": f"conc_{safe}", "verdict": "suitable", "bottleneck": None},
            "knee": {"load": knee, "role": f"conc_{knee}", "verdict": "partially_suitable", "bottleneck": "queue"},
            "break": {"load": brk, "role": f"conc_{brk}", "verdict": "unsuitable", "bottleneck": "queue"},
            "limiting_bottleneck": "queue",
        },
        "phases": [
            {
                "group": "stress",
                "role": f"conc_{safe}",
                "scenario": "rag_like",
                "load": safe,
                "verdict": "suitable",
                "score": 90,
                "bottleneck": None,
                "p95_latency_peak_seconds": 1.0,
                "ttft_p95_peak_seconds": 0.2,
            }
        ],
    }
    (path / "session-report.json").write_text(json.dumps(payload), encoding="utf-8")


def test_session_compare_detects_capacity_improvement(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_session(before, safe=20, knee=40, brk=80, score=50)
    _write_session(after, safe=40, knee=80, brk=120, score=70)

    comparison = build_session_comparison(before, after)
    assert comparison["capacity"]["safe"]["improved"] is True
    assert comparison["capacity"]["knee"]["delta"] == 40
    assert comparison["capacity"]["break"]["improved"] is True
    assert comparison["overall"]["score_delta"] == 20

    md = render_markdown(comparison)
    assert "Capacity Ladder Change" in md
    assert "40 vus" in md


def test_session_compare_rejects_workload_mismatch(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_session(before, workload="support_chat")
    _write_session(after, workload="doc_summary")

    with pytest.raises(ValueError, match="workload mismatch"):
        build_session_comparison(before, after)
