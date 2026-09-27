"""Builds the /v1/dashboard payload (PRD §7.2)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Config
from app.metrics import (
    METRIC_BROKER_ORDERS,
    METRIC_BROKER_SESSION_UP,
    METRIC_ESPI_REPORTS_TOTAL,
    METRIC_EXEC_INHIBITOR_ACTIVE,
    METRIC_EXEC_LAST_RECONCILE,
    METRIC_FIRECRAWL_CREDITS_PLAN,
    METRIC_FIRECRAWL_CREDITS_REMAINING,
    METRIC_IBKR_2FA_REQUIRED,
    METRIC_NIGHTLY_DURATION,
    METRIC_NIGHTLY_LAST_SUCCESS,
    METRIC_OPEN_POSITIONS,
    METRIC_STEP_STATUS,
    PROBE_MAX_AGE_SECONDS,
    REPORTED_ORDER_STATUSES,
    STEP_LABELS,
    Sample,
    delta_24h,
    latest,
    latest_fresh,
    read_series,
)
from app.ports import MetricServiceClient

#: Outside Sunday, a nightly older than this is an alarm: a full day plus two
#: hours of slack for a late start (PRD FR-14).
NIGHTLY_ALARM_AGE_SECONDS = 26 * 3600
NIGHTLY_TIMEZONE = ZoneInfo("Europe/Warsaw")


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _order_count(
    samples_by_status: dict[str, list[Sample]], status: str, *, now_epoch: int
) -> dict[str, Any]:
    samples = samples_by_status.get(status)
    current = latest(samples)
    return {
        "total": int(current.value) if current is not None else None,
        "delta24h": delta_24h(samples, now_epoch=now_epoch),
    }


def build_dashboard(
    client: MetricServiceClient,
    config: Config,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read every needed series and assemble the device-facing payload.

    Raises MonitoringError if Cloud Monitoring cannot be read; a partial payload
    that silently looks like "all quiet" would be worse than an error.
    """

    reference = now or datetime.now(UTC)
    now_epoch = int(reference.timestamp())

    metrics = {
        METRIC_NIGHTLY_LAST_SUCCESS: None,
        METRIC_NIGHTLY_DURATION: None,
        METRIC_STEP_STATUS: "step",
        METRIC_BROKER_SESSION_UP: None,
        METRIC_EXEC_INHIBITOR_ACTIVE: None,
        METRIC_EXEC_LAST_RECONCILE: None,
        METRIC_BROKER_ORDERS: "status",
        METRIC_OPEN_POSITIONS: None,
        METRIC_IBKR_2FA_REQUIRED: None,
        METRIC_ESPI_REPORTS_TOTAL: None,
        METRIC_FIRECRAWL_CREDITS_REMAINING: None,
        METRIC_FIRECRAWL_CREDITS_PLAN: None,
    }
    # Independent reads share one snapshot boundary. Four workers keep the
    # extra fields from adding four sequential round trips on the Cardputer.
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {
            name: pool.submit(
                read_series, client, config, name, label=label, now=reference
            )
            for name, label in metrics.items()
        }
        series = {name: future.result() for name, future in pending.items()}

    nightly_success = series[METRIC_NIGHTLY_LAST_SUCCESS]
    nightly_duration = series[METRIC_NIGHTLY_DURATION]
    step_status = series[METRIC_STEP_STATUS]
    session_up = series[METRIC_BROKER_SESSION_UP]
    inhibitor = series[METRIC_EXEC_INHIBITOR_ACTIVE]
    last_reconcile = series[METRIC_EXEC_LAST_RECONCILE]
    orders = series[METRIC_BROKER_ORDERS]
    open_positions = series[METRIC_OPEN_POSITIONS]

    # --- nightly ---------------------------------------------------------
    success_sample = latest(nightly_success.get(""))
    last_success_epoch = (
        int(success_sample.value) if success_sample is not None else None
    )

    duration_sample = latest(nightly_duration.get(""))
    step_samples = {step: latest(step_status.get(step)) for step in STEP_LABELS}
    lifecycle = [success_sample, duration_sample, *step_samples.values()]
    run_epoch = max(
        (sample.epoch_seconds for sample in lifecycle if sample is not None),
        default=None,
    )
    # GPW emits duration on terminal runs, and last-success only for completed
    # runs, in the SAME timestamped bundle. Compare measurement timestamps, not
    # the timestamp value inside last-success. Side-step errors and skipped
    # steps do not necessarily fail the whole run.
    run_status = None
    if success_sample is not None and success_sample.epoch_seconds == run_epoch:
        run_status = "completed"
    elif duration_sample is not None and duration_sample.epoch_seconds == run_epoch:
        run_status = "error"
    if duration_sample is not None and duration_sample.epoch_seconds != run_epoch:
        duration_sample = None

    # Only steps that actually reported are counted, so a partial emission is
    # never rounded up into a clean "7/7".
    steps_seen = 0
    steps_ok = 0
    steps_skipped = 0
    failed_steps: list[str] = []
    for step in STEP_LABELS:
        sample = step_samples[step]
        if sample is None or sample.epoch_seconds != run_epoch:
            continue
        steps_seen += 1
        if sample.value >= 1:
            steps_ok += 1
        elif sample.value < 0:
            steps_skipped += 1
        else:
            failed_steps.append(step)

    # --- execution -------------------------------------------------------
    session_sample = latest_fresh(
        session_up.get(""), PROBE_MAX_AGE_SECONDS, now_epoch=now_epoch
    )
    inhibitor_sample = latest_fresh(
        inhibitor.get(""), PROBE_MAX_AGE_SECONDS, now_epoch=now_epoch
    )
    reconcile_sample = latest(last_reconcile.get(""))
    positions_sample = latest(open_positions.get(""))
    two_factor_sample = latest_fresh(
        series[METRIC_IBKR_2FA_REQUIRED].get(""),
        PROBE_MAX_AGE_SECONDS,
        now_epoch=now_epoch,
    )
    espi_sample = latest(series[METRIC_ESPI_REPORTS_TOTAL].get(""))
    credits_sample = latest(series[METRIC_FIRECRAWL_CREDITS_REMAINING].get(""))
    plan_sample = latest(series[METRIC_FIRECRAWL_CREDITS_PLAN].get(""))
    # Never pair a new balance with a plan limit from a different observation.
    if (
        credits_sample is None
        or plan_sample is None
        or credits_sample.epoch_seconds != plan_sample.epoch_seconds
    ):
        plan_sample = None

    # --- overall status (PRD FR-14) --------------------------------------
    # Nightly is not expected on Sundays in Poland. Keep the readings unchanged,
    # including nulls, but suppress the missing/stale nightly alarm for that day.
    nightly_expected = reference.astimezone(NIGHTLY_TIMEZONE).weekday() != 6
    if nightly_expected and (
        last_success_epoch is None
        or now_epoch - last_success_epoch > NIGHTLY_ALARM_AGE_SECONDS
    ):
        overall = "alarm"
    else:
        overall = "ok"

    return {
        "generatedAt": _timestamp(reference),
        "overallStatus": overall,
        "nightly": {
            "lastRunStatus": run_status,
            "lastRunEpochSeconds": run_epoch,
            "lastSuccessEpochSeconds": last_success_epoch,
            "durationSeconds": (
                round(duration_sample.value, 1) if duration_sample is not None else None
            ),
            "stepsOk": steps_ok if steps_seen else None,
            "stepsTotal": steps_seen if steps_seen else None,
            "stepsSkipped": steps_skipped if steps_seen else None,
            "failedSteps": failed_steps,
            "espiReportsTotal": int(espi_sample.value)
            if espi_sample is not None
            else None,
            "firecrawlCreditsRemaining": (
                int(credits_sample.value) if credits_sample is not None else None
            ),
            "firecrawlCreditsPlan": (
                int(plan_sample.value) if plan_sample is not None else None
            ),
        },
        "execution": {
            "twoFactorRequired": (
                bool(two_factor_sample.value >= 1)
                if two_factor_sample is not None
                else None
            ),
            "sessionUp": (
                bool(session_sample.value >= 1) if session_sample is not None else None
            ),
            "inhibitorActive": (
                bool(inhibitor_sample.value >= 1)
                if inhibitor_sample is not None
                else None
            ),
            "lastReconcileEpochSeconds": (
                int(reconcile_sample.value) if reconcile_sample is not None else None
            ),
            "orders": {
                status: _order_count(orders, status, now_epoch=now_epoch)
                for status in REPORTED_ORDER_STATUSES
            },
            "openPositions": (
                int(positions_sample.value) if positions_sample is not None else None
            ),
        },
    }
