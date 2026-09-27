#include "screens.h"

#include <M5Cardputer.h>
#include <Preferences.h>
#include <WiFi.h>

#include "config.h"

namespace {

// ST7789V2, 240x135. At a font size readable from across a desk this leaves
// roughly six lines, which is what drives every layout decision below.
constexpr int kWidth  = 240;
constexpr int kHeight = 135;

// Palette. Grey is load-bearing: it is reserved for "unknown" so that a grey
// dash never reads like a green zero (PRD §6.5).
constexpr uint16_t kBg       = TFT_BLACK;
constexpr uint16_t kFg       = TFT_WHITE;
constexpr uint16_t kGood     = TFT_GREEN;
constexpr uint16_t kWarn     = TFT_YELLOW;
constexpr uint16_t kBad      = TFT_RED;
constexpr uint16_t kUnknown  = 0x8410;  // mid grey
constexpr uint16_t kHeader   = 0x5AEB;  // dim grey-blue
constexpr uint16_t kDiscBg   = 0x18E3;  // very dark blue-grey, deliberately not red

M5Canvas canvas(&M5Cardputer.Display);
Preferences g_displayPreferences;
bool g_displayPreferencesReady = false;

// "—" for a value we do not have. Drawn in grey by every caller.
constexpr const char* kNoData = "---";

// Formats an age in seconds as something a human reads at a glance.
String formatAge(int64_t seconds)
{
    if (seconds < 0) {
        // A future timestamp means a clock disagreement, not negative time.
        return "teraz";
    }
    if (seconds < 60) {
        return String(static_cast<int>(seconds)) + " s temu";
    }
    if (seconds < 3600) {
        return String(static_cast<int>(seconds / 60)) + " min temu";
    }
    if (seconds < 86400) {
        const int hours   = static_cast<int>(seconds / 3600);
        const int minutes = static_cast<int>((seconds % 3600) / 60);
        if (hours < 10 && minutes > 0) {
            return String(hours) + " h " + String(minutes) + " min";
        }
        return String(hours) + " h temu";
    }
    const int days = static_cast<int>(seconds / 86400);
    return String(days) + (days == 1 ? " dzien temu" : " dni temu");
}

String formatDuration(float seconds)
{
    if (seconds < 60.0f) {
        return String(static_cast<int>(seconds)) + " s";
    }
    return String(static_cast<int>(seconds / 60.0f)) + " min";
}

// Renders "143 (+3)" / "143 (?)" — the "(?)" case is what keeps an unknown
// delta from masquerading as "nothing happened".
String formatCount(const OrderCount& count)
{
    String text = count.total.has ? String(count.total.value) : String(kNoData);
    if (count.delta24h.has) {
        text += " (+" + String(count.delta24h.value) + ")";
    } else {
        text += " (?)";
    }
    return text;
}

// Battery gauge drawn as a small pictogram at the top right. Colour is a
// redundant cue only — the fill level carries the same information, so the
// indicator still reads correctly for a colour-blind viewer.
//
// `right` is the x coordinate of the icon's right edge; returns the x of its
// left edge so the caller can lay out whatever sits beside it.
int drawBatteryIcon(int right, int top)
{
    constexpr int kBodyW = 22;
    constexpr int kBodyH = 11;
    constexpr int kNubW  = 2;
    constexpr int kNubH  = 5;

    const int bodyRight = right - kNubW;
    const int bodyLeft  = bodyRight - kBodyW;

    const int level = M5Cardputer.Power.getBatteryLevel();
    const auto charging = M5Cardputer.Power.isCharging();

    // A negative level means the gauge is unreadable on this hardware; draw an
    // empty outline rather than a confident "0%".
    const bool known = level >= 0;
    const int clamped = known ? constrain(level, 0, 100) : 0;

    uint16_t color = kUnknown;
    if (charging == m5::Power_Class::is_charging) {
        color = kGood;
    } else if (known) {
        if (clamped <= 15) {
            color = kBad;
        } else if (clamped <= 30) {
            color = kWarn;
        } else {
            color = kHeader;
        }
    }

    canvas.drawRect(bodyLeft, top, kBodyW, kBodyH, color);
    canvas.fillRect(bodyRight, top + (kBodyH - kNubH) / 2, kNubW, kNubH, color);

    if (known) {
        const int innerW = kBodyW - 4;
        const int fill   = (innerW * clamped) / 100;
        if (fill > 0) {
            canvas.fillRect(bodyLeft + 2, top + 2, fill, kBodyH - 4, color);
        }
    } else {
        canvas.setTextDatum(middle_center);
        canvas.setTextColor(color, kBg);
        canvas.setTextSize(1);
        canvas.drawString("?", bodyLeft + kBodyW / 2, top + kBodyH / 2);
    }

    // A charging bolt would not fit legibly inside 22x11, so charging is shown
    // as a marker to the left of the icon instead.
    if (charging == m5::Power_Class::is_charging) {
        canvas.setTextDatum(middle_right);
        canvas.setTextColor(kGood, kBg);
        canvas.setTextSize(1);
        canvas.drawString("+", bodyLeft - 2, top + kBodyH / 2);
    }

    return bodyLeft;
}

void drawHeader(const char* title, Page page, bool showRotation = true)
{
    canvas.setTextDatum(top_left);
    canvas.setTextColor(kHeader, kBg);
    canvas.setTextSize(1);
    canvas.drawString(title, 6, 5);

    const int batteryLeft = drawBatteryIcon(kWidth - 4, 3);

    // Auxiliary screens are not part of the page rotation.
    if (showRotation) {
        const int radius = 3;
        const int spacing = 12;
        const int lastX = batteryLeft - 12;
        const int startX = lastX - static_cast<int>(kPageCount - 1) * spacing;
        for (size_t i = 0; i < kPageCount; ++i) {
            const int cx = startX + static_cast<int>(i) * spacing;
            if (i == static_cast<size_t>(page)) {
                canvas.fillCircle(cx, 9, radius, kFg);
            } else {
                canvas.drawCircle(cx, 9, radius, kHeader);
            }
        }
    }
    canvas.drawFastHLine(0, 18, kWidth, kHeader);
}

void drawNightly(const DashboardData& data, const RenderContext& ctx)
{
    drawHeader("NIGHTLY", Page::Nightly);

    const NightlySection& nightly = data.nightly;
    const char* status = "STATUS: ---";
    uint16_t statusColor = kUnknown;
    if (nightly.lastRunStatus == NightlyStatus::Completed) {
        status = "OSTATNI: OK";
        statusColor = kGood;
    } else if (nightly.lastRunStatus == NightlyStatus::Error) {
        status = "OSTATNI: BLAD";
        statusColor = kBad;
    }
    canvas.setTextDatum(top_center);
    canvas.setTextSize(2);
    canvas.setTextColor(statusColor, kBg);
    canvas.drawString(status, kWidth / 2, 24);

    canvas.setTextSize(1);
    canvas.setTextColor(kHeader, kBg);
    String runTime = "przebieg: ";
    if (nightly.lastRunEpochSeconds.has) {
        const time_t when = static_cast<time_t>(nightly.lastRunEpochSeconds.value);
        struct tm local {};
        localtime_r(&when, &local);
        char stamp[24];
        strftime(stamp, sizeof(stamp), "%H:%M, %d.%m", &local);
        runTime += stamp;
    } else {
        runTime += kNoData;
    }
    canvas.drawString(runTime, kWidth / 2, 45);

    // Keep success age separate: a newer failed attempt must not hide it.
    canvas.setTextDatum(top_left);
    String successAge = "udany: ";
    uint16_t ageColor = kUnknown;
    if (nightly.lastSuccessEpochSeconds.has && ctx.nowEpochSeconds > 0) {
        const int64_t age = ctx.nowEpochSeconds - nightly.lastSuccessEpochSeconds.value;
        successAge += formatAge(age);
        // The proxy owns the age alarm, including scheduled days off.
        ageColor = data.overallStatus == OverallStatus::Alarm ? kBad
            : (data.overallStatus == OverallStatus::Ok ? kGood : kUnknown);
    } else {
        successAge += kNoData;
    }
    canvas.setTextColor(ageColor, kBg);
    canvas.drawString(successAge, 6, 60);
    canvas.setTextDatum(top_right);
    canvas.setTextColor(nightly.durationSeconds.has ? kFg : kUnknown, kBg);
    canvas.drawString("czas: " + (nightly.durationSeconds.has
        ? formatDuration(nightly.durationSeconds.value) : String(kNoData)), 234, 60);

    canvas.setTextDatum(top_left);
    String steps = "kroki: ";
    if (nightly.stepsOk.has && nightly.stepsTotal.has) {
        steps += String(nightly.stepsOk.value) + "/" + String(nightly.stepsTotal.value);
        canvas.setTextColor(nightly.failedStepCount > 0 ? kWarn : kGood, kBg);
    } else {
        steps += kNoData;
        canvas.setTextColor(kUnknown, kBg);
    }
    canvas.drawString(steps, 6, 76);
    canvas.setTextDatum(top_right);
    canvas.setTextColor(nightly.espiReportsTotal.has ? kFg : kUnknown, kBg);
    canvas.drawString("ESPI: " + (nightly.espiReportsTotal.has
        ? String(nightly.espiReportsTotal.value) : String(kNoData)), 234, 76);

    canvas.setTextDatum(top_left);
    String credits = "Firecrawl: ";
    if (nightly.firecrawlCreditsRemaining.has) {
        credits += String(nightly.firecrawlCreditsRemaining.value);
        if (nightly.firecrawlCreditsPlan.has) {
            credits += "/" + String(nightly.firecrawlCreditsPlan.value);
        }
        canvas.setTextColor(nightly.firecrawlCreditsRemaining.value > 0 ? kFg : kWarn, kBg);
    } else {
        credits += kNoData;
        canvas.setTextColor(kUnknown, kBg);
    }
    canvas.drawString(credits, 6, 92);

    canvas.setTextColor(kHeader, kBg);
    String note = "ESPI/FC: ostatni pomiar";
    if (nightly.failedStepCount > 0) {
        note = "padly: ";
        for (size_t i = 0; i < nightly.failedStepCount; ++i) {
            if (i > 0) {
                note += ", ";
            }
            note += nightly.failedSteps[i];
        }
        canvas.setTextColor(kWarn, kBg);
    } else if (nightly.stepsSkipped.has && nightly.stepsSkipped.value > 0) {
        note = "pominiete kroki: " + String(nightly.stepsSkipped.value);
    }
    if (canvas.textWidth(note) > kWidth - 12) {
        while (note.length() > 0 && canvas.textWidth(note + "...") > kWidth - 12) {
            note.remove(note.length() - 1);
        }
        note += "...";
    }
    canvas.drawString(note, 6, 108);
}

void drawExecution(const DashboardData& data, const RenderContext& ctx)
{
    drawHeader("EGZEKUCJA", Page::Execution);

    // Session health, the execution latch and the explicit 2FA probe are
    // separate facts: a working session does not imply orders are permitted.
    const char* label = "SESJA: ---";
    uint16_t labelColor = kUnknown;
    if (data.execution.sessionUp.has) {
        label = data.execution.sessionUp.value ? "SESJA: DZIALA" : "SESJA: BRAK";
        labelColor = data.execution.sessionUp.value ? kGood : kBad;
    }
    canvas.setTextDatum(top_center);
    canvas.setTextColor(labelColor, kBg);
    canvas.setTextSize(2);
    canvas.drawString(label, kWidth / 2, 28);

    canvas.setTextDatum(top_left);
    canvas.setTextSize(1);
    const MaybeBool& twoFactor = data.execution.twoFactorRequired;
    canvas.setTextColor(twoFactor.has ? (twoFactor.value ? kWarn : kGood) : kUnknown, kBg);
    canvas.drawString(String("2FA: ") + (twoFactor.has
        ? (twoFactor.value ? "WYMAGANE" : "NIE") : kNoData), 6, 48);
    const MaybeBool& inhibitor = data.execution.inhibitorActive;
    canvas.setTextDatum(top_right);
    canvas.setTextColor(inhibitor.has ? (inhibitor.value ? kBad : kGood) : kUnknown, kBg);
    canvas.drawString(String("blokada: ") + (inhibitor.has
        ? (inhibitor.value ? "TAK" : "NIE") : kNoData), 234, 48);
    canvas.setTextDatum(top_left);

    // Filled and rejected, each with its 24 h increment.
    canvas.setTextColor(kFg, kBg);
    canvas.drawString("filled", 6, 66);
    canvas.setTextColor(data.execution.filled.total.has ? kFg : kUnknown, kBg);
    canvas.drawString(formatCount(data.execution.filled), 54, 66);

    canvas.setTextColor(kFg, kBg);
    canvas.drawString("rej", 140, 66);
    const bool freshRejects =
        data.execution.rejected.delta24h.has && data.execution.rejected.delta24h.value > 0;
    canvas.setTextColor(freshRejects ? kWarn : (data.execution.rejected.total.has ? kFg : kUnknown),
                        kBg);
    canvas.drawString(formatCount(data.execution.rejected), 170, 66);

    // Open positions and last reconcile.
    canvas.setTextColor(kFg, kBg);
    canvas.drawString("pozycje", 6, 84);
    if (data.execution.openPositions.has) {
        canvas.setTextColor(kFg, kBg);
        canvas.drawString(String(data.execution.openPositions.value) + "*", 60, 84);
    } else {
        canvas.setTextColor(kUnknown, kBg);
        canvas.drawString(kNoData, 60, 84);
    }

    canvas.setTextColor(kFg, kBg);
    canvas.drawString("rekon.", 120, 84);
    if (data.execution.lastReconcileEpochSeconds.has && ctx.nowEpochSeconds > 0) {
        const int64_t age = ctx.nowEpochSeconds - data.execution.lastReconcileEpochSeconds.value;
        canvas.setTextColor(kFg, kBg);
        canvas.drawString(formatAge(age), 168, 84);
    } else {
        canvas.setTextColor(kUnknown, kBg);
        canvas.drawString(kNoData, 168, 84);
    }

    // The asterisk earns its footnote: open positions come from nightly while the
    // order counts come from exec-reconcile, so these numbers have different ages.
    canvas.setTextColor(kHeader, kBg);
    canvas.drawString("* z nocnego przebiegu", 6, 106);
}

void drawDevice(const DashboardData& data, const RenderContext& ctx)
{
    (void)data;
    drawHeader("URZADZENIE", ctx.page, false);

    canvas.setTextDatum(top_left);
    canvas.setTextSize(1);
    canvas.setTextColor(kFg, kBg);

    const bool connected = WiFi.status() == WL_CONNECTED;
    canvas.drawString(String("WiFi: ") + (connected ? "OK" : "brak"), 6, 26);
    if (connected) {
        canvas.drawString(String("RSSI: ") + String(WiFi.RSSI()) + " dBm", 120, 26);
        canvas.drawString(String("IP: ") + WiFi.localIP().toString(), 6, 42);
    }
    const int brightnessPercent = (M5Cardputer.Display.getBrightness() * 100 + 127) / 255;
    canvas.drawString("jasn: " + String(brightnessPercent) + "%", 150, 42);

    const uint32_t uptimeSec = millis() / 1000UL;
    canvas.drawString("uptime: " + formatAge(uptimeSec), 6, 58);

    if (ctx.hasEverSucceeded) {
        const uint32_t ageSec = (millis() - ctx.lastSuccessMillis) / 1000UL;
        canvas.drawString("dane: " + formatAge(ageSec), 6, 74);
    } else {
        canvas.setTextColor(kUnknown, kBg);
        canvas.drawString("dane: brak pobrania", 6, 74);
        canvas.setTextColor(kFg, kBg);
    }

    canvas.drawString("bledy: " + String(ctx.totalFailures), 6, 90);

    // The header already carries the gauge; here it is spelled out, including the
    // charging state the icon can only hint at.
    const int battery = M5Cardputer.Power.getBatteryLevel();
    String batteryText = battery >= 0 ? String(battery) + "%" : String(kNoData);
    if (M5Cardputer.Power.isCharging() == m5::Power_Class::is_charging) {
        batteryText += " lad.";
    }
    canvas.setTextColor(battery >= 0 ? kFg : kUnknown, kBg);
    canvas.drawString("bat: " + batteryText, 120, 90);

    canvas.setTextColor(kHeader, kBg);
    canvas.drawString(String("fw ") + cfg::kFirmwareVersion, 6, 110);
    canvas.drawString("i/esc = wroc", 120, 110);
#if USE_MOCK_DATA
    canvas.setTextColor(kWarn, kBg);
    canvas.drawString("TEST", 200, 110);
#endif
}

void drawAlarm(const DashboardData& data, const RenderContext& ctx)
{
    canvas.fillScreen(kBad);
    canvas.setTextDatum(top_center);
    canvas.setTextColor(TFT_WHITE, kBad);

    canvas.setTextSize(3);
    canvas.drawString("BRAK NIGHTLY", kWidth / 2, 22);

    canvas.setTextSize(2);
    if (data.nightly.lastSuccessEpochSeconds.has && ctx.nowEpochSeconds > 0) {
        const int64_t age = ctx.nowEpochSeconds - data.nightly.lastSuccessEpochSeconds.value;
        canvas.drawString("ostatni udany", kWidth / 2, 60);
        canvas.setTextSize(3);
        canvas.drawString(formatAge(age), kWidth / 2, 82);
    } else {
        canvas.setTextSize(2);
        canvas.drawString("brak udanego przebiegu", kWidth / 2, 66);
        canvas.drawString("w oknie 48 h", kWidth / 2, 88);
    }
}

// Keybinding reference. Every binding the firmware actually implements is listed
// here; anything added to handleKeyboard() belongs on this screen too, or the
// help quietly starts lying.
void drawHelp()
{
    canvas.fillScreen(kBg);

    canvas.setTextDatum(top_left);
    canvas.setTextColor(kHeader, kBg);
    canvas.setTextSize(1);
    canvas.drawString("KLAWISZE", 6, 5);
    drawBatteryIcon(kWidth - 4, 3);
    canvas.drawFastHLine(0, 18, kWidth, kHeader);

    struct Binding {
        const char* key;
        const char* description;
    };
    // Labels name the key as printed on the case, not the character it emits:
    // the arrow keys report ',' and '.', and Esc reports '`'. Confirmed on
    // hardware — keep these in sync with handleKeyboard().
    static const Binding bindings[] = {
        {"< >", "poprzedni / nastepny ekran"},
        {"r", "odswiez dane teraz"},
        {"[ ]", "jasnosc - / +"},
        {"i", "urzadzenie (i zamyka)"},
        {"h", "ta pomoc (h zamyka)"},
        {"esc", "powrot do pulpitu"},
    };

    int y = 24;
    for (const Binding& binding : bindings) {
        canvas.setTextColor(kGood, kBg);
        canvas.drawString(binding.key, 8, y);
        canvas.setTextColor(kFg, kBg);
        canvas.drawString(binding.description, 52, y);
        y += 14;
    }

    canvas.setTextColor(kHeader, kBg);
    canvas.drawString("strzalki wstrzymuja rotacje na 60 s", 6, y + 2);
    canvas.drawString("alarm wraca po 30 s", 6, y + 14);
}

void drawDisconnected(const RenderContext& ctx)
{
    // Deliberately NOT red: "I cannot reach the proxy" is not the same claim as
    // "the monitored system is broken" (PRD FR-15).
    canvas.fillScreen(kDiscBg);
    canvas.setTextDatum(top_center);

    canvas.setTextColor(TFT_WHITE, kDiscBg);
    canvas.setTextSize(2);
    canvas.drawString("BRAK LACZNOSCI", kWidth / 2, 24);

    canvas.setTextSize(1);
    canvas.setTextColor(kWarn, kDiscBg);
    canvas.drawString(fetchErrorText(ctx.lastError), kWidth / 2, 54);

    canvas.setTextColor(TFT_WHITE, kDiscBg);
    if (ctx.hasEverSucceeded) {
        const uint32_t ageSec = (millis() - ctx.lastSuccessMillis) / 1000UL;
        canvas.drawString("ostatnie dane: " + formatAge(ageSec), kWidth / 2, 76);
    } else {
        canvas.drawString("nigdy nie pobrano danych", kWidth / 2, 76);
    }

    canvas.setTextColor(kHeader, kDiscBg);
    canvas.drawString("prob nieudanych: " + String(ctx.consecutiveFail), kWidth / 2, 96);
    canvas.drawString("r = ponow teraz   h = pomoc", kWidth / 2, 112);
    canvas.drawString("auto: co " + String(cfg::kPollIntervalRetryMs / 1000UL) + " s",
                      kWidth / 2, 124);
}

}  // namespace

