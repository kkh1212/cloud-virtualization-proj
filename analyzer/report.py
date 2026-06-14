from __future__ import annotations

import json
from typing import Any

from analyzer.schemas import Report


def render_markdown(report: Report) -> str:
    triggered = [result for result in report.diagnosis if result.triggered]
    lines: list[str] = [
        "# LLM 운영 진단 리포트",
        "",
        "## 1. 테스트 요약",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 시나리오 | {report.scenario} |",
        f"| 시작 | {report.time_range[0].isoformat()} |",
        f"| 종료 | {report.time_range[1].isoformat()} |",
        f"| 총 요청 수(추정) | {_fmt(report.summary.get('estimated_total_requests'))} |",
        f"| 적용된 진단 룰 | {len(report.diagnosis)} |",
        f"| Triggered 룰 | {len(triggered)} |",
        "",
        "## 2. 성능 결과",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 평균 latency | {_fmt_seconds(report.performance.get('avg_latency_seconds'))} |",
        f"| p95 latency peak | {_fmt_seconds(report.performance.get('p95_latency_peak_seconds'))} |",
        f"| p99 latency peak | {_fmt_seconds(report.performance.get('p99_latency_peak_seconds'))} |",
        f"| error rate peak | {_fmt(report.performance.get('error_rate_peak'))} |",
        f"| throughput avg | {_fmt(report.performance.get('throughput_avg_rps'))} req/s |",
        f"| throughput peak | {_fmt(report.performance.get('throughput_peak_rps'))} req/s |",
        f"| k6 latency p50 | {_fmt_ms(_k6(report, 'http_req_duration_p50_ms'))} |",
        f"| k6 latency p95 | {_fmt_ms(_k6(report, 'http_req_duration_p95_ms'))} |",
        f"| k6 latency p99 | {_fmt_ms(_k6(report, 'http_req_duration_p99_ms'))} |",
        f"| k6 failed rate | {_fmt_percent(_k6(report, 'http_req_failed_rate'))} |",
        f"| k6 checks success rate | {_fmt_percent(_k6(report, 'checks_success_rate'))} |",
        f"| k6 request count | {_fmt(_k6(report, 'request_count'))} |",
        f"| k6 VU peak | {_fmt(_k6(report, 'vus_peak'))} |",
        "",
        "## 3. LLM 상태",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| max running | {_fmt(report.llm_state.get('max_running'))} |",
        f"| max waiting | {_fmt(report.llm_state.get('max_waiting'))} |",
        f"| prompt token throughput avg | {_fmt(report.llm_state.get('prompt_token_rate_avg'))} tok/s |",
        f"| output token throughput avg | {_fmt(report.llm_state.get('output_token_rate_avg'))} tok/s |",
        f"| TTFT p95 (peak) | {_fmt_seconds(report.llm_state.get('ttft_p95_peak_seconds'))} |",
        f"| inter-token latency p95 (peak) | {_fmt_seconds(report.llm_state.get('tpot_p95_peak_seconds'))} |",
        f"| queue wait p95 (peak) | {_fmt_seconds(report.llm_state.get('queue_wait_p95_peak_seconds'))} |",
        f"| prompt tokens/request p95 | {_fmt(report.llm_state.get('prompt_tokens_per_request_p95'))} |",
        f"| output tokens/request p95 | {_fmt(report.llm_state.get('output_tokens_per_request_p95'))} |",
        f"| max batch size | {_fmt(report.llm_state.get('max_batch_size'))} |",
        f"| KV-cache 사용률 avg (proxy) | {_fmt(report.llm_state.get('kv_cache_ratio_avg'))} |",
        "",
        "## 4. Kubernetes 상태",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| desired replicas first → last | {_fmt(report.k8s_state.get('desired_replicas_first'))} → {_fmt(report.k8s_state.get('desired_replicas_last'))} |",
        f"| ready replicas first → last | {_fmt(report.k8s_state.get('ready_replicas_first'))} → {_fmt(report.k8s_state.get('ready_replicas_last'))} |",
        f"| desired replicas max | {_fmt(report.k8s_state.get('desired_replicas_max'))} |",
        f"| ready replicas max | {_fmt(report.k8s_state.get('ready_replicas_max'))} |",
        f"| pending pod max | {_fmt(report.k8s_state.get('pending_pod_max'))} |",
        "",
        "## 5. 자원 상태",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| CPU 평균(request 대비) | {_fmt_ratio(report.resource_state.get('cpu_usage_ratio_avg'))} |",
        f"| CPU peak(request 대비) | {_fmt_ratio(report.resource_state.get('cpu_usage_ratio_peak'))} |",
        f"| memory avg | {_fmt_bytes(report.resource_state.get('memory_bytes_avg'))} |",
        f"| memory peak | {_fmt_bytes(report.resource_state.get('memory_bytes_peak'))} |",
        f"| GPU utilization avg | {_fmt_percent_ratio(report.resource_state.get('gpu_utilization_avg'))} |",
        f"| GPU utilization peak | {_fmt_percent_ratio(report.resource_state.get('gpu_utilization_peak'))} |",
        f"| GPU memory avg | {_fmt_percent_ratio(report.resource_state.get('gpu_memory_used_ratio_avg'))} |",
        f"| GPU memory peak | {_fmt_percent_ratio(report.resource_state.get('gpu_memory_used_ratio_peak'))} |",
        f"| GPU metric status | {_fmt(report.resource_state.get('gpu'))} |",
        "",
        "## 6. 비용 추정",
        "",
    ]

    if report.cost:
        currency = report.cost.get("currency", "USD")
        lines.extend(
            [
                "| 항목 | 값 |",
                "|---|---|",
                f"| cost profile | {report.cost.get('profile')} |",
                f"| estimated run cost | {_fmt_money(report.cost.get('estimated_run_cost'), currency)} |",
                f"| cost per 1K requests | {_fmt_money(report.cost.get('cost_per_1k_requests'), currency)} |",
                f"| cost per 1K tokens | {_fmt_money(report.cost.get('cost_per_1k_tokens'), currency)} |",
                f"| avg billable replicas | {_fmt(report.cost.get('avg_billable_replicas'))} |",
                f"| estimated tokens | {_fmt(report.cost.get('estimated_tokens'))} |",
            ]
        )
    else:
        lines.append("비용 profile 이 선택되지 않았습니다. `--cost-profile <name>` 으로 활성화할 수 있습니다.")

    lines.extend(["", "## 7. SLO 판정", ""])

    if report.slo:
        met = report.slo.get("met")
        verdict = "충족" if met else ("위반" if met is False else "평가 항목 없음")
        lines.append(f"- profile: `{report.slo.get('profile')}` / 종합 판정: **{verdict}**")
        lines.append("")
        checks = report.slo.get("checks", [])
        if checks:
            lines.extend(
                [
                    "| 지표 | 목표 | 관측 | 판정 |",
                    "|---|---|---|---|",
                ]
            )
            for check in checks:
                mark = "OK" if check.get("met") else "BREACH"
                lines.append(
                    f"| {check.get('metric')} | {check.get('comparison')} "
                    f"{_fmt(check.get('target'))} | {_fmt(check.get('observed'))} | {mark} |"
                )
        else:
            lines.append("이 run 에서 평가 가능한 SLO 지표가 없습니다(해당 메트릭 미수집).")
    else:
        lines.append("SLO profile 이 선택되지 않았습니다. `--slo-profile <name>` 으로 활성화할 수 있습니다.")

    lines.extend(["", "## 8. 진단", ""])

    if triggered:
        lines.extend(
            [
                "| Rule | Severity | Evidence |",
                "|---|---|---|",
            ]
        )
        for result in triggered:
            lines.append(
                f"| {result.rule_id} | {result.severity} | "
                f"`{json.dumps(result.evidence, ensure_ascii=False)}` |"
            )
    else:
        lines.append("Triggered 된 진단 룰이 없습니다.")

    lines.extend(["", "## 9. 권장 설정", ""])
    if report.recommendations:
        lines.extend(
            [
                "| 대상 | 현재 | 권장 | 근거 |",
                "|---|---|---|---|",
            ]
        )
        for rec in report.recommendations:
            target = rec.get('target')
            if rec.get('priority') == 'high':
                target = f"★ {target}"
            lines.append(
                f"| {target} | {rec.get('current')} | "
                f"{rec.get('recommended')} | {rec.get('rationale')} |"
            )
        lines.append("")
        lines.append(
            "> 권장값은 advisory 입니다. 적용 전 검토하고, 적용 후 재실험으로 검증하세요(Phase 5)."
        )
    else:
        lines.append("현재 수집 구간 기준 권장할 설정 변경이 없습니다.")

    tagged_latency = (report.k6 or {}).get("tagged_latency_p95_ms", {})
    if tagged_latency:
        lines.extend(["", "### k6 요청 유형별 p95 latency", ""])
        lines.extend(["| 요청 유형 | p95 |", "|---|---:|"])
        for label, value in sorted(tagged_latency.items()):
            lines.append(f"| {label} | {_fmt_ms(value)} |")

    lines.extend(["", "## 10. 개선 방향", ""])
    for item in report.improvements:
        lines.append(f"- {item}")

    lines.extend(_render_workload_fit(report))
    lines.append("")
    return "\n".join(lines)


