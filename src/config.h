// Tunable constants, deliberately gathered in one place (PRD US-207, US-209).
#pragma once

#include <Arduino.h>

namespace cfg {

inline constexpr const char* kFirmwareVersion = "0.1.5";

// --- Polling (PRD FR-12) ---------------------------------------------------
// Market hours move fast (M8 probes every 3 h, orders fill during the session);
// outside them the only thing that changes is the nightly run, once a day.
inline constexpr uint32_t kPollIntervalSessionMs = 90UL * 1000UL;
inline constexpr uint32_t kPollIntervalIdleMs    = 15UL * 60UL * 1000UL;
// Retry failed fetches promptly, including startup failures outside market hours.
inline constexpr uint32_t kPollIntervalRetryMs   = 30UL * 1000UL;

// GPW continuous trading plus a margin on both sides, local time, Mon-Fri.
inline constexpr int kSessionStartMinutes = 8 * 60 + 30;   // 08:30
inline constexpr int kSessionEndMinutes   = 17 * 60 + 30;  // 17:30

// --- Display ---------------------------------------------------------------
// Backlight uses 0-255. Manual adjustment never turns the panel fully off.
// The default applies until a brightness preference has been saved.
inline constexpr uint8_t kDisplayBrightness = 120;
inline constexpr uint8_t kDisplayBrightnessMin = 20;
inline constexpr uint8_t kDisplayBrightnessMax = 255;
inline constexpr uint8_t kDisplayBrightnessStep = 20;

// --- Screen rotation (PRD FR-13) -------------------------------------------
inline constexpr uint32_t kScreenRotationMs = 8UL * 1000UL;
// A manual arrow press pauses rotation; it resumes after this long untouched.
inline constexpr uint32_t kRotationResumeMs = 60UL * 1000UL;
// The alarm screen may be paged away from, but it comes back on its own.
inline constexpr uint32_t kAlarmReturnMs = 30UL * 1000UL;

// --- Network ---------------------------------------------------------------
inline constexpr uint32_t kHttpTimeoutMs = 10UL * 1000UL;
// Keep previously loaded data visible through short outages. When no data has
// ever loaded, show the connection error immediately instead of an empty screen.
inline constexpr uint8_t kMaxConsecutiveFailures = 3;

// WiFi reconnect backoff: 1s, 2s, 4s ... capped, so a router reboot does not
// turn into a busy loop.
inline constexpr uint32_t kWifiRetryBaseMs = 1000UL;
inline constexpr uint32_t kWifiRetryMaxMs  = 60UL * 1000UL;

// --- Time ------------------------------------------------------------------
inline constexpr const char* kNtpServer = "pl.pool.ntp.org";
// Poland: CET/CEST with EU daylight-saving rules.
inline constexpr const char* kTimezone = "CET-1CEST,M3.5.0,M10.5.0/3";

}  // namespace cfg
