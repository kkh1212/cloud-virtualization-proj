from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from dateutil.parser import isoparse

from analyzer.collector import PrometheusClient, PrometheusError
from analyzer.cost import CostConfigError, build_cost_estimate, load_cost_config
from analyzer.recommend import (
    RecommendConfigError,
    build_recommendations,
    load_recommend_config,
)
from analyzer.report import render_json, render_markdown
from analyzer.rules import ALL_RULES
from analyzer.schemas import MetricSnapshot, Report, TimeSeries
from analyzer.slo import (
    SLOConfigError,
    build_slo_evaluation,
    load_slo_config,
    slo_breach_result,
)

BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    args = _parse_args()
    run_context = _resolve_run_context(args)

    metrics_cfg = _load_yaml(BASE_DIR / "config" / "metrics.yaml")
    rules_cfg = _load_yaml(BASE_DIR / "config" / "rules.yaml")

    client = PrometheusClient(run_context["prometheus_url"], strict=args.strict)
    try:
        snapshot = client.fetch_snapshot(
            metrics_cfg,
            run_context["start"],
            run_context["end"],
            args.step,
        )
    except PrometheusError as exc:
        print(f"[strict] prometheus failure: {exc}", file=sys.stderr)
        print(
            "[strict] Check the port-forward (kubectl -n monitoring port-forward "
            "svc/prom-kube-prometheus-stack-prometheus 9090:9090) "
            "and the --prometheus-url argument.",
            file=sys.stderr,
        )
        return 2

    diagnosis = []
    for rule_cls in ALL_RULES:
        rule = rule_cls()
        if not rule.applies(snapshot):
            continue
        diagnosis.append(rule.evaluate(snapshot, rules_cfg.get(rule.id, {})))

    cost_estimate = None
    if args.cost_profile:
        try:
            cost_estimate = build_cost_estimate(
                snapshot,
                args.cost_profile,
                load_cost_config(BASE_DIR / "config" / "cost.yaml"),
            )
        except CostConfigError as exc:
            print(f"[cost] {exc}", file=sys.stderr)
            return 2

    slo_evaluation = None
    if args.slo_profile:
        try:
            slo_evaluation = build_slo_evaluation(
                snapshot,
                args.slo_profile,
                load_slo_config(BASE_DIR / "config" / "slo.yaml"),
            )
        except SLOConfigError as exc:
            print(f"[slo] {exc}", file=sys.stderr)
            return 2
        breach = slo_breach_result(slo_evaluation)
        if breach is not None:
            diagnosis.append(breach)

    recommendations: list[dict[str, Any]] = []
    try:
        recommendations = build_recommendations(
            snapshot,
            diagnosis,
            slo_evaluation,
            load_recommend_config(BASE_DIR / "config" / "recommend.yaml"),
        )
    except (RecommendConfigError, OSError) as exc:
        # Recommendations are advisory; never block the report on a config issue.
        print(f"[recommend] skipped: {exc}", file=sys.stderr)

    report = _build_report(
        run_context["scenario"],
        snapshot,
        diagnosis,
        cost=cost_estimate,
        slo=slo_evaluation,
        recommendations=recommendations,
    )
    output_dir = run_context["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (output_dir / "report.json").write_text(render_json(report), encoding="utf-8")

    triggered = sum(1 for item in diagnosis if item.triggered)
    print(f"wrote {output_dir / 'report.md'}")
    print(f"wrote {output_dir / 'report.json'}")
    print(f"triggered_rules={triggered}")
    # TODO: add an opt-in non-zero exit mode when this analyzer becomes a CI gate.
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate LLM ops diagnosis reports.")
    parser.add_argument("--run", help="Path to reports/<scenario>-<ts> containing run.json")
    parser.add_argument("--scenario", help="Scenario name for direct mode")
    parser.add_argument("--since", help="Start time ISO-8601 for direct mode")
    parser.add_argument("--until", help="End time ISO-8601 for direct mode")
    parser.add_argument("--prometheus-url", help="Prometheus base URL for direct mode")
    parser.add_argument("--output", help="Output directory for direct mode")
    parser.add_argument("--step", default="15s", help="Prometheus query_range step")
    parser.add_argument("--cost-profile", help="Name under analyzer/config/cost.yaml profiles")
    parser.add_argument("--slo-profile", help="Name under analyzer/config/slo.yaml profiles")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Prometheus 연결/HTTP 실패 시 exit 2 로 종료 (기본은 빈 series 로 강등). 디버깅에 유용.",
    )
    return parser.parse_args()


