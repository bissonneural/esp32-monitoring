// Dashboard payload model — mirrors the /v1/dashboard contract in
// tasks/prd-cardputer-gpw-monitor.md §7.2.
//
// The single most important rule of this file: a metric that has no data point is
// NOT zero. gpw_radar emits one-hot series where "no point" means "the run never
// happened" — rendering that as 0 would make a dead pipeline look like a quiet
// night. Every numeric field therefore carries its own `has` flag, and the
// screens draw a grey em dash whenever it is false.
#pragma once

#include <Arduino.h>

// A metric value that may be absent. `has == false` means "unknown", never zero.
template <typename T>
struct Maybe {
    bool has = false;
    T value  = T();

    void set(T v)
    {
        has   = true;
        value = v;
    }
    void clear()
    {
        has   = false;
        value = T();
    }
};

using MaybeInt    = Maybe<int32_t>;
using MaybeLong   = Maybe<int64_t>;
using MaybeFloat  = Maybe<float>;
using MaybeBool   = Maybe<bool>;

// Cumulative total plus its 24 h increment. `delta24h` is absent when the proxy
// had no reference point 24 h back — "(?)" on screen, never "(+0)".
struct OrderCount {
    MaybeInt total;
    MaybeInt delta24h;
};

enum class NightlyStatus { Completed, Error, Unknown };

// gpw nightly — the 7-step pipeline run and latest collection snapshots.
struct NightlySection {
    NightlyStatus lastRunStatus = NightlyStatus::Unknown;
    MaybeLong lastRunEpochSeconds;
    MaybeLong lastSuccessEpochSeconds;
    MaybeFloat durationSeconds;
    MaybeInt stepsOk;
    MaybeInt stepsTotal;
    MaybeInt stepsSkipped;
    MaybeInt espiReportsTotal;
    MaybeInt firecrawlCreditsRemaining;
    MaybeInt firecrawlCreditsPlan;
    // Names of steps whose gpw_step_status was 0. Empty when all passed.
    static constexpr size_t kMaxFailedSteps = 7;
    String failedSteps[kMaxFailedSteps];
    size_t failedStepCount = 0;
};

// M8 — IBKR execution.
struct ExecutionSection {
    // These flags come from exec-health-probe (every 3 h). A point older than 36 h is
    // reported absent by the proxy rather than as a stale last-known value.
    MaybeBool sessionUp;
    MaybeBool inhibitorActive;
    MaybeBool twoFactorRequired;
    MaybeLong lastReconcileEpochSeconds;
    OrderCount filled;
    OrderCount unfilled;
    OrderCount rejected;
    // Emitted by nightly, not by exec-reconcile — a different schedule from the
    // order counts above, which screen 2 annotates.
    MaybeInt openPositions;
};

// Overall verdict, computed by the proxy (PRD FR-14) so changing the alarm
// threshold never requires reflashing the device.
enum class OverallStatus { Ok, Alarm, Unknown };

struct DashboardData {
    OverallStatus overallStatus = OverallStatus::Unknown;
    NightlySection nightly;
    ExecutionSection execution;
    // Server-side timestamp of the response, seconds since epoch. Absent when the
    // payload did not carry a parsable generatedAt.
    MaybeLong generatedAtEpochSeconds;
};
