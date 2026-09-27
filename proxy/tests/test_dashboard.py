"""Tests for the assembled payload and the alarm verdict (PRD FR-14)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.dashboard import NIGHTLY_ALARM_AGE_SECONDS, build_dashboard
from app.metrics import (
    METRIC_BROKER_ORDERS,
    METRIC_BROKER_SESSION_UP,
    METRIC_ESPI_REPORTS_TOTAL,
    METRIC_EXEC_INHIBITOR_ACTIVE,
    METRIC_FIRECRAWL_CREDITS_PLAN,
    METRIC_FIRECRAWL_CREDITS_REMAINING,
    METRIC_IBKR_2FA_REQUIRED,
    METRIC_NIGHTLY_DURATION,
    METRIC_NIGHTLY_LAST_SUCCESS,
    METRIC_OPEN_POSITIONS,
    METRIC_STEP_STATUS,
    MonitoringError,
)
from tests.conftest import FailingMetricClient, FakeMetricClient, make_series

NOW = datetime(2026, 8, 28, 20, 0, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())


def healthy_series(nightly_age_hours: float = 8.0) -> dict:
    last_success = NOW_EPOCH - int(nightly_age_hours * 3600)
    return {
        METRIC_NIGHTLY_LAST_SUCCESS: [make_series([(last_success, last_success)])],
        METRIC_STEP_STATUS: [
            make_series([(1, last_success)], {"step": step})
            for step in (
                "ingest",
                "factors",
                "events",
                "sentiment",
                "analyze",
                "resolve",
                "report",
            )
        ],
        METRIC_BROKER_SESSION_UP: [make_series([(1, NOW_EPOCH - 3600)])],
        METRIC_EXEC_INHIBITOR_ACTIVE: [make_series([(0, NOW_EPOCH - 3600)])],
        METRIC_BROKER_ORDERS: [
            make_series(
                [(143, NOW_EPOCH), (140, NOW_EPOCH - 25 * 3600)], {"status": "filled"}
            )
        ],
        METRIC_OPEN_POSITIONS: [make_series([(7, NOW_EPOCH - 8 * 3600)])],
    }


def test_healthy_run_is_ok(config):
    payload = build_dashboard(FakeMetricClient(healthy_series()), config, now=NOW)
    assert payload["overallStatus"] == "ok"
    assert payload["nightly"]["stepsOk"] == 7
    assert payload["nightly"]["stepsTotal"] == 7
    assert payload["nightly"]["failedSteps"] == []
    assert payload["nightly"]["lastRunStatus"] == "completed"
    assert payload["nightly"]["lastRunEpochSeconds"] == NOW_EPOCH - 8 * 3600
    assert payload["nightly"]["stepsSkipped"] == 0
    assert payload["execution"]["sessionUp"] is True
    assert payload["execution"]["inhibitorActive"] is False
    assert payload["execution"]["orders"]["filled"] == {"total": 143, "delta24h": 3}
    assert payload["generatedAt"].endswith("Z")


def test_stale_nightly_triggers_alarm(config):
    """Past 26 h the verdict flips, regardless of how healthy everything else is."""
    series = healthy_series(nightly_age_hours=(NIGHTLY_ALARM_AGE_SECONDS / 3600) + 1)
    payload = build_dashboard(FakeMetricClient(series), config, now=NOW)
    assert payload["overallStatus"] == "alarm"


def test_missing_nightly_metric_is_alarm_not_ok(config):
    """No point at all must not read as a healthy system (PRD FR-14)."""
    series = healthy_series()
    del series[METRIC_NIGHTLY_LAST_SUCCESS]
    payload = build_dashboard(FakeMetricClient(series), config, now=NOW)
    assert payload["overallStatus"] == "alarm"
    assert payload["nightly"]["lastSuccessEpochSeconds"] is None


@pytest.mark.parametrize("missing", [False, True], ids=["stale", "missing"])
@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # Summer: Polish midnight is 22:00 UTC on the preceding day.
        ("2026-09-26T21:59:59+00:00", "alarm"),
        ("2026-09-26T22:00:00+00:00", "ok"),
        ("2026-09-27T21:59:59+00:00", "ok"),
        ("2026-09-27T22:00:00+00:00", "alarm"),
        # Winter: Polish midnight is 23:00 UTC on the preceding day.
        ("2026-01-03T22:59:59+00:00", "alarm"),
        ("2026-01-03T23:00:00+00:00", "ok"),
        ("2026-01-04T22:59:59+00:00", "ok"),
        ("2026-01-04T23:00:00+00:00", "alarm"),
        # The Sunday exemption also ends correctly after each clock change.
        ("2026-03-29T21:59:59+00:00", "ok"),
        ("2026-03-29T22:00:00+00:00", "alarm"),
        ("2026-10-25T22:59:59+00:00", "ok"),
        ("2026-10-25T23:00:00+00:00", "alarm"),
    ],
)
def test_missing_or_stale_nightly_is_allowed_only_on_polish_sunday(
    config, missing, timestamp, expected
):
    now = datetime.fromisoformat(timestamp)
    last_success = int(now.timestamp()) - 30 * 3600
    series = (
        {}
        if missing
        else {
            METRIC_NIGHTLY_LAST_SUCCESS: [make_series([(last_success, last_success)])]
        }
    )

    payload = build_dashboard(FakeMetricClient(series), config, now=now)

    assert payload["overallStatus"] == expected
    assert payload["nightly"]["lastSuccessEpochSeconds"] == (
        None if missing else last_success
    )


@pytest.mark.parametrize("extra_seconds", [0, 1])
def test_weekday_nightly_alarm_threshold_remains_26_hours(config, extra_seconds):
    last_success = NOW_EPOCH - NIGHTLY_ALARM_AGE_SECONDS - extra_seconds
    series = {
        METRIC_NIGHTLY_LAST_SUCCESS: [make_series([(last_success, last_success)])]
    }
    payload = build_dashboard(FakeMetricClient(series), config, now=NOW)
    assert payload["overallStatus"] == ("alarm" if extra_seconds else "ok")


def test_sunday_does_not_hide_monitoring_failure(config):
    sunday = datetime(2026, 9, 27, 12, tzinfo=UTC)
    with pytest.raises(MonitoringError):
        build_dashboard(FailingMetricClient(), config, now=sunday)


def test_failed_step_is_named(config):
    series = healthy_series()
    last_success = NOW_EPOCH - 8 * 3600
    series[METRIC_STEP_STATUS] = [
        make_series([(1, last_success)], {"step": "ingest"}),
        make_series([(0, last_success)], {"step": "sentiment"}),
    ]
    payload = build_dashboard(FakeMetricClient(series), config, now=NOW)
    assert payload["nightly"]["failedSteps"] == ["sentiment"]
    assert payload["nightly"]["stepsOk"] == 1
    # Only steps that reported are counted, so a partial emission cannot pose
    # as a full clean run.
    assert payload["nightly"]["stepsTotal"] == 2
    # Noncritical steps may fail in an overall completed run.
    assert payload["nightly"]["lastRunStatus"] == "completed"


def test_absent_everything_yields_nulls_not_zeros(config):
    payload = build_dashboard(FakeMetricClient({}), config, now=NOW)
    execution = payload["execution"]
    assert execution["sessionUp"] is None
    assert execution["inhibitorActive"] is None
    assert execution["openPositions"] is None
    assert execution["twoFactorRequired"] is None
    assert execution["orders"]["filled"] == {"total": None, "delta24h": None}
    assert payload["nightly"]["stepsOk"] is None
    for field in (
        "lastRunStatus",
        "lastRunEpochSeconds",
        "stepsSkipped",
        "espiReportsTotal",
        "firecrawlCreditsRemaining",
        "firecrawlCreditsPlan",
    ):
        assert payload["nightly"][field] is None


def test_stale_probe_reported_absent(config):
    """A 40 h old probe is not evidence of the Gateway's current state."""
    series = healthy_series()
    series[METRIC_BROKER_SESSION_UP] = [make_series([(1, NOW_EPOCH - 40 * 3600)])]
    series[METRIC_EXEC_INHIBITOR_ACTIVE] = [make_series([(0, NOW_EPOCH - 40 * 3600)])]
    payload = build_dashboard(FakeMetricClient(series), config, now=NOW)
    assert payload["execution"]["sessionUp"] is None
    assert payload["execution"]["inhibitorActive"] is None


