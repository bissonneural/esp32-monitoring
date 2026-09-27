// GPW Radar monitor for M5Stack Cardputer ADV.
//
// Read-only by construction: the only outbound request this firmware can make is
// GET /v1/dashboard (see dashboard_client.cpp). There is no code path that
// changes anything in gpw_radar, GCP or the broker (PRD FR-20, C-5).
//
// PRD: tasks/prd-cardputer-gpw-monitor.md

#include <M5Cardputer.h>
#include <WiFi.h>
#include <time.h>

#include "config.h"
#include "dashboard_client.h"
#include "dashboard_data.h"
#include "screens.h"
#include "secrets.h"

namespace {

DashboardData g_data;
RenderContext g_ctx;

uint32_t g_lastFetchMillis    = 0;
uint32_t g_lastRotationMillis = 0;
uint32_t g_lastKeyMillis      = 0;
uint32_t g_alarmLeftMillis    = 0;
bool g_alarmPagedAway         = false;
bool g_needsRedraw            = true;

// WiFi reconnect backoff state.
uint32_t g_wifiRetryDelayMs = cfg::kWifiRetryBaseMs;
uint32_t g_wifiRetryAtMs    = 0;
bool g_ntpSynced            = false;

bool isAuxiliaryScreen()
{
    return g_ctx.state == DeviceState::Help || g_ctx.state == DeviceState::DeviceInfo;
}

void startWifi()
{
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    // WIFI_PS_MIN_MODEM is the Arduino default and lets the SoC drop into
    // automatic light sleep between beacons. On a mains-powered desk dashboard
    // that buys nothing and costs responsiveness, so it is turned off.
    WiFi.setSleep(false);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

// Non-blocking WiFi supervision with exponential backoff, so a router reboot
// never turns into a busy loop and never blocks the display (PRD US-201).
void serviceWifi()
{
    if (WiFi.status() == WL_CONNECTED) {
        g_wifiRetryDelayMs = cfg::kWifiRetryBaseMs;
        return;
    }
    const uint32_t now = millis();
    if (g_wifiRetryAtMs != 0 && now < g_wifiRetryAtMs) {
        return;
    }
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    g_wifiRetryAtMs = now + g_wifiRetryDelayMs;
    g_wifiRetryDelayMs = min(g_wifiRetryDelayMs * 2, cfg::kWifiRetryMaxMs);
}

void syncTime()
{
    configTzTime(cfg::kTimezone, cfg::kNtpServer);
}

// True while the local clock says we are inside a trading session. When the
// clock has not synced we deliberately return true: polling too often is the
// safer direction to be wrong in (PRD US-209).
bool inSessionHours()
{
    if (!g_ntpSynced) {
        return true;
    }
    time_t now = time(nullptr);
    struct tm local {};
    localtime_r(&now, &local);
    if (local.tm_wday == 0 || local.tm_wday == 6) {
        return false;
    }
    const int minutes = local.tm_hour * 60 + local.tm_min;
    return minutes >= cfg::kSessionStartMinutes && minutes < cfg::kSessionEndMinutes;
}

uint32_t pollIntervalMs()
{
    if (g_ctx.consecutiveFail > 0) {
        return cfg::kPollIntervalRetryMs;
    }
#if USE_MOCK_DATA
    // The mock cycles through its scenarios on each fetch; poll fast so all of
    // them can be seen on hardware in one sitting.
    return 30UL * 1000UL;
#else
    return inSessionHours() ? cfg::kPollIntervalSessionMs : cfg::kPollIntervalIdleMs;
#endif
}

void refreshClock()
{
    time_t now = time(nullptr);
    // Anything before 2021 means NTP has not landed yet; leave the age
    // calculations disabled rather than compute nonsense from a 1970 clock.
    if (now > 1600000000) {
        g_ntpSynced           = true;
        g_ctx.nowEpochSeconds = static_cast<int64_t>(now);
    } else {
        g_ntpSynced           = false;
        g_ctx.nowEpochSeconds = 0;
    }
}

// Applies a fetch outcome to the device state (PRD US-203, FR-16).
void applyFetchResult(const FetchResult& result)
{
    if (result.ok()) {
        g_data                 = result.data;
        g_ctx.lastSuccessMillis = millis();
        g_ctx.hasEverSucceeded  = true;
        g_ctx.consecutiveFail   = 0;
        g_ctx.lastError         = FetchError::None;

        // Explicitly opened screens must survive background refreshes.
        const bool readingInfo = isAuxiliaryScreen();

        // The proxy owns the alarm verdict so the threshold can change without
        // reflashing (PRD FR-14).
        if (g_data.overallStatus == OverallStatus::Alarm) {
            // Only a fresh alarm re-arms the screen. A user who has paged away
            // from a still-standing alarm keeps their reprieve until it expires.
            if (!g_alarmPagedAway && !readingInfo) {
                g_ctx.state = DeviceState::Alarm;
            }
        } else {
            // The alarm cleared: forget that it was ever paged away.
            g_alarmPagedAway = false;
            if (!readingInfo) {
                g_ctx.state = DeviceState::Normal;
            }
        }
    } else {
        g_ctx.lastError = result.error;
        g_ctx.totalFailures++;
        if (g_ctx.consecutiveFail < 255) {
            g_ctx.consecutiveFail++;
        }
        // A first-load failure has no cached dashboard to keep on screen.
        if ((!g_ctx.hasEverSucceeded ||
             g_ctx.consecutiveFail >= cfg::kMaxConsecutiveFailures) &&
            !isAuxiliaryScreen()) {
            g_ctx.state = DeviceState::Disconnected;
        }
    }
    g_needsRedraw = true;
}

void doFetch()
{
    const FetchResult result = fetchDashboard();
    applyFetchResult(result);
    // Schedule the next attempt from completion so slow requests cannot cause
    // immediate retries or starve keyboard handling between attempts.
    g_lastFetchMillis = millis();
    Serial.printf("fetch http=%d error=%d\n", result.httpStatus,
                  static_cast<int>(result.error));
    if (result.error == FetchError::None) {
        const NightlySection& nightly = g_data.nightly;
        const ExecutionSection& execution = g_data.execution;
        const char* status = nightly.lastRunStatus == NightlyStatus::Completed ? "completed"
            : (nightly.lastRunStatus == NightlyStatus::Error ? "error" : "unknown");
        Serial.printf("dashboard nightly=%s session=%d 2fa=%d espi=%ld firecrawl=%ld/%ld\n",
                      status,
                      execution.sessionUp.has ? execution.sessionUp.value : -1,
                      execution.twoFactorRequired.has ? execution.twoFactorRequired.value : -1,
                      static_cast<long>(nightly.espiReportsTotal.has ? nightly.espiReportsTotal.value : -1),
                      static_cast<long>(nightly.firecrawlCreditsRemaining.has ? nightly.firecrawlCreditsRemaining.value : -1),
                      static_cast<long>(nightly.firecrawlCreditsPlan.has ? nightly.firecrawlCreditsPlan.value : -1));
    }
}

void nextPage(int direction)
{
    int page = static_cast<int>(g_ctx.page) + direction;
    if (page < 0) {
        page = static_cast<int>(kPageCount) - 1;
    }
    if (page >= static_cast<int>(kPageCount)) {
        page = 0;
    }
    g_ctx.page          = static_cast<Page>(page);
    g_needsRedraw       = true;
    g_lastRotationMillis = millis();
}

// Arrow keys on the Cardputer are the ',' and '.' keys; 'r' forces a refresh.
// Leaves the alarm screen for a normal page. The alarm is not dismissed — it is
// only paged away from, and comes back on its own (PRD US-207).
void leaveAlarmTemporarily(uint32_t now)
{
    if (g_ctx.state != DeviceState::Alarm) {
        return;
    }
    g_alarmPagedAway  = true;
    g_alarmLeftMillis = now;
    g_ctx.state       = DeviceState::Normal;
}

// Return from an auxiliary screen and immediately restore any active alarm.
void returnToDashboard(uint32_t now)
{
    g_lastRotationMillis = now;
    if (g_data.overallStatus == OverallStatus::Alarm) {
        g_alarmPagedAway = false;
        g_ctx.state      = DeviceState::Alarm;
    } else if (!g_ctx.hasEverSucceeded ||
               g_ctx.consecutiveFail >= cfg::kMaxConsecutiveFailures) {
        g_ctx.state = DeviceState::Disconnected;
    } else {
        g_ctx.state = DeviceState::Normal;
    }
    g_needsRedraw = true;
}

void handleKeyboard()
{
    if (!M5Cardputer.Keyboard.isChange() || !M5Cardputer.Keyboard.isPressed()) {
        return;
    }
    const auto& state = M5Cardputer.Keyboard.keysState();
    const uint32_t now = millis();

    for (const char c : state.word) {
        if (c == '[' || c == ']' || c == '{' || c == '}') {
            const int direction = (c == '[' || c == '{') ? -1 : 1;
            screensAdjustBrightness(direction);
            g_lastKeyMillis = now;
            g_needsRedraw = true;
            continue;
        }

        if (c == 'i' || c == 'I') {
            g_lastKeyMillis = now;
            if (g_ctx.state == DeviceState::DeviceInfo) {
                returnToDashboard(now);
            } else {
                leaveAlarmTemporarily(now);
                g_ctx.state          = DeviceState::DeviceInfo;
                g_ctx.rotationPaused = true;
            }
            g_needsRedraw = true;
            continue;
        }

        if (c == 'h' || c == 'H') {
            g_lastKeyMillis = now;
            // Toggle, so the same key that opened help also closes it.
            if (g_ctx.state == DeviceState::Help) {
                returnToDashboard(now);
            } else {
                leaveAlarmTemporarily(now);
                g_ctx.state          = DeviceState::Help;
                g_ctx.rotationPaused = true;
                g_needsRedraw        = true;
            }
            continue;
        }

        // The Esc key reports '`': the library has no KEY_ESC constant and maps
        // that position through the ordinary character table. Verified on the
        // device — this is the real Esc, not a substitute.
        if (c == '`') {
            g_lastKeyMillis = now;
            if (isAuxiliaryScreen()) {
                returnToDashboard(now);
            }
            continue;
        }

        // Arrows leave either auxiliary screen. Refresh keeps device diagnostics
        // open so connectivity errors can be inspected in place.
        const bool navigating = c == ',' || c == ';' || c == '.' || c == '/';
        if ((isAuxiliaryScreen() && navigating) ||
            (g_ctx.state == DeviceState::Help && (c == 'r' || c == 'R'))) {
            returnToDashboard(now);
        }

        if (c == ',' || c == ';') {
            g_lastKeyMillis      = now;
            g_ctx.rotationPaused = true;
            leaveAlarmTemporarily(now);
            nextPage(-1);
        } else if (c == '.' || c == '/') {
            g_lastKeyMillis      = now;
            g_ctx.rotationPaused = true;
            leaveAlarmTemporarily(now);
            nextPage(1);
        } else if (c == 'r' || c == 'R') {
            g_lastKeyMillis = now;
            doFetch();
        }
    }
}

// Diagnostics that deliberately never print the SSID, the password, the proxy
// URL or the bearer token (PRD FR-19) — only states and counts.
void logStatus(const char* event)
{
    long nightlyAge = -1;
    if (g_data.nightly.lastSuccessEpochSeconds.has && g_ctx.nowEpochSeconds > 0) {
        nightlyAge = static_cast<long>(g_ctx.nowEpochSeconds -
                                       g_data.nightly.lastSuccessEpochSeconds.value);
    }
    Serial.printf(
        "[%lu] %s wifi=%d state=%d page=%d fails=%lu clock=%ld nightlyAge=%lds bat=%d chg=%d bright=%u\n",
        static_cast<unsigned long>(millis()), event, static_cast<int>(WiFi.status()),
        static_cast<int>(g_ctx.state), static_cast<int>(g_ctx.page),
        static_cast<unsigned long>(g_ctx.totalFailures),
        static_cast<long>(g_ctx.nowEpochSeconds), nightlyAge,
        static_cast<int>(M5Cardputer.Power.getBatteryLevel()),
        static_cast<int>(M5Cardputer.Power.isCharging()),
        static_cast<unsigned int>(M5Cardputer.Display.getBrightness()));
}

}  // namespace

void setup()
{
    auto cfgM5 = M5.config();
    M5Cardputer.begin(cfgM5, true);

    Serial.begin(115200);
    delay(200);
    Serial.println("gpw-cardputer boot");

    screensBegin();
    screensRenderStarting("laczenie z WiFi...");

    startWifi();

    // Give WiFi a short head start so the first screen is usually real data,
    // but never block the UI on it.
    const uint32_t deadline = millis() + 8000UL;
    while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
        delay(100);
    }

