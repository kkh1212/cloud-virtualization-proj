"""Aggregate a multi-phase workload session into a single verdict.

scripts/run-workload.sh runs a common baseline + target baseline + workload-specific
stress (+ operational) as separate analyzable phases under
reports/session-<workload>-<level>-<ts>/, then calls this to roll them up:

  - overall verdict = the WORST verdict among the workload-specific stress phases
    (falling back to the target baseline, then any judged phase),
  - baseline-relative load weight = how much heavier the stress phases are than the
    common LLM baseline (p95 / TTFT ratio),
  - the dominant bottleneck(s) observed.

Writes session-report.md + session-report.json next to session.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from analyzer.schemas import Report

_VERDICT_ORDER = {"unsuitable": 0, "partially_suitable": 1, "suitable": 2}
_VERDICT_LABEL = {
    "unsuitable": "부적합(unsuitable)",
    "partially_suitable": "부분 적합(partially_suitable)",
    "suitable": "적합(suitable)",
}


def build_session(session_dir: Path) -> dict[str, Any]:
    manifest = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    phases: list[dict[str, Any]] = []
    for entry in manifest.get("phases", []):
        report = _load_report(session_dir / entry["dir"])
        fit = (report.workload_fit if report else None) or {}
        perf = (report.performance if report else {}) or {}
        llm = (report.llm_state if report else {}) or {}
        phases.append(
            {
                "group": entry.get("group"),
                "role": entry.get("role"),
                "scenario": entry.get("scenario"),
                "dir": entry.get("dir"),
                "load": entry.get("load"),
                "verdict": fit.get("verdict"),
                "score": fit.get("score"),
                "bottleneck": fit.get("bottleneck"),
                "p95_latency_peak_seconds": perf.get("p95_latency_peak_seconds"),
                "ttft_p95_peak_seconds": llm.get("ttft_p95_peak_seconds"),
            }
        )

    judged = [p for p in phases if p["verdict"] in _VERDICT_ORDER]
    stress = [p for p in judged if p["group"] == "stress"]
    target = [p for p in judged if p["group"] == "target_baseline"]
    pool = stress or target or judged
    overall_verdict = (
        min(pool, key=lambda p: _VERDICT_ORDER[p["verdict"]])["verdict"] if pool else None
    )
    overall_score = min((p["score"] for p in pool if p["score"] is not None), default=None)
    bottlenecks = sorted({p["bottleneck"] for p in judged if p["bottleneck"]})

    baseline = next((p for p in phases if p["group"] == "common_baseline"), None)
    weight = _baseline_weight(baseline, stress or target)

    load_unit = manifest.get("load_unit") or ""
    capacity = _capacity(phases, load_unit)

    return {
        "workload": manifest.get("workload"),
        "level": manifest.get("level"),
        "created_iso": manifest.get("created_iso"),
        "overall_verdict": overall_verdict,
        "overall_score": overall_score,
        "bottlenecks": bottlenecks,
        "baseline_weight": weight,
        "load_unit": load_unit,
        "capacity": capacity,
        "phases": phases,
    }


def _capacity(phases: list[dict[str, Any]], load_unit: str) -> dict[str, Any]:
    """Walk the ordered stress ladder to find the capacity knee.

    The ladder rungs are evaluated in increasing-load order, so:
      - safe   = highest load in the leading all-`suitable` prefix,
      - knee   = the first rung that degrades (partially_suitable or worse),
      - break  = the first `unsuitable` rung,
      - limiting = the dominant bottleneck category at the knee.
    Loads may be None on legacy/manual sessions; verdict-based knee still works.
    """
    rungs = [
        p for p in phases
        if p["group"] == "stress" and p["verdict"] in _VERDICT_ORDER
    ]
    if not rungs:
        return {"load_unit": load_unit, "rungs_evaluated": 0}

    safe_rung: dict[str, Any] | None = None
    for rung in rungs:
        if rung["verdict"] == "suitable":
            safe_rung = rung
        else:
            break  # monotonic ladder: stop at the first non-suitable rung

    knee_rung = next((r for r in rungs if r["verdict"] != "suitable"), None)
    break_rung = next((r for r in rungs if r["verdict"] == "unsuitable"), None)

    return {
        "load_unit": load_unit,
        "rungs_evaluated": len(rungs),
        "safe": _rung_brief(safe_rung),
        "knee": _rung_brief(knee_rung),
        "break": _rung_brief(break_rung),
        "limiting_bottleneck": knee_rung["bottleneck"] if knee_rung else None,
    }


def _rung_brief(rung: dict[str, Any] | None) -> dict[str, Any] | None:
    if rung is None:
        return None
    return {
        "load": rung.get("load"),
        "role": rung.get("role"),
        "verdict": rung.get("verdict"),
        "bottleneck": rung.get("bottleneck"),
    }


def _baseline_weight(
    baseline: dict[str, Any] | None,
    heavy: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not baseline or not heavy:
        return None

    def worst(key: str) -> float | None:
        values = [p[key] for p in heavy if p.get(key) is not None]
        return max(values) if values else None

    def ratio(base: Any, peak: float | None) -> float | None:
        if base is None or peak is None or base == 0:
            return None
        return round(peak / base, 2)

    return {
        "p95_ratio_vs_baseline": ratio(baseline.get("p95_latency_peak_seconds"), worst("p95_latency_peak_seconds")),
        "ttft_ratio_vs_baseline": ratio(baseline.get("ttft_p95_peak_seconds"), worst("ttft_p95_peak_seconds")),
    }


def render_markdown(session: dict[str, Any]) -> str:
    overall = _VERDICT_LABEL.get(session["overall_verdict"], "평가 항목 없음")
    score = session["overall_score"]
    lines = [
        "# LLM 워크로드 세션 리포트",
        "",
        "## 1. 세션 개요",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| workload | {session['workload']} |",
        f"| level | {session['level']} |",
        f"| 생성 | {session['created_iso']} |",
        f"| 종합 판정 | **{overall}** |",
        f"| 종합 score(최저) | {('%.1f' % score) if score is not None else '평가 불가'} |",
        f"| 관측 병목 | {', '.join(session['bottlenecks']) or '없음'} |",
        f"| 안전 용량(safe capacity) | {_capacity_headline(session.get('capacity'))} |",
        "",
        "## 2. Phase별 결과",
        "",
        "| # | group | role | scenario | verdict | score | bottleneck | p95(s) | TTFT(s) |",
        "|---|---|---|---|---|---:|---|---:|---:|",
    ]
    for index, phase in enumerate(session["phases"], start=1):
        lines.append(
            "| {n} | {group} | {role} | {scenario} | {verdict} | {score} | {bn} | {p95} | {ttft} |".format(
                n=index,
                group=phase["group"],
                role=phase["role"],
                scenario=phase["scenario"],
                verdict=phase["verdict"] if phase["verdict"] is not None else "평가없음",
                score=("%.1f" % phase["score"]) if phase["score"] is not None else "-",
                bn=phase["bottleneck"] or "-",
                p95=_fmt(phase["p95_latency_peak_seconds"]),
                ttft=_fmt(phase["ttft_p95_peak_seconds"]),
            )
        )

    weight = session["baseline_weight"]
    lines.extend(["", "## 3. Common baseline 대비 부하 무게", ""])
    if weight:
        lines.extend(
            [
                "| 항목 | baseline 대비 배수 |",
                "|---|---:|",
                f"| p95 latency | {_fmt_ratio(weight.get('p95_ratio_vs_baseline'))} |",
                f"| TTFT | {_fmt_ratio(weight.get('ttft_ratio_vs_baseline'))} |",
            ]
        )
    else:
        lines.append("baseline 또는 stress phase 지표가 부족해 무게 비교를 계산하지 못했습니다.")

    lines.extend(_render_capacity(session.get("capacity")))

    lines.extend(
        [
            "",
            "## 5. 종합 판단",
            "",
            f"- 이 설정은 워크로드 '{session['workload']}' 기준 **{overall}** 입니다.",
        ]
    )
    if session["bottlenecks"]:
        lines.append(
            f"- 주요 병목: {', '.join(session['bottlenecks'])}. "
            "각 phase report.md 의 '권장 설정'(workload playbook 포함)을 적용 후 동일 level 로 재실행해 비교하세요."
        )
    else:
        lines.append("- 임계값을 넘긴 phase 가 없습니다(또는 평가 지표 미수집).")
    lines.append("")
    return "\n".join(lines)


def _fmt_load(load: Any, unit: str) -> str:
    if load is None:
        return "미기록"
    load_str = f"{load:g}" if isinstance(load, (int, float)) else str(load)
    return f"{load_str} {unit}".strip()


def _capacity_headline(capacity: dict[str, Any] | None) -> str:
    if not capacity or not capacity.get("rungs_evaluated"):
        return "ladder 미실행"
    unit = capacity.get("load_unit", "")
    safe = capacity.get("safe")
    if safe:
        return f"~{_fmt_load(safe['load'], unit)} 까지 안전"
    knee = capacity.get("knee")
    if knee:
        return f"최저 단계({_fmt_load(knee['load'], unit)})부터 이미 한계"
    return "판정 불가"


def _render_capacity(capacity: dict[str, Any] | None) -> list[str]:
    lines = ["", "## 4. 부하 한계(용량) 판정", ""]
    if not capacity or not capacity.get("rungs_evaluated"):
        lines.append("부하 ladder(stress) 단계가 없거나 평가 지표가 부족해 용량을 판정하지 못했습니다.")
        return lines

    unit = capacity.get("load_unit", "")
    safe, knee, brk = capacity.get("safe"), capacity.get("knee"), capacity.get("break")
    lines.extend(
        [
            "점진 부하 ladder를 단계별로 올리며 각 단계의 SLO를 평가한 결과입니다.",
            "",
            "| 구간 | 부하 | 의미 |",
            "|---|---|---|",
            f"| 안전(safe) | {_fmt_load(safe['load'], unit) if safe else '없음'} | 이 부하까지는 SLO 통과 |",
            f"| 한계 시작(knee) | {_fmt_load(knee['load'], unit) if knee else '미도달'} | 이 부하부터 SLO 저하({knee['verdict'] if knee else '-'}) |",
            f"| 붕괴(break) | {_fmt_load(brk['load'], unit) if brk else '미도달'} | 이 부하에서 부적합 |",
            "",
            f"- 한계 병목: **{capacity.get('limiting_bottleneck') or '없음'}** "
            "(각 phase report.md 의 '권장 설정' playbook을 적용 후 동일 ladder 재실행해 knee 이동을 비교하세요).",
        ]
    )
    return lines


def _load_report(run_dir: Path) -> Report | None:
    path = run_dir / "report.json"
    if not path.exists():
        return None
    try:
        return Report.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.3f}"


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return "현재 미수집"
    return f"×{float(value):.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate a workload session into one verdict.")
    parser.add_argument("--session", required=True, help="reports/session-<...> directory")
    args = parser.parse_args()

    session_dir = Path(args.session)
    if not (session_dir / "session.json").exists():
        print(f"[session] missing session.json under {session_dir}", file=sys.stderr)
        return 2

    session = build_session(session_dir)
    (session_dir / "session-report.json").write_text(
        json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (session_dir / "session-report.md").write_text(render_markdown(session), encoding="utf-8")
    print(f"wrote {session_dir / 'session-report.md'}")
    print(f"wrote {session_dir / 'session-report.json'}")
    print(f"overall_verdict={session['overall_verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