def test_monitoring_failure_raises(config):
    with pytest.raises(MonitoringError):
        build_dashboard(FailingMetricClient(), config, now=NOW)


def test_latest_failed_attempt_does_not_borrow_previous_successful_steps(config):
    series = healthy_series()
    failed_at = NOW_EPOCH - 3600
    series[METRIC_NIGHTLY_DURATION] = [make_series([(21, failed_at)])]
    series[METRIC_STEP_STATUS].append(
        make_series([(0, failed_at)], {"step": "resolve"})
    )
    payload = build_dashboard(FakeMetricClient(series), config, now=NOW)
    nightly = payload["nightly"]
    assert nightly["lastRunStatus"] == "error"
    assert nightly["lastRunEpochSeconds"] == failed_at
    assert nightly["lastSuccessEpochSeconds"] == NOW_EPOCH - 8 * 3600
    assert nightly["durationSeconds"] == 21
    assert nightly["stepsTotal"] == 1
    assert nightly["stepsOk"] == 0
    assert nightly["failedSteps"] == ["resolve"]
    # The existing age alarm remains independent of the latest attempt's result.
    assert payload["overallStatus"] == "ok"


def test_status_uses_emission_timestamp_not_last_success_value(config):
    series = healthy_series()
    collected_at = NOW_EPOCH - 3600
    emitted_at = collected_at + 29
    series[METRIC_NIGHTLY_LAST_SUCCESS] = [make_series([(collected_at, emitted_at)])]
    series[METRIC_NIGHTLY_DURATION] = [make_series([(125, emitted_at)])]
    nightly = build_dashboard(FakeMetricClient(series), config, now=NOW)["nightly"]
    assert nightly["lastRunStatus"] == "completed"
    assert nightly["lastRunEpochSeconds"] == emitted_at
    assert nightly["lastSuccessEpochSeconds"] == collected_at
    assert nightly["stepsTotal"] is None


