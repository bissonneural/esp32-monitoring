"""Test doubles. No test in this suite touches the network or real GCP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.config import Config


@dataclass
class FakePayload:
    data: bytes


@dataclass
class FakeSecretResponse:
    payload: FakePayload


class FakeSecretClient:
    def __init__(self, token: str = "correct-token") -> None:
        self.token = token
        self.calls = 0

    def access_secret_version(self, *, name: str) -> FakeSecretResponse:
        self.calls += 1
        return FakeSecretResponse(payload=FakePayload(data=self.token.encode()))


class FailingSecretClient:
    def access_secret_version(self, *, name: str) -> FakeSecretResponse:
        raise RuntimeError("secret manager unreachable")


# --- Cloud Monitoring doubles ---------------------------------------------
# Shaped to match the protobuf surface the reader touches: series.metric.labels
# and series.points[].value/.interval.end_time.seconds.


@dataclass
class FakeTypedValue:
    int64_value: int = 0
    double_value: float = 0.0


@dataclass
class FakeEndTime:
    seconds: int


@dataclass
class FakeInterval:
    end_time: FakeEndTime


@dataclass
class FakePoint:
    value: FakeTypedValue
    interval: FakeInterval


@dataclass
class FakeMetric:
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeSeries:
    metric: FakeMetric
    points: list[FakePoint]


def make_series(
    points: list[tuple[float, int]], labels: dict[str, str] | None = None
) -> FakeSeries:
    """Build a series from (value, epoch_seconds) tuples."""
    return FakeSeries(
        metric=FakeMetric(labels=labels or {}),
        points=[
            FakePoint(
                value=FakeTypedValue(
                    int64_value=int(value) if float(value).is_integer() else 0,
                    double_value=0.0 if float(value).is_integer() else float(value),
                ),
                interval=FakeInterval(end_time=FakeEndTime(seconds=epoch)),
            )
            for value, epoch in points
        ],
    )


class FakeMetricClient:
    """Returns canned series per metric type, and records what was asked for."""

    def __init__(self, series_by_metric: dict[str, list[FakeSeries]]) -> None:
        self.series_by_metric = series_by_metric
        self.requests: list[dict[str, Any]] = []

    def list_time_series(
        self, request: dict[str, Any], *, timeout: float, retry: None
    ) -> list[FakeSeries]:
        self.requests.append(request)
        filter_text = request["filter"]
        for metric_name, series in self.series_by_metric.items():
            if f"custom.googleapis.com/{metric_name}" in filter_text:
                return series
        return []


class FailingMetricClient:
    def list_time_series(
        self, request: dict[str, Any], *, timeout: float, retry: None
    ) -> list[FakeSeries]:
        raise RuntimeError("monitoring API is down")


@pytest.fixture
def config() -> Config:
    return Config(
        project_id="test-project", region="europe-central2", secret_id="test-secret"
    )


@pytest.fixture
def secret_client() -> FakeSecretClient:
    return FakeSecretClient()