def _resolve_run_context(args: argparse.Namespace) -> dict[str, Any]:
    if args.run:
        run_dir = Path(args.run)
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        return {
            "scenario": run_json["scenario"],
            "start": isoparse(run_json["start_iso"]),
            "end": isoparse(run_json["end_iso"]),
            "prometheus_url": run_json.get("prometheus_url", "http://localhost:9090"),
            "output_dir": run_dir,
        }

    missing = [
        name
        for name in ("scenario", "since", "until", "prometheus_url", "output")
        if getattr(args, name) is None
    ]
    if missing:
        raise SystemExit(
            "--run 또는 direct mode 인자 전체가 필요합니다: "
            + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        )

    return {
        "scenario": args.scenario,
        "start": isoparse(args.since),
        "end": isoparse(args.until),
        "prometheus_url": args.prometheus_url,
        "output_dir": Path(args.output),
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _build_report(
    scenario: str,
    snapshot: MetricSnapshot,
    diagnosis,
    cost: dict[str, Any] | None = None,
    slo: dict[str, Any] | None = None,
    recommendations: list[dict[str, Any]] | None = None,
) -> Report:
    start, end = snapshot.time_range
    duration_seconds = max((end - start).total_seconds(), 0.0)
    throughput = _series(snapshot, "requests_total")
    avg_latency = _series(snapshot, "avg_latency")
    p95 = _series(snapshot, "p95_latency")
    p99 = _series(snapshot, "p99_latency")
    error_rate = _series(snapshot, "error_rate")
    running = _series(snapshot, "requests_running")
    waiting = _series(snapshot, "requests_waiting")
    prompt_tokens = _series(snapshot, "prompt_token_rate")
    output_tokens = _series(snapshot, "output_token_rate")
    ttft = _series(snapshot, "ttft_p95")
    tpot = _series(snapshot, "tpot_p95")
    batch_size = _series(snapshot, "batch_size_max")
    kv_cache = _series(snapshot, "kv_cache_ratio")
    desired = _series(snapshot, "replicas_desired")
    ready = _series(snapshot, "replicas_ready")
    pending = _series(snapshot, "pod_pending_count")
    cpu = _series(snapshot, "cpu_usage_ratio")
    memory = _series(snapshot, "memory_bytes")

    triggered_suggestions = []
    seen = set()
    for result in diagnosis:
        if result.triggered and result.suggestion not in seen:
            triggered_suggestions.append(result.suggestion)
            seen.add(result.suggestion)
    if not triggered_suggestions:
        triggered_suggestions.append("현재 수집 구간에서 임계값을 넘은 진단 룰은 없습니다.")

    return Report(
        scenario=scenario,
        time_range=snapshot.time_range,
        summary={
            "duration_seconds": duration_seconds,
            "estimated_total_requests": int(round(throughput.mean() * duration_seconds)),
            "series_collected": sorted(snapshot.series.keys()),
        },
        performance={
            "avg_latency_seconds": _none_if_empty(avg_latency, avg_latency.mean()),
            "p95_latency_avg_seconds": _none_if_empty(p95, p95.mean()),
            "p95_latency_peak_seconds": _none_if_empty(p95, p95.max()),
            "p99_latency_avg_seconds": _none_if_empty(p99, p99.mean()),
            "p99_latency_peak_seconds": _none_if_empty(p99, p99.max()),
            "error_rate_avg": _none_if_empty(error_rate, error_rate.mean()),
            "error_rate_peak": _none_if_empty(error_rate, error_rate.max()),
            "throughput_avg_rps": _none_if_empty(throughput, throughput.mean()),
            "throughput_peak_rps": _none_if_empty(throughput, throughput.max()),
        },
        llm_state={
            "max_running": _none_if_empty(running, running.max()),
            "max_waiting": _none_if_empty(waiting, waiting.max()),
            "prompt_token_rate_avg": _none_if_empty(prompt_tokens, prompt_tokens.mean()),
            "output_token_rate_avg": _none_if_empty(output_tokens, output_tokens.mean()),
            "ttft_p95_peak_seconds": _none_if_empty(ttft, ttft.max()),
            "tpot_p95_peak_seconds": _none_if_empty(tpot, tpot.max()),
            "max_batch_size": _none_if_empty(batch_size, batch_size.max()),
            "kv_cache_ratio_avg": _none_if_empty(kv_cache, kv_cache.mean()),
        },
        k8s_state={
            "desired_replicas_first": _none_if_empty(desired, desired.first()),
            "desired_replicas_last": _none_if_empty(desired, desired.last()),
            "desired_replicas_max": _none_if_empty(desired, desired.max()),
            "ready_replicas_first": _none_if_empty(ready, ready.first()),
            "ready_replicas_last": _none_if_empty(ready, ready.last()),
            "ready_replicas_max": _none_if_empty(ready, ready.max()),
            "pending_pod_max": _none_if_empty(pending, pending.max()),
        },
        resource_state={
            "cpu_usage_ratio_avg": _none_if_empty(cpu, cpu.mean()),
            "cpu_usage_ratio_peak": _none_if_empty(cpu, cpu.max()),
            "memory_bytes_avg": _none_if_empty(memory, memory.mean()),
            "memory_bytes_peak": _none_if_empty(memory, memory.max()),
            "gpu": "현재 미수집",
        },
        cost=cost,
        slo=slo,
        recommendations=recommendations or [],
        diagnosis=diagnosis,
        improvements=triggered_suggestions,
    )


def _series(snapshot: MetricSnapshot, name: str) -> TimeSeries:
    return snapshot.series.get(name, TimeSeries(name=name, points=[]))


def _none_if_empty(series: TimeSeries, value):
    if series.length() == 0:
        return None
    return value


if __name__ == "__main__":
    raise SystemExit(main())
