"""End-to-end replay test against captured Prometheus fixtures.

Each subdirectory under analyzer/tests/fixtures/ that contains both
`responses.json` and `_meta.json` becomes a test case. If no fixture has
been captured yet (only `.gitkeep` present), the whole module skips with
a clear message — capture one via:

    python -m analyzer.tools.capture_fixtures \
        --run reports/<scenario>-<ts> \
        --output analyzer/tests/fixtures/<name>

The point of this test is *not* to assert specific metric values (those
change per experiment). It asserts that:
  1. the collector's parsing logic accepts every real Prometheus payload
     without crashing (catches schema drift),
  2. fetch_snapshot returns at least one non-empty series for the core
     metrics (catches typos in metrics.yaml PromQL),
  3. every applicable rule evaluates without crashing,
  4. render_markdown + render_json produce a complete report.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from dateutil.parser import isoparse

from analyzer.collector import PrometheusClient
from analyzer.main import _build_report
from analyzer.report import render_json, render_markdown
from analyzer.rules import ALL_RULES
from analyzer.schemas import MetricSnapshot, TimeSeries

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
ANALYZER_ROOT = Path(__file__).parent.parent


def _fixture_dirs() -> list[Path]:
    if not FIXTURE_ROOT.exists():
        return []
    dirs = []
    for child in sorted(FIXTURE_ROOT.iterdir()):
        if child.is_dir() and (child / "responses.json").exists() and (
            child / "_meta.json"
        ).exists():
            dirs.append(child)
    return dirs


class FixtureReplayClient(PrometheusClient):
    """Drop-in PrometheusClient that returns pre-captured payloads."""

    def __init__(self, responses: dict[str, dict]):
        super().__init__(base_url="fixture://replay", strict=False)
        self._responses = responses

    def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> TimeSeries:
        payload = self._responses.get(promql)
        if payload is None:
            return TimeSeries(name=promql, points=[])
        return self._payload_to_timeseries(promql, payload)


_FIXTURES = _fixture_dirs()


@pytest.mark.skipif(
    not _FIXTURES,
    reason=(
        "no captured fixtures under analyzer/tests/fixtures/. "
        "run `python -m analyzer.tools.capture_fixtures --run reports/<dir> "
        "--output analyzer/tests/fixtures/<name>` once to enable replay tests."
    ),
)
@pytest.mark.parametrize(
    "fixture_dir", _FIXTURES, ids=[p.name for p in _FIXTURES]
)
def test_fixture_replay_end_to_end(fixture_dir: Path):
    responses = json.loads((fixture_dir / "responses.json").read_text(encoding="utf-8"))
    meta = json.loads((fixture_dir / "_meta.json").read_text(encoding="utf-8"))

    metrics_cfg = yaml.safe_load(
        (ANALYZER_ROOT / "config" / "metrics.yaml").read_text(encoding="utf-8")
    )
    rules_cfg = yaml.safe_load(
        (ANALYZER_ROOT / "config" / "rules.yaml").read_text(encoding="utf-8")
    )
    start = isoparse(meta["start_iso"])
    end = isoparse(meta["end_iso"])

    client = FixtureReplayClient(responses)
    snapshot = client.fetch_snapshot(metrics_cfg, start, end, meta.get("step", "15s"))

    # (2) core metrics must be non-empty in any real burst_traffic run
    assert isinstance(snapshot, MetricSnapshot)
    core_present = [
        m
        for m in ("requests_total", "requests_running", "p95_latency")
        if m in snapshot.series and snapshot.series[m].length() > 0
    ]
    assert core_present, (
        f"no core metric had data in fixture {fixture_dir.name}; "
        "likely PromQL typo or Prometheus scrape gap"
    )

    # (3) every applicable rule must evaluate without crashing
    diagnosis = []
    for rule_cls in ALL_RULES:
        rule = rule_cls()
        if rule.applies(snapshot):
            result = rule.evaluate(snapshot, rules_cfg.get(rule.id, {}))
            diagnosis.append(result)
            assert isinstance(result.evidence, dict)
            assert isinstance(result.suggestion, str) and result.suggestion

    # (4) render_markdown + render_json must produce a complete report
    report = _build_report(meta.get("scenario", "fixture"), snapshot, diagnosis)
    md = render_markdown(report)
    js = render_json(report)
    assert md.startswith("# LLM 운영 진단 리포트")
    # Section numbers shift as optional sections (cost, SLO) are added, so match
    # on the section title regardless of its number.
    assert re.search(r"## \d+\. 진단", md)
    assert re.search(r"## \d+\. 개선 방향", md)
    parsed_js = json.loads(js)
    assert parsed_js["scenario"] == meta.get("scenario", "fixture")
    assert "diagnosis" in parsed_js


def test_payload_to_timeseries_handles_empty_payload():
    """Sanity: the staticmethod must survive both None and malformed payloads."""
    assert PrometheusClient._payload_to_timeseries("x", None).length() == 0
    assert PrometheusClient._payload_to_timeseries("x", {}).length() == 0
    assert PrometheusClient._payload_to_timeseries(
        "x", {"status": "error"}
    ).length() == 0
    assert PrometheusClient._payload_to_timeseries(
        "x", {"status": "success", "data": {"result": []}}
    ).length() == 0


def test_payload_to_timeseries_parses_real_shape():
    """Sanity: a minimal real-shape payload yields the expected points."""
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {},
                    "values": [
                        [1715680800.0, "10"],
                        [1715680815.0, "12.5"],
                        [1715680830.0, "NaN"],
                    ],
                }
            ],
        },
    }
    ts = PrometheusClient._payload_to_timeseries("test", payload)
    # NaN sample dropped
    assert ts.length() == 2
    assert ts.points[0][1] == 10.0
    assert ts.points[1][1] == 12.5