    if (WiFi.status() == WL_CONNECTED) {
        screensRenderStarting("synchronizacja czasu...");
        syncTime();
        const uint32_t ntpDeadline = millis() + 5000UL;
        while (time(nullptr) < 1600000000 && millis() < ntpDeadline) {
            delay(100);
        }
    }

    refreshClock();
    logStatus("wifi-phase-done");

    screensRenderStarting("pobieranie danych...");
    doFetch();
    logStatus("first-fetch");
    g_lastRotationMillis = millis();
}

void loop()
{
    M5Cardputer.update();
    const uint32_t now = millis();

    serviceWifi();
    handleKeyboard();
    refreshClock();

    // Scheduled poll.
    if (now - g_lastFetchMillis >= pollIntervalMs()) {
        doFetch();
    }

    // Resume automatic rotation once the user has stopped pressing keys.
    if (g_ctx.rotationPaused && now - g_lastKeyMillis >= cfg::kRotationResumeMs) {
        g_ctx.rotationPaused = false;
        g_needsRedraw        = true;
    }

    // The alarm always comes back — it must not be dismissible by walking away
    // from it (PRD US-207). Auxiliary screens may stay open: the timer keeps
    // running, so the alarm reasserts itself when the auxiliary screen is closed.
    if (g_alarmPagedAway && g_data.overallStatus == OverallStatus::Alarm &&
        !isAuxiliaryScreen() &&
        now - g_alarmLeftMillis >= cfg::kAlarmReturnMs) {
        g_alarmPagedAway    = false;
        g_ctx.state         = DeviceState::Alarm;
        g_ctx.rotationPaused = false;
        g_needsRedraw       = true;
    }

    // Automatic rotation, only in the normal state: the alarm and disconnected
    // screens are full-screen and do not rotate.
    if (g_ctx.state == DeviceState::Normal && !g_ctx.rotationPaused &&
        now - g_lastRotationMillis >= cfg::kScreenRotationMs) {
        nextPage(1);
    }

    // Redraw once per second even without an event, so relative ages
    // ("8 h temu", uptime) do not sit frozen on screen.
    static uint32_t lastTick = 0;
    if (now - lastTick >= 1000UL) {
        lastTick      = now;
        g_needsRedraw = true;
    }

    // Heartbeat every 10 s, so a hung device is distinguishable from a quiet one.
    static uint32_t lastBeat = 0;
    if (now - lastBeat >= 10000UL) {
        lastBeat = now;
        logStatus("tick");
    }

    if (g_needsRedraw) {
        g_needsRedraw = false;
        screensRender(g_data, g_ctx);
    }

    delay(20);
}
