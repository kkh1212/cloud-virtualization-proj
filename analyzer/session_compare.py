"""Compare two workload session reports.

This is the session-level companion to analyzer.compare. It compares
session-report.json files, especially the capacity ladder (safe/knee/break), so
users can see whether a tuning change moved the workload limit upward.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_VERDICT_ORDER = {
    "measurement_failed": -1,
    "unsuitable": 0,
    "partially_suitable": 1,
    "suitable": 2,
}


def build_session_comparison(before_dir: Path, after_dir: Path) -> dict[str, Any]:
    before = _load_session(before_dir)
    after = _load_session(after_dir)
    if before.get("workload") != after.get("workload"):
        raise ValueError(
            f"workload mismatch: before={before.get('workload')}, after={after.get('workload')}"
        )
    if before.get("level") != after.get("level"):
        raise ValueError(
            f"level mismatch: before={before.get('level')}, after={after.get('level')}"
        )

    capacity = _capacity_change(before.get("capacity") or {}, after.get("capacity") or {})
    phases = _phase_changes(before.get("phases") or [], after.get("phases") or [])
    verdict_change = _verdict_change(before.get("overall_verdict"), after.get("overall_verdict"))
    score_delta = _delta(before.get("overall_score"), after.get("overall_score"))
    summary = _summary(capacity, verdict_change, score_delta)

    return {
        "workload": before.get("workload"),
        "level": before.get("level"),
        "load_unit": after.get("load_unit") or before.get("load_unit") or "",
        "before_session": str(before_dir),
        "after_session": str(after_dir),
        "overall": {
            "before_verdict": before.get("overall_verdict"),
            "after_verdict": after.get("overall_verdict"),
            "verdict_improved": verdict_change,
            "before_score": before.get("overall_score"),
            "after_score": after.get("overall_score"),
            "score_delta": score_delta,
        },
        "capacity": capacity,
        "bottlenecks": {
            "before": before.get("bottlenecks") or [],
            "after": after.get("bottlenecks") or [],
            "added": sorted(set(after.get("bottlenecks") or []) - set(before.get("bottlenecks") or [])),
            "removed": sorted(set(before.get("bottlenecks") or []) - set(after.get("bottlenecks") or [])),
        },
        "phases": phases,
        "summary": summary,
    }


def _load_session(session_dir: Path) -> dict[str, Any]:
    path = session_dir / "session-report.json"
    if not path.exists():
        raise ValueError(f"missing session-report.json under {session_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def _capacity_change(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "load_unit": after.get("load_unit") or before.get("load_unit") or "",
        "safe": _rung_change(before.get("safe"), after.get("safe"), higher_is_better=True),
        "knee": _rung_change(before.get("knee"), after.get("knee"), higher_is_better=True, absent_after_is_better=True),
        "break": _rung_change(before.get("break"), after.get("break"), higher_is_better=True, absent_after_is_better=True),
        "limiting_bottleneck": {
            "before": before.get("limiting_bottleneck"),
            "after": after.get("limiting_bottleneck"),
            "changed": before.get("limiting_bottleneck") != after.get("limiting_bottleneck"),
        },
    }


def _rung_change(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    higher_is_better: bool,
    absent_after_is_better: bool = False,
) -> dict[str, Any]:
    before_load = _load(before)
    after_load = _load(after)
    improved: bool | None
    if before_load is None and after_load is None:
        improved = None
    elif before_load is None:
        improved = True
    elif after_load is None:
        improved = True if absent_after_is_better else False
    elif after_load == before_load:
        improved = None
    else:
        improved = after_load > before_load if higher_is_better else after_load < before_load
    return {
        "before": before,
        "after": after,
        "before_load": before_load,
        "after_load": after_load,
        "delta": _delta(before_load, after_load),
        "improved": improved,
    }


def _phase_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after_by_key = {_phase_key(p): p for p in after}
    rows: list[dict[str, Any]] = []
    for b in before:
        key = _phase_key(b)
        a = after_by_key.get(key)
        if not a:
            rows.append({"key": key, "before": b, "after": None, "matched": False})
            continue
        rows.append(
            {
                "key": key,
                "matched": True,
                "group": b.get("group"),
                "role": b.get("role"),
                "scenario": b.get("scenario"),
                "load": a.get("load") if a.get("load") is not None else b.get("load"),
                "verdict": {
                    "before": b.get("verdict"),
                    "after": a.get("verdict"),
                    "improved": _verdict_change(b.get("verdict"), a.get("verdict")),
                },
                "score_delta": _delta(b.get("score"), a.get("score")),
                "p95_delta_seconds": _delta(b.get("p95_latency_peak_seconds"), a.get("p95_latency_peak_seconds")),
                "ttft_delta_seconds": _delta(b.get("ttft_p95_peak_seconds"), a.get("ttft_p95_peak_seconds")),
                "bottleneck": {"before": b.get("bottleneck"), "after": a.get("bottleneck")},
            }
        )
    return rows


def _phase_key(phase: dict[str, Any]) -> str:
    return "|".join(
        str(phase.get(part) or "")
        for part in ("group", "role", "scenario", "load")
    )


def _load(rung: dict[str, Any] | None) -> float | None:
    if not rung:
        return None
    value = rung.get("load")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delta(before: Any, after: Any) -> float | None:
    if before is None or after is None:
        return None
    try:
        return float(after) - float(before)
    except (TypeError, ValueError):
        return None


def _verdict_change(before: str | None, after: str | None) -> bool | None:
    if before not in _VERDICT_ORDER or after not in _VERDICT_ORDER:
        return None
    if before == after:
        return None
    return _VERDICT_ORDER[after] > _VERDICT_ORDER[before]


def _summary(capacity: dict[str, Any], verdict_change: bool | None, score_delta: float | None) -> list[str]:
    items: list[str] = []
    for name, label in (("safe", "safe capacity"), ("knee", "capacity knee"), ("break", "break point")):
        change = capacity.get(name) or {}
        if change.get("improved") is True:
            items.append(f"{label} improved")
        elif change.get("improved") is False:
            items.append(f"{label} regressed")
    if verdict_change is True:
        items.append("overall workload verdict improved")
    elif verdict_change is False:
        items.append("overall workload verdict regressed")
    if score_delta is not None and score_delta != 0:
        items.append(f"overall score changed by {score_delta:+.1f}")
    if not items:
        items.append("no clear session-level improvement or regression detected")
    return items


def render_markdown(comparison: dict[str, Any]) -> str:
    unit = comparison.get("load_unit") or ""
    lines = [
        "# LLM Workload Session Comparison",
        "",
        "## 1. Overview",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| workload | {comparison['workload']} |",
        f"| level | {comparison['level']} |",
        f"| load unit | {unit or '-'} |",
        f"| before | {comparison['before_session']} |",
        f"| after | {comparison['after_session']} |",
        "",
        "## 2. Capacity Ladder Change",
        "",
        "| Point | Before | After | Delta | Improved |",
        "|---|---:|---:|---:|---|",
    ]
    cap = comparison["capacity"]
    for key in ("safe", "knee", "break"):
        row = cap[key]
        lines.append(
            f"| {key} | {_fmt_load(row['before_load'], unit)} | "
            f"{_fmt_load(row['after_load'], unit)} | {_fmt_delta(row['delta'], unit)} | "
            f"{_fmt_bool(row['improved'])} |"
        )
    lines.extend(
        [
            "",
            "| Bottleneck | Before | After |",
            "|---|---|---|",
            f"| limiting | {cap['limiting_bottleneck']['before'] or '-'} | {cap['limiting_bottleneck']['after'] or '-'} |",
            "",
            "## 3. Overall Verdict",
            "",
            "| Item | Before | After | Delta |",
            "|---|---:|---:|---:|",
        ]
    )
    overall = comparison["overall"]
    lines.append(
        f"| verdict | {overall['before_verdict'] or '-'} | {overall['after_verdict'] or '-'} | {_fmt_bool(overall['verdict_improved'])} |"
    )
    lines.append(
        f"| score | {_fmt_num(overall['before_score'])} | {_fmt_num(overall['after_score'])} | {_fmt_num(overall['score_delta'], signed=True)} |"
    )
    lines.extend(
        [
            "",
            "## 4. Phase Changes",
            "",
            "| Phase | Load | Verdict | Score Delta | p95 Delta | TTFT Delta | Bottleneck |",
            "|---|---:|---|---:|---:|---:|---|",
        ]
    )
    for row in comparison["phases"]:
        if not row.get("matched"):
            lines.append(f"| {row['key']} | - | missing after phase | - | - | - | - |")
            continue
        bottleneck = row["bottleneck"]
        lines.append(
            f"| {row['group']}/{row['role']} | {_fmt_load(row.get('load'), unit)} | "
            f"{row['verdict']['before']} -> {row['verdict']['after']} | "
            f"{_fmt_num(row['score_delta'], signed=True)} | "
            f"{_fmt_num(row['p95_delta_seconds'], signed=True, unit='s')} | "
            f"{_fmt_num(row['ttft_delta_seconds'], signed=True, unit='s')} | "
            f"{bottleneck['before'] or '-'} -> {bottleneck['after'] or '-'} |"
        )
    lines.extend(["", "## 5. Summary", ""])
    for item in comparison["summary"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _fmt_load(value: Any, unit: str) -> str:
    if value is None:
        return "-"
    return f"{float(value):g} {unit}".strip()


def _fmt_delta(value: Any, unit: str) -> str:
    if value is None:
        return "-"
    return f"{float(value):+g} {unit}".strip()


def _fmt_bool(value: bool | None) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"


def _fmt_num(value: Any, *, signed: bool = False, unit: str = "") -> str:
    if value is None:
        return "-"
    fmt = "{:+.3f}" if signed else "{:.3f}"
    return f"{fmt.format(float(value))}{(' ' + unit) if unit else ''}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two workload session reports.")
    parser.add_argument("--before", required=True, help="Before reports/session-... directory")
    parser.add_argument("--after", required=True, help="After reports/session-... directory")
    parser.add_argument("--output", help="Output directory. Defaults to --after")
    args = parser.parse_args()

    before_dir = Path(args.before)
    after_dir = Path(args.after)
    output_dir = Path(args.output) if args.output else after_dir
    try:
        comparison = build_session_comparison(before_dir, after_dir)
    except ValueError as exc:
        print(f"[session-compare] {exc}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "session-comparison.json").write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "session-comparison.md").write_text(
        render_markdown(comparison), encoding="utf-8"
    )
    print(f"wrote {output_dir / 'session-comparison.md'}")
    print(f"wrote {output_dir / 'session-comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
