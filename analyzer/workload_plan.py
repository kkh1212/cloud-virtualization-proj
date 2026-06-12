"""Resolve a workload + level into an ordered list of load-test phases.

Reads test_plan from analyzer/config/workload-profiles.yaml. The session
orchestrator (scripts/run-workload.sh) consumes the TSV CLI output; Python
callers (tests, analyzer/session.py) use resolve_phases().

Phase composition deliberately keeps a common LLM baseline in front of the
workload-specific stress so a workload run is never judged in isolation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from analyzer.workload import WorkloadConfigError, load_workload_config

BASE_DIR = Path(__file__).resolve().parent


def resolve_phases(
    workload: str,
    level: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    profiles = config.get("profiles", {})
    if workload not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise WorkloadConfigError(f"unknown workload '{workload}'. available: {available}")

    plan = (profiles[workload] or {}).get("test_plan")
    if not plan:
        raise WorkloadConfigError(f"workload '{workload}' has no test_plan")

    levels = plan.get("levels", {})
    if level not in levels:
        available = ", ".join(sorted(levels)) or "<none>"
        raise WorkloadConfigError(f"unknown level '{level}'. available: {available}")

    phases: list[dict[str, Any]] = []
    for group_key in levels[level]:
        group = plan.get(group_key)
        if group is None:
            continue
        if isinstance(group, dict):
            phases.append(_phase(group_key, group_key, group))
        elif isinstance(group, list):
            for index, item in enumerate(group):
                role = item.get("role") or f"{group_key}_{index + 1}"
                phases.append(_phase(group_key, role, item))
    return phases


def _phase(group: str, role: str, spec: dict[str, Any]) -> dict[str, Any]:
    scenario = spec.get("scenario")
    if not scenario:
        raise WorkloadConfigError(f"test_plan phase '{role}' is missing a scenario")
    return {"group": group, "role": role, "scenario": scenario, "env": spec.get("env") or {}}


def _env_csv(env: dict[str, Any]) -> str:
    if not env:
        return "-"
    return ",".join(f"{key}={value}" for key, value in env.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve workload+level into load-test phases.")
    parser.add_argument("--workload", required=True)
    parser.add_argument("--level", default="standard", choices=["quick", "standard", "full"])
    parser.add_argument("--config", default=None, help="workload-profiles.yaml path")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of TSV")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else BASE_DIR / "config" / "workload-profiles.yaml"
    try:
        config = load_workload_config(config_path)
        phases = resolve_phases(args.workload, args.level, config)
    except (WorkloadConfigError, OSError) as exc:
        print(f"[workload-plan] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(phases, ensure_ascii=False, indent=2))
    else:
        # group<TAB>role<TAB>scenario<TAB>ENVCSV  (one phase per line)
        for phase in phases:
            print(f"{phase['group']}\t{phase['role']}\t{phase['scenario']}\t{_env_csv(phase['env'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
