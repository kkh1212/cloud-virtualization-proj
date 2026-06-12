from __future__ import annotations

from pathlib import Path

import pytest

from analyzer.workload import WorkloadConfigError, load_workload_config
from analyzer.workload_plan import resolve_phases

CFG = load_workload_config(
    Path(__file__).resolve().parents[1] / "config" / "workload-profiles.yaml"
)


def test_quick_is_baselines_only():
    phases = resolve_phases("faq_chatbot", "quick", CFG)
    assert [p["group"] for p in phases] == ["common_baseline", "target_baseline"]
    assert phases[0]["scenario"] == "short_prompt"


def test_standard_adds_stress_phases():
    phases = resolve_phases("faq_chatbot", "standard", CFG)
    groups = [p["group"] for p in phases]
    assert groups == ["common_baseline", "target_baseline", "stress", "stress"]
    # stress env propagated (high-concurrency short test)
    stress = [p for p in phases if p["group"] == "stress"]
    assert any(p["env"].get("SHORT_VUS") == 40 for p in stress)
    assert any(p["role"] == "short_burst" for p in stress)


def test_full_adds_operational():
    phases = resolve_phases("faq_chatbot", "full", CFG)
    assert phases[-1]["group"] == "operational"


def test_every_workload_resolves_all_levels():
    for name in CFG["profiles"]:
        for level in ("quick", "standard", "full"):
            phases = resolve_phases(name, level, CFG)
            assert phases, f"{name}/{level} produced no phases"
            assert phases[0]["group"] == "common_baseline"


def test_unknown_workload_and_level_raise():
    with pytest.raises(WorkloadConfigError):
        resolve_phases("nope", "quick", CFG)
    with pytest.raises(WorkloadConfigError):
        resolve_phases("faq_chatbot", "nope", CFG)
