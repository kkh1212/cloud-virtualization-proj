"""Capture raw Prometheus query_range responses as analyzer test fixtures.

Run once after a real experiment (e.g. burst_traffic) to snapshot what
Prometheus actually returned for every PromQL in analyzer/config/metrics.yaml.
The captured fixture is then replayed by tests/test_fixture_integration.py
so that schema/format drift between the simulator, kube-prometheus-stack, and
the analyzer's parsing logic gets caught on the next pytest run.

Usage (preferred — reads time range from run.json):
    python -m analyzer.tools.capture_fixtures \\
        --run reports/burst_traffic-20260514T100710Z \\
        --output analyzer/tests/fixtures/burst_baseline

Usage (direct mode):
    python -m analyzer.tools.capture_fixtures \\
        --prometheus-url http://localhost:9090 \\
        --since 2026-05-14T10:00:00Z --until 2026-05-14T10:05:00Z \\
        --scenario burst_traffic \\
        --output analyzer/tests/fixtures/burst_baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
import yaml
from dateutil.parser import isoparse

BASE_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    args = _parse_args()
    if args.run:
        run_dir = Path(args.run)
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        url = run_json["prometheus_url"]
        start = isoparse(run_json["start_iso"])
        end = isoparse(run_json["end_iso"])
        scenario = run_json["scenario"]
        metrics_config = run_json.get("metrics_config_path")
    else:
        missing = [
            name
            for name in ("prometheus_url", "since", "until", "scenario")
            if getattr(args, name) is None
        ]
        if missing:
            print(
                "--run 또는 direct mode 인자 전체 필요: "
                + ", ".join(f"--{n.replace('_', '-')}" for n in missing),
                file=sys.stderr,
            )
            return 1
        url = args.prometheus_url
        start = isoparse(args.since)
        end = isoparse(args.until)
        scenario = args.scenario
        metrics_config = args.metrics_config

    metrics_cfg = yaml.safe_load(_resolve_metrics_config(metrics_config).read_text(encoding="utf-8"))
    metrics = metrics_cfg.get("metrics", {})
    if not metrics:
        print("metrics.yaml 에 metrics 가 비어있습니다.", file=sys.stderr)
        return 1

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    responses: dict[str, dict] = {}
    failures: list[str] = []
    base_url = url.rstrip("/")
    for logical_name, spec in metrics.items():
        promql = spec["promql"]
        params = {
            "query": promql,
            "start": str(start.timestamp()),
            "end": str(end.timestamp()),
            "step": args.step,
        }
        try:
            r = requests.get(
                f"{base_url}/api/v1/query_range", params=params, timeout=30
            )
            r.raise_for_status()
            payload = r.json()
            series_count = len(payload.get("data", {}).get("result", []))
            print(f"  [OK] {logical_name:<22} series={series_count}")
            responses[promql] = payload
        except requests.RequestException as exc:
            failures.append(f"{logical_name}: {exc}")
            print(f"  [FAIL] {logical_name}: {exc}", file=sys.stderr)

    (out_dir / "responses.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "_meta.json").write_text(
        json.dumps(
            {
                "scenario": scenario,
                "prometheus_url": url,
                "start_iso": start.isoformat(),
                "end_iso": end.isoformat(),
                "step": args.step,
                "metrics_config_path": str(_resolve_metrics_config(metrics_config)),
                "captured_metrics": sorted(metrics.keys()),
                "failed_metrics": failures,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"\nfixture saved to {out_dir}")
    print(f"  responses.json: {len(responses)} promql entries")
    if failures:
        print(f"  failures: {len(failures)} (see _meta.json failed_metrics)")
    print(
        "\n다음: analyzer/.venv/bin/pytest analyzer/tests "
        "→ test_fixture_replay_* 가 이 fixture 를 자동으로 픽업"
    )
    return 0 if not failures else 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture Prometheus responses as analyzer test fixtures.",
    )
    parser.add_argument(
        "--run", help="reports/<scenario>-<ts> (run.json 에서 시간/url 읽음)"
    )
    parser.add_argument("--prometheus-url", help="direct mode")
    parser.add_argument("--since", help="direct mode start ISO-8601")
    parser.add_argument("--until", help="direct mode end ISO-8601")
    parser.add_argument("--scenario", help="direct mode scenario label")
    parser.add_argument("--step", default="15s")
    parser.add_argument(
        "--metrics-config",
        help="Metric config file path or analyzer/config file name. Default: metrics.yaml.",
    )
    parser.add_argument(
        "--output", required=True, help="analyzer/tests/fixtures/<name>"
    )
    return parser.parse_args()


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


if __name__ == "__main__":
    raise SystemExit(main())