void screensBegin()
{
    M5Cardputer.Display.setRotation(1);
    g_displayPreferencesReady = g_displayPreferences.begin("gpw-display", false);
    const int brightness = g_displayPreferencesReady
        ? g_displayPreferences.getUChar("brightness", cfg::kDisplayBrightness)
        : cfg::kDisplayBrightness;
    M5Cardputer.Display.setBrightness(constrain(
        brightness, cfg::kDisplayBrightnessMin, cfg::kDisplayBrightnessMax));

    // This is an always-on desk dashboard: the panel must never automatically
    // dim or sleep. M5Unified has no idle timeout, and
    // Power::timerSleep() is only ever called explicitly — so this is a guard
    // against a future change rather than a fix. Keep it that way:
    //   * never call M5.Power.timerSleep() / deepSleep() / lightSleep(),
    //   * never call Display.sleep() or setBrightness(0),
    //   * keep the delay() in loop() non-zero so the SoC stays out of automatic
    //     light sleep, and keep the one-second redraw tick alive.
    // A dashboard whose screen is dark is indistinguishable from a dead one.
    M5Cardputer.Display.wakeup();

    canvas.setColorDepth(16);
    canvas.createSprite(kWidth, kHeight);
    canvas.setTextWrap(false);
}

void screensAdjustBrightness(int direction)
{
    const int current = M5Cardputer.Display.getBrightness();
    const int next = constrain(current + direction * cfg::kDisplayBrightnessStep,
                               cfg::kDisplayBrightnessMin, cfg::kDisplayBrightnessMax);
    if (next == current) {
        return;
    }
    M5Cardputer.Display.setBrightness(static_cast<uint8_t>(next));
    if (g_displayPreferencesReady) {
        g_displayPreferences.putUChar("brightness", static_cast<uint8_t>(next));
    }
}

