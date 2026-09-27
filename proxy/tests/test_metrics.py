"""Tests for the null-vs-zero rule and the delta window.

These are the two places where a plausible-looking bug would quietly turn "the
pipeline is dead" into "everything reads zero, all is well".
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.metrics import (
    METRIC_BROKER_ORDERS,
    METRIC_STEP_STATUS,
    PROBE_MAX_AGE_SECONDS,
    Sample,
    delta_24h,
    latest,
    latest_fresh,
    read_series,
)
from tests.conftest import FakeMetricClient, make_series

NOW = datetime(2026, 8, 28, 20, 0, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())


def test_absent_series_reads_as_none_not_zero(config):
    client = FakeMetricClient({})
    grouped = read_series(client, config, METRIC_STEP_STATUS, label="step", now=NOW)
    assert grouped == {}
    assert latest(grouped.get("ingest")) is None


def test_measured_zero_is_preserved(config):
    """A real 0 (a failed step) must survive as 0, not collapse into absent."""
    client = FakeMetricClient(
        {METRIC_STEP_STATUS: [make_series([(0, NOW_EPOCH)], {"step": "sentiment"})]}
    )
    grouped = read_series(client, config, METRIC_STEP_STATUS, label="step", now=NOW)
    sample = latest(grouped["sentiment"])
    assert sample is not None
    assert sample.value == 0.0


def test_series_grouped_by_label(config):
    client = FakeMetricClient(
        {
            METRIC_STEP_STATUS: [
                make_series([(1, NOW_EPOCH)], {"step": "ingest"}),
                make_series([(0, NOW_EPOCH)], {"step": "analyze"}),
            ]
        }
    )
    grouped = read_series(client, config, METRIC_STEP_STATUS, label="step", now=NOW)
    assert set(grouped) == {"ingest", "analyze"}
    assert latest(grouped["ingest"]).value == 1.0
    assert latest(grouped["analyze"]).value == 0.0


def test_latest_picks_newest_point(config):
    client = FakeMetricClient(
        {
            METRIC_BROKER_ORDERS: [
                make_series(
                    [(140, NOW_EPOCH - 7200), (143, NOW_EPOCH)], {"status": "filled"}
                )
            ]
        }
    )
    grouped = read_series(client, config, METRIC_BROKER_ORDERS, label="status", now=NOW)
    assert latest(grouped["filled"]).value == 143.0


# --- probe freshness (PRD FR-7) -------------------------------------------


def test_probe_older_than_36h_reads_absent():
    stale = [Sample(value=1.0, epoch_seconds=NOW_EPOCH - PROBE_MAX_AGE_SECONDS - 60)]
    assert latest_fresh(stale, PROBE_MAX_AGE_SECONDS, now_epoch=NOW_EPOCH) is None


def test_probe_within_36h_is_used():
    fresh = [Sample(value=1.0, epoch_seconds=NOW_EPOCH - 3600)]
    sample = latest_fresh(fresh, PROBE_MAX_AGE_SECONDS, now_epoch=NOW_EPOCH)
    assert sample is not None and sample.value == 1.0


# --- 24 h delta (PRD FR-6) -------------------------------------------------


def test_delta_without_baseline_is_none_not_zero():
    """The whole point: no reference point means "unknown", never "+0"."""
    samples = [Sample(value=143.0, epoch_seconds=NOW_EPOCH)]
    assert delta_24h(samples, now_epoch=NOW_EPOCH) is None


def test_delta_computed_against_point_older_than_24h():
    samples = [
        Sample(value=143.0, epoch_seconds=NOW_EPOCH),
        Sample(value=140.0, epoch_seconds=NOW_EPOCH - 25 * 3600),
    ]
    assert delta_24h(samples, now_epoch=NOW_EPOCH) == 3


def test_delta_of_genuinely_unchanged_counter_is_zero():
    """An unchanged counter is 0 — distinct from the None case above."""
    samples = [
        Sample(value=143.0, epoch_seconds=NOW_EPOCH),
        Sample(value=143.0, epoch_seconds=NOW_EPOCH - 30 * 3600),
    ]
    assert delta_24h(samples, now_epoch=NOW_EPOCH) == 0


def test_delta_of_empty_series_is_none():
    assert delta_24h([], now_epoch=NOW_EPOCH) is None
    assert delta_24h(None, now_epoch=NOW_EPOCH) is None
