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
from analyzer.workload import (
    WorkloadConfigError,
    build_workload_fit,
    load_workload_config,
    workload_slo_profile,
)

BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    args = _parse_args()
    run_context = _resolve_run_context(args)

    metrics_cfg = _load_yaml(
        _resolve_metrics_config(
            args.metrics_config or run_context.get("metrics_config_path")
        )
    )
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

    # Workload-fit judgment (optional). Drives suitability verdict + SLO auto-select
    # + workload-aware recommendations. Absent metrics (e.g. GPU on mock) are skipped.
    workload_name = args.workload or run_context.get("workload")
    workload_fit = None
    workload_cfg = None
    if workload_name:
        try:
            workload_cfg = load_workload_config(
                BASE_DIR / "config" / "workload-profiles.yaml"
            )
            workload_fit = build_workload_fit(snapshot, workload_name, workload_cfg)
        except WorkloadConfigError as exc:
            print(f"[workload] {exc}", file=sys.stderr)
            return 2

    # Explicit --slo-profile wins; otherwise fall back to the workload's slo_profile.
    slo_profile_name = args.slo_profile
    if not slo_profile_name and workload_cfg is not None:
        slo_profile_name = workload_slo_profile(workload_name, workload_cfg)

    slo_evaluation = None
    if slo_profile_name:
        try:
            slo_evaluation = build_slo_evaluation(
                snapshot,
                slo_profile_name,
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
            workload_fit=workload_fit,
        )
    except (RecommendConfigError, OSError) as exc:
        # Recommendations are advisory; never block the report on a config issue.
        print(f"[recommend] skipped: {exc}", file=sys.stderr)

    report = _build_report(
        run_context["scenario"],
        snapshot,
        diagnosis,
        k6=_load_k6_summary(run_context),
        cost=cost_estimate,
        slo=slo_evaluation,
        workload_fit=workload_fit,
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
    parser.add_argument(
        "--metrics-config",
        help=(
            "Metric config file path or analyzer/config file name. "
            "Default: metrics.yaml; vLLM runs usually use metrics-vllm.yaml."
        ),
    )
    parser.add_argument("--step", default="15s", help="Prometheus query_range step")
    parser.add_argument("--cost-profile", help="Name under analyzer/config/cost.yaml profiles")
    parser.add_argument("--slo-profile", help="Name under analyzer/config/slo.yaml profiles")
    parser.add_argument(
        "--workload",
        help=(
            "Name under analyzer/config/workload-profiles.yaml. 워크로드 부하 기준 판정 + "
            "SLO 자동선택 + 워크로드 인식 추천을 활성화."
        ),
    )
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
            "run_dir": run_dir,
            "k6_summary_path": run_json.get("k6_summary_path"),
            "metrics_config_path": run_json.get("metrics_config_path"),
            "workload": run_json.get("workload"),
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
        "run_dir": None,
        "k6_summary_path": None,
        "metrics_config_path": args.metrics_config,
        "workload": args.workload,
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve_metrics_config(value: str | None) -> Path:
    if not value:
        return BASE_DIR / "config" / "metrics.yaml"

    path = Path(value)
    if path.is_absolute():
        return path

    config_path = BASE_DIR / "config" / value
    if config_path.exists():
        return config_path
    return path


def _build_report(
    scenario: str,
    snapshot: MetricSnapshot,
    diagnosis,
    k6: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    slo: dict[str, Any] | None = None,
    workload_fit: dict[str, Any] | None = None,
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
    queue_wait = _series(snapshot, "queue_wait_p95")
    prompt_tokens_p95 = _series(snapshot, "prompt_tokens_p95")
    output_tokens_p95 = _series(snapshot, "output_tokens_p95")
    batch_size = _series(snapshot, "batch_size_max")
    kv_cache = _series(snapshot, "kv_cache_ratio")
    desired = _series(snapshot, "replicas_desired")
    ready = _series(snapshot, "replicas_ready")
    pending = _series(snapshot, "pod_pending_count")
    cpu = _series(snapshot, "cpu_usage_ratio")
    memory = _series(snapshot, "memory_bytes")
    gpu_utilization = _series(snapshot, "gpu_utilization")
    gpu_memory = _series(snapshot, "gpu_memory_used_ratio")

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
            "queue_wait_p95_peak_seconds": _none_if_empty(queue_wait, queue_wait.max()),
            "prompt_tokens_per_request_p95": _none_if_empty(
                prompt_tokens_p95,
                prompt_tokens_p95.max(),
            ),
            "output_tokens_per_request_p95": _none_if_empty(
                output_tokens_p95,
                output_tokens_p95.max(),
            ),
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
            "gpu_utilization_avg": _none_if_empty(
                gpu_utilization,
                gpu_utilization.mean(),
            ),
            "gpu_utilization_peak": _none_if_empty(
                gpu_utilization,
                gpu_utilization.max(),
            ),
            "gpu_memory_used_ratio_avg": _none_if_empty(
                gpu_memory,
                gpu_memory.mean(),
            ),
            "gpu_memory_used_ratio_peak": _none_if_empty(
                gpu_memory,
                gpu_memory.max(),
            ),
            "gpu": (
                "collected"
                if gpu_utilization.length() or gpu_memory.length()
                else "현재 미수집"
            ),
        },
        k6=k6,
        cost=cost,
        slo=slo,
        workload_fit=workload_fit,
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


def _load_k6_summary(run_context: dict[str, Any]) -> dict[str, Any] | None:
    run_dir = run_context.get("run_dir")
    summary_path = run_context.get("k6_summary_path")
    if not run_dir or not summary_path:
        return None
    path = Path(run_dir) / str(summary_path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return _summarize_k6(payload)


def _summarize_k6(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
    duration = _metric_values(metrics, "http_req_duration")
    failed = _metric_values(metrics, "http_req_failed")
    checks = _metric_values(metrics, "checks")
    http_reqs = _metric_values(metrics, "http_reqs")
    vus = _metric_values(metrics, "vus")
    vus_max = _metric_values(metrics, "vus_max")
    vus_peak = _max_value(
        _value(vus, "max"),
        _value(vus, "value"),
        _value(vus_max, "max"),
        _value(vus_max, "value"),
    )
    return {
        "http_req_duration_p50_ms": _value(duration, "med"),
        "http_req_duration_p95_ms": _value(duration, "p(95)"),
        "http_req_duration_p99_ms": _value(duration, "p(99)"),
        "http_req_failed_rate": _value(failed, "rate"),
        "checks_success_rate": _value(checks, "rate"),
        "request_count": _value(http_reqs, "count"),
        "vus_peak": vus_peak,
        "tagged_latency_p95_ms": _tagged_latency_p95(metrics),
    }


def _metric_values(metrics: dict[str, Any], name: str) -> dict[str, Any]:
    metric = metrics.get(name, {})
    values = metric.get("values", {}) if isinstance(metric, dict) else {}
    return values if isinstance(values, dict) else {}


def _value(values: dict[str, Any], key: str) -> float | None:
    value = values.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_value(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _tagged_latency_p95(metrics: dict[str, Any]) -> dict[str, float]:
    tagged: dict[str, float] = {}
    prefix = "http_req_duration{"
    for name, metric in metrics.items():
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        values = metric.get("values", {}) if isinstance(metric, dict) else {}
        p95 = _value(values, "p(95)") if isinstance(values, dict) else None
        if p95 is None:
            continue
        tags = _parse_k6_tags(name)
        label_parts = [
            tags.get("scenario_type"),
            tags.get("prompt_type"),
            tags.get("output_type"),
        ]
        label = "/".join(part for part in label_parts if part)
        if label:
            tagged[label] = p95
    return tagged


def _parse_k6_tags(metric_name: str) -> dict[str, str]:
    start = metric_name.find("{")
    end = metric_name.rfind("}")
    if start < 0 or end <= start:
        return {}
    tags = {}
    for item in metric_name[start + 1 : end].split(","):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        tags[key.strip()] = value.strip()
    return tags


if __name__ == "__main__":
    raise SystemExit(main())