void screensRenderStarting(const char* message)
{
    canvas.fillScreen(kBg);
    canvas.setTextDatum(middle_center);
    canvas.setTextColor(kFg, kBg);
    canvas.setTextSize(2);
    canvas.drawString("GPW Radar", kWidth / 2, kHeight / 2 - 16);
    canvas.setTextSize(1);
    canvas.setTextColor(kHeader, kBg);
    canvas.drawString(message, kWidth / 2, kHeight / 2 + 12);
    canvas.pushSprite(0, 0);
}

void screensRender(const DashboardData& data, const RenderContext& ctx)
{
    switch (ctx.state) {
        case DeviceState::Alarm:
            drawAlarm(data, ctx);
            break;
        case DeviceState::Disconnected:
            drawDisconnected(ctx);
            break;
        case DeviceState::Help:
            drawHelp();
            break;
        case DeviceState::DeviceInfo:
            canvas.fillScreen(kBg);
            drawDevice(data, ctx);
            break;
        case DeviceState::Starting:
            screensRenderStarting("pobieranie danych...");
            return;
        case DeviceState::Normal:
            canvas.fillScreen(kBg);
            switch (ctx.page) {
                case Page::Nightly:
                    drawNightly(data, ctx);
                    break;
                case Page::Execution:
                    drawExecution(data, ctx);
                    break;
            }
            // A paused rotation is shown, so a frozen screen is never a mystery.
            if (ctx.rotationPaused) {
                canvas.setTextDatum(top_left);
                canvas.setTextSize(1);
                canvas.setTextColor(kHeader, kBg);
                canvas.drawString("||", 6, 122);
            }
            break;
    }
    canvas.pushSprite(0, 0);
}
