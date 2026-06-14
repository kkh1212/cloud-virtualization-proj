from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from analyzer.schemas import Report


METRICS = [
    ("avg_latency_seconds", "performance", "avg latency", "s", "lower"),
    ("p95_latency_peak_seconds", "performance", "p95 latency peak", "s", "lower"),
    ("p99_latency_peak_seconds", "performance", "p99 latency peak", "s", "lower"),
    ("error_rate_peak", "performance", "error rate peak", "err/s", "lower"),
    ("throughput_avg_rps", "performance", "throughput avg", "req/s", "higher"),
    ("throughput_peak_rps", "performance", "throughput peak", "req/s", "higher"),
    # LLM serving deltas (present once metrics.yaml / vLLM bindings collect them).
    ("ttft_p95_peak_seconds", "llm_state", "TTFT p95 peak", "s", "lower"),
    ("tpot_p95_peak_seconds", "llm_state", "TPOT p95 peak", "s", "lower"),
    ("queue_wait_p95_peak_seconds", "llm_state", "queue wait p95 peak", "s", "lower"),
    ("max_waiting", "llm_state", "max waiting", "requests", "lower"),
    ("max_batch_size", "llm_state", "batch size max", "", "neutral"),
    ("prompt_token_rate_avg", "llm_state", "input tokens/s", "tok/s", "higher"),
    ("output_token_rate_avg", "llm_state", "output tokens/s", "tok/s", "higher"),
    ("kv_cache_ratio_avg", "llm_state", "KV cache ratio", "", "lower"),
    # K8s / resource deltas.
    ("desired_replicas_max", "k8s_state", "desired replicas max", "replicas", "neutral"),
    ("ready_replicas_max", "k8s_state", "ready replicas max", "replicas", "neutral"),
    ("pending_pod_max", "k8s_state", "pending pod max", "pods", "lower"),
    ("cpu_usage_ratio_peak", "resource_state", "CPU peak", "ratio", "neutral"),
    ("memory_bytes_peak", "resource_state", "memory peak", "B", "lower"),
    # GPU deltas — skipped ("현재 미수집") on the mock pipeline, auto-appear under vLLM.
    ("gpu_utilization_peak", "resource_state", "GPU util peak", "ratio", "higher"),
    ("gpu_memory_used_ratio_peak", "resource_state", "GPU mem peak", "ratio", "lower"),
    # cost delta: TODO (cost 비교는 이번 범위 제외).
]


def main() -> int:
    args = _parse_args()
    before_dir = Path(args.before)
    after_dir = Path(args.after)
    output_dir = Path(args.output) if args.output else after_dir

    try:
        comparison = build_comparison(before_dir, after_dir)
    except ValueError as exc:
        print(f"[compare] {exc}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "comparison.md").write_text(
        render_markdown(comparison),
        encoding="utf-8",
    )
    print(f"wrote {output_dir / 'comparison.md'}")
    print(f"wrote {output_dir / 'comparison.json'}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two analyzer report runs.")
    parser.add_argument("--before", required=True, help="Baseline reports/<run> directory")
    parser.add_argument("--after", required=True, help="Candidate reports/<run> directory")
    parser.add_argument("--output", help="Output directory. Defaults to --after directory")
    return parser.parse_args()


def build_comparison(before_dir: Path, after_dir: Path) -> dict[str, Any]:
    before = _load_report(before_dir)
    after = _load_report(after_dir)
    if before.scenario != after.scenario:
        raise ValueError(
            f"scenario mismatch: before={before.scenario}, after={after.scenario}"
        )

    metric_rows = []
    for key, section, label, unit, direction in METRICS:
        before_value = _metric(before, section, key)
        after_value = _metric(after, section, key)
        metric_rows.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "before": before_value,
                "after": after_value,
                "delta": _delta(before_value, after_value),
                "pct_change": _pct_change(before_value, after_value),
                "direction": direction,
                "improved": _improved(before_value, after_value, direction),
            }
        )

    before_rules = _triggered_rules(before)
    after_rules = _triggered_rules(after)
    workload_fit = _workload_fit_change(before, after)
    summary = _summary(metric_rows, before_rules, after_rules)
    if workload_fit and workload_fit.get("improved") is not None:
        arrow = "개선" if workload_fit["improved"] else "악화/유지"
        summary.insert(
            0,
            f"워크로드 부하 기준 판정: {workload_fit['before_verdict']} → "
            f"{workload_fit['after_verdict']} ({arrow})",
        )
    return {
        "scenario": before.scenario,
        "before_run": str(before_dir),
        "after_run": str(after_dir),
        "metrics": metric_rows,
        "triggered_rules": {
            "before": before_rules,
            "after": after_rules,
            "added": sorted(set(after_rules) - set(before_rules)),
            "removed": sorted(set(before_rules) - set(after_rules)),
            "unchanged": sorted(set(before_rules) & set(after_rules)),
        },
        "workload_fit": workload_fit,
        "summary": summary,
    }