def test_skipped_steps_are_not_failures(config):
    series = healthy_series()
    last_success = NOW_EPOCH - 8 * 3600
    series[METRIC_STEP_STATUS] = [
        make_series([(1, last_success)], {"step": "ingest"}),
        make_series([(-1, last_success)], {"step": "analyze"}),
        make_series([(-1, last_success)], {"step": "resolve"}),
    ]
    nightly = build_dashboard(FakeMetricClient(series), config, now=NOW)["nightly"]
    assert nightly["lastRunStatus"] == "completed"
    assert nightly["failedSteps"] == []
    assert nightly["stepsOk"] == 1
    assert nightly["stepsSkipped"] == 2
    assert nightly["stepsTotal"] == 3


def test_new_steps_alone_cannot_determine_overall_run_result(config):
    series = healthy_series()
    previous = NOW_EPOCH - 8 * 3600
    series[METRIC_NIGHTLY_DURATION] = [make_series([(300, previous)])]
    series[METRIC_STEP_STATUS] = [
        make_series([(1, NOW_EPOCH - 3600)], {"step": "ingest"})
    ]
    nightly = build_dashboard(FakeMetricClient(series), config, now=NOW)["nightly"]
    assert nightly["lastRunStatus"] is None
    assert nightly["durationSeconds"] is None
    assert nightly["stepsOk"] == 1


@pytest.mark.parametrize(
    ("value", "age_hours", "expected"),
    [(0, 1, False), (1, 1, True), (1, 36, True), (0, 40, None), (1, 40, None)],
)
def test_two_factor_is_explicit_and_respects_probe_freshness(
    config, value, age_hours, expected
):
    series = healthy_series()
    series[METRIC_IBKR_2FA_REQUIRED] = [
        make_series([(value, NOW_EPOCH - age_hours * 3600)])
    ]
    execution = build_dashboard(FakeMetricClient(series), config, now=NOW)["execution"]
    assert execution["twoFactorRequired"] is expected
    assert execution["sessionUp"] is True


@pytest.mark.parametrize(("espi", "credits"), [(2747, 4827), (0, 0)])
def test_espi_and_firecrawl_preserve_latest_counts_including_zero(
    config, espi, credits
):
    series = healthy_series()
    sample_at = NOW_EPOCH - 8 * 3600
    series[METRIC_ESPI_REPORTS_TOTAL] = [make_series([(espi, sample_at)])]
    series[METRIC_FIRECRAWL_CREDITS_REMAINING] = [make_series([(credits, sample_at)])]
    series[METRIC_FIRECRAWL_CREDITS_PLAN] = [make_series([(5000, sample_at)])]
    nightly = build_dashboard(FakeMetricClient(series), config, now=NOW)["nightly"]
    assert nightly["espiReportsTotal"] == espi
    assert nightly["firecrawlCreditsRemaining"] == credits
    assert nightly["firecrawlCreditsPlan"] == 5000


def test_firecrawl_does_not_pair_current_balance_with_older_plan(config):
    series = healthy_series()
    series[METRIC_FIRECRAWL_CREDITS_REMAINING] = [make_series([(4827, NOW_EPOCH)])]
    series[METRIC_FIRECRAWL_CREDITS_PLAN] = [
        make_series([(5000, NOW_EPOCH - 24 * 3600)])
    ]
    nightly = build_dashboard(FakeMetricClient(series), config, now=NOW)["nightly"]
    assert nightly["firecrawlCreditsRemaining"] == 4827
    assert nightly["firecrawlCreditsPlan"] is None
