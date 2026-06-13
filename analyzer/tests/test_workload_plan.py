from __future__ import annotations

from pathlib import Path

import pytest

from analyzer.workload import WorkloadConfigError, load_workload_config
from analyzer.workload_plan import resolve_phases, workload_load_unit

CFG = load_workload_config(
    Path(__file__).resolve().parents[1] / "config" / "workload-profiles.yaml"
)


def test_quick_is_baselines_only():
    phases = resolve_phases("support_chat", "quick", CFG)
    assert [p["group"] for p in phases] == ["common_baseline", "target_baseline"]
    assert phases[0]["scenario"] == "short_prompt"


def test_standard_runs_the_full_ladder():
    phases = resolve_phases("support_chat", "standard", CFG)
    groups = [p["group"] for p in phases]
    # baselines first, then the monotonic stress ladder (>=3 rungs, no operational)
    assert groups[:2] == ["common_baseline", "target_baseline"]
    assert set(groups[2:]) == {"stress"}
    stress = [p for p in phases if p["group"] == "stress"]
    assert len(stress) >= 3
    # the ladder load increases monotonically (support_chat ramps RAG_VUS)
    vus = [p["env"].get("RAG_VUS") for p in stress]
    assert vus == sorted(vus) and vus[-1] > vus[0]
    # each rung carries a numeric `load` that also increases monotonically
    loads = [p["load"] for p in stress]
    assert all(isinstance(x, (int, float)) for x in loads)
    assert loads == sorted(loads) and loads[-1] > loads[0]


def test_baselines_have_no_load_but_ladder_does():
    phases = resolve_phases("support_chat", "standard", CFG)
    baselines = [p for p in phases if p["group"] != "stress"]
    assert all(p["load"] is None for p in baselines)


def test_load_unit_reported_per_workload():
    assert workload_load_unit("support_chat", CFG) == "vus"
    assert workload_load_unit("doc_summary", CFG) == "input_tokens"
    assert workload_load_unit("json_extraction", CFG) == "rps"


def test_full_adds_operational():
    phases = resolve_phases("support_chat", "full", CFG)
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
        resolve_phases("support_chat", "nope", CFG)
