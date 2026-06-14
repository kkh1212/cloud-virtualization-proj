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
    # standard is the fast capacity mode: only the monotonic stress ladder.
    assert set(groups) == {"stress"}
    stress = [p for p in phases if p["group"] == "stress"]
    assert len(stress) == 3
    # the ladder load increases monotonically (support_chat ramps RAG_VUS)
    vus = [p["env"].get("RAG_VUS") for p in stress]
    assert vus == sorted(vus) and vus[-1] > vus[0]
    # each rung carries a numeric `load` that also increases monotonically
    loads = [p["load"] for p in stress]
    assert all(isinstance(x, (int, float)) for x in loads)
    assert loads == sorted(loads) and loads[-1] > loads[0]


def test_baselines_have_no_load_but_ladder_does():
    phases = resolve_phases("support_chat", "full", CFG)
    baselines = [p for p in phases if p["group"] != "stress"]
    assert all(p["load"] is None for p in baselines)


def test_load_unit_reported_per_workload():
    assert workload_load_unit("support_chat", CFG) == "vus"
    assert workload_load_unit("doc_summary", CFG) == "input_tokens"
    assert workload_load_unit("code_assistant", CFG) == "output_tokens"
    assert workload_load_unit("json_extraction", CFG) == "rps"


def test_code_assistant_ramps_output_length_not_concurrency():
    phases = resolve_phases("code_assistant", "standard", CFG)
    stress = [p for p in phases if p["group"] == "stress"]
    loads = [p["load"] for p in stress]
    assert loads == [256, 768, 1536]
    assert [p["env"]["LONG_OUTPUT_MAX_TOKENS"] for p in stress] == loads
    assert {p["env"]["LONG_OUTPUT_INPUT_TOKENS"] for p in stress} == {1200}
    assert {p["env"]["LONG_OUTPUT_VUS"] for p in stress} == {2}


def test_json_extraction_uses_conservative_rps_ladder():
    phases = resolve_phases("json_extraction", "standard", CFG)
    stress = [p for p in phases if p["group"] == "stress"]
    loads = [p["load"] for p in stress]
    assert loads == [25, 100, 200]
    assert [p["env"]["EXTRACT_RATE"] for p in stress] == loads


def test_full_adds_operational():
    phases = resolve_phases("support_chat", "full", CFG)
    assert phases[0]["group"] == "common_baseline"
    assert phases[-1]["group"] == "operational"


def test_every_workload_resolves_all_levels():
    for name in CFG["profiles"]:
        for level in ("quick", "standard", "full"):
            phases = resolve_phases(name, level, CFG)
            assert phases, f"{name}/{level} produced no phases"
            if level == "standard":
                assert phases[0]["group"] == "stress"
            else:
                assert phases[0]["group"] == "common_baseline"


def test_unknown_workload_and_level_raise():
    with pytest.raises(WorkloadConfigError):
        resolve_phases("nope", "quick", CFG)
    with pytest.raises(WorkloadConfigError):
        resolve_phases("support_chat", "nope", CFG)