_VERDICT_LABEL = {
    "suitable": "기준 통과(suitable)",
    "partially_suitable": "주의(partially_suitable)",
    "unsuitable": "기준 미통과(unsuitable)",
}


def _render_workload_fit(report: Report) -> list[str]:
    lines = ["", "## 11. 워크로드 부하 기준 판정", ""]
    fit = report.workload_fit
    if not fit:
        lines.append(
            "워크로드가 지정되지 않았습니다. `--workload <name>` 으로 활성화할 수 있습니다."
        )
        return lines

    verdict = fit.get("verdict")
    label = _VERDICT_LABEL.get(verdict, "평가 항목 없음")
    score = fit.get("score")
    shape = fit.get("request_shape", "")
    lines.append(f"- workload: `{fit.get('workload')}`" + (f" ({shape})" if shape else ""))
    score_text = f" / score: {_fmt(score)} / 100" if score is not None else ""
    bottleneck = fit.get("bottleneck")
    bn_text = f" / 주요 병목: {bottleneck}" if bottleneck else ""
    lines.append(f"- 기준 판정: **{label}**{score_text}{bn_text}")
    lines.append("")

    checks = fit.get("checks", [])
    if checks:
        lines.extend(["| 지표 | 목표 | 관측 | 판정 |", "|---|---|---|---|"])
        for check in checks:
            comparison = "≤" if check.get("direction") == "max" else "≥"
            met = check.get("met")
            mark = "미수집(SKIP)" if met is None else ("OK" if met else "FAIL")
            observed = check.get("observed")
            lines.append(
                f"| {check.get('metric')} ({check.get('agg')}) "
                f"| {comparison} {_fmt(check.get('target'))} "
                f"| {'현재 미수집' if observed is None else _fmt(observed)} | {mark} |"
            )
    else:
        lines.append("이 워크로드에 정의된 임계값이 없습니다.")
    return lines


def render_json(report: Report) -> str:
    return report.model_dump_json(indent=2)


def _fmt(value: Any) -> str:
    if value is None:
        return "현재 미수집"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _fmt_seconds(value: Any) -> str:
    if value is None:
        return "현재 미수집"
    return f"{float(value):.3f}s"


def _fmt_ms(value: Any) -> str:
    if value is None:
        return "현재 미수집"
    return f"{float(value):.1f}ms"


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "현재 미수집"
    return f"{float(value) * 100:.2f}%"


def _fmt_percent_ratio(value: Any) -> str:
    if value is None:
        return 'not collected'
    number = float(value)
    if number > 1:
        return f"{number:.2f}%"
    return f"{number * 100:.2f}%"


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return "현재 미수집"
    return f"{float(value):.2f}x"


def _fmt_bytes(value: Any) -> str:
    if value is None:
        return "현재 미수집"
    size = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.2f} {units[index]}"


def _fmt_money(value: Any, currency: Any) -> str:
    if value is None:
        return "현재 미수집"
    return f"{float(value):.6f} {currency}"


def _k6(report: Report, key: str):
    if not report.k6:
        return None
    return report.k6.get(key)
