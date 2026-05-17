from __future__ import annotations

from datetime import datetime, timezone

from analyzer.collector import PrometheusClient


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "status": "success",
            "data": {
                "result": [
                    {
                        "values": [
                            [1, "0.5"],
                            [2, "NaN"],
                            [3, "+Inf"],
                            [4, "1.5"],
                        ]
                    }
                ]
            },
        }


def test_query_range_skips_non_finite_samples(monkeypatch):
    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("analyzer.collector.requests.get", fake_get)
    client = PrometheusClient("http://prometheus")

    series = client.query_range(
        "some_query",
        datetime.fromtimestamp(0, tz=timezone.utc),
        datetime.fromtimestamp(10, tz=timezone.utc),
    )

    assert [value for _, value in series.points] == [0.5, 1.5]