def render_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# LLM 운영 진단 비교 리포트",
        "",
        "## 1. 비교 대상",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 시나리오 | {comparison['scenario']} |",
        f"| before | {comparison['before_run']} |",
        f"| after | {comparison['after_run']} |",
        "",
        "## 2. 핵심 지표 변화",
        "",
        "| Metric | Before | After | Delta | Change | Improved |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in comparison["metrics"]:
        lines.append(
            "| {label} | {before} | {after} | {delta} | {pct} | {improved} |".format(
                label=row["label"],
                before=_fmt(row["before"], row["unit"]),
                after=_fmt(row["after"], row["unit"]),
                delta=_fmt(row["delta"], row["unit"]),
                pct=_fmt_pct(row["pct_change"]),
                improved=_fmt_improved(row["improved"]),
            )
        )

    rules = comparison["triggered_rules"]
    lines.extend(
        [
            "",
            "## 3. Triggered Rule 변화",
            "",
            "| 항목 | 값 |",
            "|---|---|",
            f"| before | {_fmt_list(rules['before'])} |",
            f"| after | {_fmt_list(rules['after'])} |",
            f"| added | {_fmt_list(rules['added'])} |",
            f"| removed | {_fmt_list(rules['removed'])} |",
        ]
    )

    wf = comparison.get("workload_fit")
    if wf:
        lines.extend(
            [
                "",
                "## 4. 워크로드 부하 기준 판정 변화",
                "",
                "| 항목 | Before | After |",
                "|---|---|---|",
                f"| workload | {wf.get('workload')} | {wf.get('workload')} |",
                f"| verdict | {wf.get('before_verdict')} | {wf.get('after_verdict')} |",
                f"| score | {_fmt(wf.get('before_score'), '')} | {_fmt(wf.get('after_score'), '')} |",
                f"| score delta | | {_fmt(wf.get('score_delta'), '')} |",
            ]
        )

    lines.extend(["", "## 5. 요약", ""])
    for item in comparison["summary"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _load_report(run_dir: Path) -> Report:
    report_path = run_dir / "report.json"
    if not report_path.exists():
        raise ValueError(f"missing report.json under {run_dir}")
    return Report.model_validate_json(report_path.read_text(encoding="utf-8"))


def _metric(report: Report, section: str, key: str) -> float | None:
    container = getattr(report, section)
    if container is None:  # optional sections (cost, workload_fit) may be unset
        return None
    value = container.get(key)
    if value is None:
        return None
    return float(value)


_VERDICT_ORDER = {
    "measurement_failed": -1,
    "unsuitable": 0,
    "partially_suitable": 1,
    "suitable": 2,
}


def _workload_fit_change(before: Report, after: Report) -> dict[str, Any] | None:
    b = before.workload_fit or {}
    a = after.workload_fit or {}
    if not b and not a:
        return None
    bv, av = b.get("verdict"), a.get("verdict")
    bs, as_ = b.get("score"), a.get("score")
    score_delta = (as_ - bs) if (bs is not None and as_ is not None) else None
    improved = None
    if bv in _VERDICT_ORDER and av in _VERDICT_ORDER:
        improved = _VERDICT_ORDER[av] > _VERDICT_ORDER[bv]
    return {
        "workload": a.get("workload") or b.get("workload"),
        "before_verdict": bv,
        "after_verdict": av,
        "before_score": bs,
        "after_score": as_,
        "score_delta": score_delta,
        "improved": improved,
    }


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return after - before


def _pct_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return (after - before) / before * 100.0


def _improved(before: float | None, after: float | None, direction: str) -> bool | None:
    if direction == "neutral" or before is None or after is None:
        return None
    if after == before:
        return None
    if direction == "lower":
        return after < before
    if direction == "higher":
        return after > before
    return None


def _triggered_rules(report: Report) -> list[str]:
    return sorted(item.rule_id for item in report.diagnosis if item.triggered)


def _summary(
    metric_rows: list[dict[str, Any]],
    before_rules: list[str],
    after_rules: list[str],
) -> list[str]:
    items = []
    improved = [r for r in metric_rows if r["improved"] is True]
    worsened = [r for r in metric_rows if r["improved"] is False]
    if improved:
        items.append("개선된 지표: " + ", ".join(r["label"] for r in improved))
    if worsened:
        items.append("악화된 지표: " + ", ".join(r["label"] for r in worsened))
    removed = sorted(set(before_rules) - set(after_rules))
    added = sorted(set(after_rules) - set(before_rules))
    if removed:
        items.append("사라진 triggered rule: " + ", ".join(removed))
    if added:
        items.append("새로 triggered 된 rule: " + ", ".join(added))
    if not items:
        items.append("두 run 사이에 뚜렷한 개선/악화 신호가 없습니다.")
    return items


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "현재 미수집"
    return f"{value:.3f} {unit}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "현재 미수집"
    return f"{value:.1f}%"


def _fmt_improved(value: bool | None) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"


def _fmt_list(values: list[str]) -> str:
    return ", ".join(values) if values else "없음"


if __name__ == "__main__":
    raise SystemExit(main())
