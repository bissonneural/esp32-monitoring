"""Reads the gpw_* series this dashboard needs from Google Cloud Monitoring.

Ground truth for the catalogue is ``src/gpw/common/metrics.py`` in the gpw_radar
repository; this module reads a deliberate subset of it (PRD FR-4).

Two rules dominate this file:

1. **Absent is not zero.** gpw_radar emits one-hot series over a full label domain,
   so a missing point means "the run never happened", not "the value was 0". Every
   reader here returns ``None`` for absent and lets the caller render a dash.
2. **Timestamps are seconds.** The emitter writes unix seconds; a milliseconds
   assumption is what once made a Grafana panel report "57 years ago".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Config
from app.ports import MetricServiceClient

logger = logging.getLogger(__name__)

METRIC_TYPE_PREFIX = "custom.googleapis.com/"

# Series read by this proxy (PRD FR-4).
METRIC_NIGHTLY_LAST_SUCCESS = "gpw_nightly_last_success_timestamp_seconds"
METRIC_NIGHTLY_DURATION = "gpw_nightly_duration_seconds"
METRIC_STEP_STATUS = "gpw_step_status"
METRIC_BROKER_SESSION_UP = "gpw_broker_session_up"
METRIC_EXEC_INHIBITOR_ACTIVE = "gpw_exec_inhibitor_active"
METRIC_EXEC_LAST_RECONCILE = "gpw_exec_last_reconcile_timestamp_seconds"
METRIC_BROKER_ORDERS = "gpw_broker_orders"
METRIC_OPEN_POSITIONS = "gpw_open_positions"
METRIC_IBKR_2FA_REQUIRED = "gpw_ibkr_2fa_required"
METRIC_ESPI_REPORTS_TOTAL = "gpw_espi_reports_total"
METRIC_FIRECRAWL_CREDITS_REMAINING = "gpw_firecrawl_credits_remaining"
METRIC_FIRECRAWL_CREDITS_PLAN = "gpw_firecrawl_credits_plan"

# Bound individual calls so an unavailable upstream cannot occupy a worker
# beyond the function's request deadline through client-library retries.
METRIC_RPC_TIMEOUT_SECONDS = 5.0

#: The 7 nightly steps, in pipeline order (gpw_radar STEP_LABELS).
STEP_LABELS: tuple[str, ...] = (
    "ingest",
    "factors",
    "events",
    "sentiment",
    "analyze",
    "resolve",
    "report",
)

#: Order statuses the device shows. gpw_radar emits all nine; these are the three
#: that answer "did anything trade, and did anything break".
REPORTED_ORDER_STATUSES: tuple[str, ...] = ("filled", "unfilled", "rejected")

#: How far back to look for a series' most recent point. A nightly that has not
#: run in two days is long past the alarm threshold anyway.
LOOKBACK_HOURS = 48

#: exec-health-probe runs every 3 h; past this the last reading is not evidence of
#: the current state and is reported absent instead ("brak probe" on the device).
PROBE_MAX_AGE_SECONDS = 36 * 3600

#: Window for the 24 h increment on cumulative order counters.
DELTA_WINDOW_SECONDS = 24 * 3600


class MonitoringError(Exception):
    """Cloud Monitoring could not be read. Never carries provider text upward."""


@dataclass(frozen=True)
class Sample:
    """One time-series point: its value and when it was recorded."""

    value: float
    epoch_seconds: int


def _point_value(point: Any) -> float | None:
    """Extract the numeric value from a point, whichever typed field carries it."""
    value = getattr(point, "value", None)
    if value is None:
        return None
    for attribute in ("int64_value", "double_value", "bool_value"):
        if hasattr(value, attribute):
            raw = getattr(value, attribute)
            # Protobuf scalars default to 0/False rather than being absent, so a
            # zero on one field is only meaningful if the others are unset too.
            if raw not in (0, 0.0, False):
                return float(raw)
    # All fields zero-valued: the point genuinely holds zero.
    for attribute in ("int64_value", "double_value", "bool_value"):
        if hasattr(value, attribute):
            return float(getattr(value, attribute))
    return None


def _point_epoch(point: Any) -> int | None:
    interval = getattr(point, "interval", None)
    if interval is None:
        return None
    end_time = getattr(interval, "end_time", None)
    if end_time is None:
        return None
    seconds = getattr(end_time, "seconds", None)
    if seconds is not None:
        return int(seconds)
    if isinstance(end_time, datetime):
        return int(end_time.timestamp())
    return None


def _series_key(series: Any, label: str | None) -> str | None:
    """The label value identifying a series within a one-hot family."""
    if label is None:
        return ""
    metric = getattr(series, "metric", None)
    if metric is None:
        return None
    labels = getattr(metric, "labels", {}) or {}
    return labels.get(label)


def read_series(
    client: MetricServiceClient,
    config: Config,
    metric_name: str,
    *,
    label: str | None = None,
    now: datetime | None = None,
) -> dict[str, list[Sample]]:
    """Return every point in the lookback window, keyed by label value.

    A metric with no label yields a single entry under "". Points come back
    newest-first, which is what the delta computation below relies on.
    """

    reference = now or datetime.now(UTC)
    start = reference - timedelta(hours=LOOKBACK_HOURS)

    request: dict[str, Any] = {
        "name": config.project_name,
        "filter": f'metric.type = "{METRIC_TYPE_PREFIX}{metric_name}"',
        "interval": {
            "start_time": {"seconds": int(start.timestamp())},
            "end_time": {"seconds": int(reference.timestamp())},
        },
        "view": "FULL",
    }

    try:
        response = client.list_time_series(
            request=request, timeout=METRIC_RPC_TIMEOUT_SECONDS, retry=None
        )
    except Exception as error:  # noqa: BLE001 - classified, never surfaced verbatim
        logger.error(
            "list_time_series_failed",
            extra={"metric": metric_name, "error_type": type(error).__name__},
        )
        raise MonitoringError(metric_name) from error

    grouped: dict[str, list[Sample]] = {}
    for series in response:
        key = _series_key(series, label)
        if key is None:
            continue
        samples: list[Sample] = []
        for point in getattr(series, "points", []) or []:
            value = _point_value(point)
            epoch = _point_epoch(point)
            if value is None or epoch is None:
                continue
            samples.append(Sample(value=value, epoch_seconds=epoch))
        samples.sort(key=lambda sample: sample.epoch_seconds, reverse=True)
        grouped.setdefault(key, []).extend(samples)

    for samples in grouped.values():
        samples.sort(key=lambda sample: sample.epoch_seconds, reverse=True)
    return grouped


def latest(samples: list[Sample] | None) -> Sample | None:
    """Most recent sample, or None when the series had no points at all."""
    if not samples:
        return None
    return samples[0]


def latest_fresh(
    samples: list[Sample] | None, max_age_seconds: int, *, now_epoch: int
) -> Sample | None:
    """Most recent sample, but only if it is younger than ``max_age_seconds``.

    Used for the probe-driven series: a Friday reading presented on Monday would
    make a dead Gateway look identical to a healthy weekend (PRD FR-7).
    """
    sample = latest(samples)
    if sample is None:
        return None
    if now_epoch - sample.epoch_seconds > max_age_seconds:
        return None
    return sample


def delta_24h(samples: list[Sample] | None, *, now_epoch: int) -> int | None:
    """Increment of a cumulative counter over the last 24 h.

    Returns None — never 0 — when there is no point old enough to subtract from.
    "I cannot tell" and "nothing happened" are different answers, and the device
    renders them differently (PRD FR-6).
    """
    if not samples:
        return None
    current = samples[0]
    cutoff = now_epoch - DELTA_WINDOW_SECONDS
    baseline = next(
        (sample for sample in samples if sample.epoch_seconds <= cutoff), None
    )
    if baseline is None:
        return None
    return int(current.value) - int(baseline.value)
