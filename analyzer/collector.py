from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

import requests

from analyzer.schemas import MetricSnapshot, TimeSeries

LOG = logging.getLogger(__name__)


class PrometheusError(RuntimeError):
    """Raised when --strict mode encounters a Prometheus failure that would
    otherwise be silently downgraded to an empty TimeSeries."""


class PrometheusClient:
    def __init__(self, base_url: str, strict: bool = False):
        self.base_url = base_url.rstrip("/")
        # strict=True turns connection/HTTP failures into PrometheusError so the
        # caller can fail fast (analyzer exit 2). Empty result sets are NOT
        # raised — they're a legitimate "metric absent" signal used by the
        # Rule.required_metrics gating mechanism (e.g. GPU rules).
        self.strict = strict

    def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> TimeSeries:
        url = f"{self.base_url}/api/v1/query_range"
        params = {
            "query": promql,
            "start": _to_prometheus_time(start),
            "end": _to_prometheus_time(end),
            "step": step,
        }
        try:
            response = requests.get(url, params=params, timeout=20)
        except requests.RequestException as exc:
            if self.strict:
                raise PrometheusError(
                    f"Prometheus request failed for {promql!r}: {exc}"
                ) from exc
            LOG.warning("Prometheus query failed: %s", exc)
            return TimeSeries(name=promql, points=[])

        if response.status_code >= 400:
            if self.strict:
                raise PrometheusError(
                    f"Prometheus returned HTTP {response.status_code} for {promql!r}"
                )
            LOG.warning(
                "Prometheus query returned HTTP %s for %s",
                response.status_code,
                promql,
            )
            return TimeSeries(name=promql, points=[])

        try:
            payload = response.json()
        except ValueError as exc:
            if self.strict:
                raise PrometheusError(
                    f"Prometheus returned invalid JSON for {promql!r}: {exc}"
                ) from exc
            LOG.warning("Prometheus returned invalid JSON: %s", exc)
            return TimeSeries(name=promql, points=[])

        if payload.get("status") != "success":
            if self.strict:
                raise PrometheusError(
                    f"Prometheus query was not successful for {promql!r}: {payload}"
                )
            LOG.warning("Prometheus query was not successful: %s", payload)
            return TimeSeries(name=promql, points=[])

        return self._payload_to_timeseries(promql, payload)

    @staticmethod
    def _payload_to_timeseries(promql: str, payload: dict | None) -> TimeSeries:
        """Convert a Prometheus query_range JSON payload into a TimeSeries.

        Pulled out as a staticmethod so the fixture-replay test can reuse the
        exact same parsing logic without going through requests.get().
        """
        if not payload or payload.get("status") != "success":
            return TimeSeries(name=promql, points=[])
        result = payload.get("data", {}).get("result", [])
        if not result:
            LOG.warning("Prometheus query returned no series: %s", promql)
            return TimeSeries(name=promql, points=[])

        values = result[0].get("values", [])
        points: list[tuple[datetime, float]] = []
        for raw_ts, raw_value in values:
            try:
                ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
                value = float(raw_value)
            except (TypeError, ValueError):
                LOG.warning("Skipping invalid Prometheus sample: %r", (raw_ts, raw_value))
                continue
            if not math.isfinite(value):
                LOG.debug("Skipping non-finite Prometheus sample: %r", (raw_ts, raw_value))
                continue
            points.append((ts, value))

        return TimeSeries(name=promql, points=points)

    def fetch_snapshot(
        self,
        metrics_yaml: dict[str, Any],
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> MetricSnapshot:
        return _fetch_snapshot(self, metrics_yaml, start, end, step)


def fetch_snapshot(
    metrics_yaml: dict[str, Any],
    start: datetime,
    end: datetime,
    step: str = "15s",
) -> MetricSnapshot:
    base_url = metrics_yaml.get("prometheus_url") or os.environ.get(
        "PROMETHEUS_URL",
        "http://localhost:9090",
    )
    return _fetch_snapshot(PrometheusClient(base_url), metrics_yaml, start, end, step)


def _fetch_snapshot(
    client: PrometheusClient,
    metrics_yaml: dict[str, Any],
    start: datetime,
    end: datetime,
    step: str,
) -> MetricSnapshot:
    series: dict[str, TimeSeries] = {}
    for logical_name, spec in metrics_yaml.get("metrics", {}).items():
        promql = spec["promql"]
        ts = client.query_range(promql, start, end, step)
        if ts.length() == 0:
            continue
        series[logical_name] = TimeSeries(name=logical_name, points=ts.points)
    return MetricSnapshot(time_range=(start, end), series=series)


def _to_prometheus_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return str(value.timestamp())
