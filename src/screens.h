#pragma once

#include <Arduino.h>

#include "dashboard_client.h"
#include "dashboard_data.h"

// What the device is currently showing (PRD US-203, FR-15). These are three
// genuinely different answers and must never be conflated:
//   Alarm        — "the system is in a bad state"      (red, full screen)
//   Disconnected — "I do not know what is going on"    (blue-grey, no metrics)
//   Normal       — "everything is fine"                (rotating screens)
// Help and device information stay open until the user leaves them.
enum class DeviceState { Starting, Normal, Alarm, Disconnected, Help, DeviceInfo };

// Which rotating page is visible.
enum class Page { Nightly, Execution };
inline constexpr size_t kPageCount = 2;

// Everything the screens need that is not part of the payload itself.
struct RenderContext {
    DeviceState state = DeviceState::Starting;
    Page page         = Page::Nightly;

    // Local wall-clock epoch seconds, or 0 when NTP has not synced yet. Ages are
    // computed against this and suppressed entirely when it is 0, so the device
    // never invents an age from an unset clock.
    int64_t nowEpochSeconds = 0;

    // millis() when the last successful fetch landed; 0 if none ever did.
    uint32_t lastSuccessMillis = 0;
    bool hasEverSucceeded      = false;

    FetchError lastError    = FetchError::None;
    uint8_t consecutiveFail = 0;
    uint32_t totalFailures  = 0;

    bool rotationPaused = false;
};

void screensBegin();

// Adjust and remember backlight brightness; direction is -1 or +1.
void screensAdjustBrightness(int direction);

// Draws the whole display for the current state. Called only when something
// actually changed — the panel is not redrawn on every loop iteration.
void screensRender(const DashboardData& data, const RenderContext& ctx);

// Startup / WiFi-connecting screen, shown before the first fetch completes.
void screensRenderStarting(const char* message);
