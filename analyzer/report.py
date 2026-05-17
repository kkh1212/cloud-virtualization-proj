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
        "",
        "## 3. LLM 상태",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| max running | {_fmt(report.llm_state.get('max_running'))} |",
        f"| max waiting | {_fmt(report.llm_state.get('max_waiting'))} |",
        f"| prompt token throughput avg | {_fmt(report.llm_state.get('prompt_token_rate_avg'))} tok/s |",
        f"| output token throughput avg | {_fmt(report.llm_state.get('output_token_rate_avg'))} tok/s |",
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
        "| GPU | 현재 미수집 |",
        "",
        "## 6. 진단",
        "",
    ]

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

    lines.extend(
        [
            "",
            "## 7. 개선 방향",
            "",
        ]
    )
    for item in report.improvements:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


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
