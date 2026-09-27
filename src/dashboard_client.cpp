#include "dashboard_client.h"

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "config.h"
#include "secrets.h"

namespace {

// Google Trust Services roots for the proxy's HTTPS endpoint. Both RSA (R1)
// and ECDSA (R4) chains are supported; Google currently serves run.app via R4.
// Official certificates, valid until 2036:
// https://pki.goog/repo/certs/gtsr1.pem
// https://pki.goog/repo/certs/gtsr4.pem
const char kGoogleRootCAs[] PROGMEM = R"(-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlk28xsBNJiGuiFzANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjEwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjEwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQC2EQKLHuOhd5s73L+UPreVp0A8of2C+X0yBoJx9vaMf/vo
27xqLpeXo4xL+Sv2sfnOhB2x+cWX3u+58qPpvBKJXqeqUqv4IyfLpLGcY9vXmX7w
Cl7raKb0xlpHDU0QM+NOsROjyBhsS+z8CZDfnWQpJSMHobTSPS5g4M/SCYe7zUjw
TcLCeoiKu7rPWRnWr4+wB7CeMfGCwcDfLqZtbBkOtdh+JhpFAz2weaSUKK0Pfybl
qAj+lug8aJRT7oM6iCsVlgmy4HqMLnXWnOunVmSPlk9orj2XwoSPwLxAwAtcvfaH
szVsrBhQf4TgTM2S0yDpM7xSma8ytSmzJSq0SPly4cpk9+aCEI3oncKKiPo4Zor8
Y/kB+Xj9e1x3+naH+uzfsQ55lVe0vSbv1gHR6xYKu44LtcXFilWr06zqkUspzBmk
MiVOKvFlRNACzqrOSbTqn3yDsEB750Orp2yjj32JgfpMpf/VjsPOS+C12LOORc92
wO1AK/1TD7Cn1TsNsYqiA94xrcx36m97PtbfkSIS5r762DL8EGMUUXLeXdYWk70p
aDPvOmbsB4om3xPXV2V4J95eSRQAogB/mqghtqmxlbCluQ0WEdrHbEg8QOB+DVrN
VjzRlwW5y0vtOUucxD/SVRNuJLDWcfr0wbrM7Rv1/oFB2ACYPTrIrnqYNxgFlQID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQU5K8rJnEaK0gnhS9SZizv8IkTcT4wDQYJKoZIhvcNAQEMBQADggIBAJ+qQibb
C5u+/x6Wki4+omVKapi6Ist9wTrYggoGxval3sBOh2Z5ofmmWJyq+bXmYOfg6LEe
QkEzCzc9zolwFcq1JKjPa7XSQCGYzyI0zzvFIoTgxQ6KfF2I5DUkzps+GlQebtuy
h6f88/qBVRRiClmpIgUxPoLW7ttXNLwzldMXG+gnoot7TiYaelpkttGsN/H9oPM4
7HLwEXWdyzRSjeZ2axfG34arJ45JK3VmgRAhpuo+9K4l/3wV3s6MJT/KYnAK9y8J
ZgfIPxz88NtFMN9iiMG1D53Dn0reWVlHxYciNuaCp+0KueIHoI17eko8cdLiA6Ef
MgfdG+RCzgwARWGAtQsgWSl4vflVy2PFPEz0tv/bal8xa5meLMFrUKTX5hgUvYU/
Z6tGn6D/Qqc6f1zLXbBwHSs09dR2CQzreExZBfMzQsNhFRAbd03OIozUhfJFfbdT
6u9AWpQKXCBfTkBdYiJ23//OYb2MI3jSNwLgjt7RETeJ9r/tSQdirpLsQBqvFAnZ
0E6yove+7u7Y/9waLd64NnHi/Hm3lCXRSHNboTXns5lndcEZOitHTtNCjv0xyBZm
2tIMPNuzjsmhDYAPexZ3FL//2wmUspO8IFgV6dtxQ/PeEMMA3KgqlbbC1j+Qa3bb
bP6MvPJwNQzcmRk13NfIRmPVNnGuV/u3gm3c
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvVYRg1rkDdc/eJkTBa6zzuhXyi
QHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHci8nHc8iMai/lxKvR
HYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D
9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8
p/SgguMh1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD
-----END CERTIFICATE-----
)";

void parseOrderCount(JsonVariantConst node, OrderCount& out)
{
    if (node.isNull()) {
        return;
    }
    // A JSON null must stay absent: "unknown" and "zero" are different answers.
    JsonVariantConst total = node["total"];
    if (!total.isNull() && total.is<int32_t>()) {
        out.total.set(total.as<int32_t>());
    }
    JsonVariantConst delta = node["delta24h"];
    if (!delta.isNull() && delta.is<int32_t>()) {
        out.delta24h.set(delta.as<int32_t>());
    }
}

OverallStatus parseOverallStatus(JsonVariantConst node)
{
    if (node.isNull() || !node.is<const char*>()) {
        return OverallStatus::Unknown;
    }
    const char* value = node.as<const char*>();
    if (strcmp(value, "ok") == 0) {
        return OverallStatus::Ok;
    }
    if (strcmp(value, "alarm") == 0) {
        return OverallStatus::Alarm;
    }
    return OverallStatus::Unknown;
}

// Days since the Unix epoch for a civil date, by Howard Hinnant's days_from_civil.
// Written out rather than using timegm(), which newlib on the ESP32 does not
// provide, and mktime(), which would silently apply the device's local timezone
// to a timestamp the contract defines as UTC.
int64_t daysFromCivil(int year, unsigned month, unsigned day)
{
    year -= month <= 2;
    const int era        = (year >= 0 ? year : year - 399) / 400;
    const unsigned yoe   = static_cast<unsigned>(year - era * 400);
    const unsigned doy   = (153u * (month + (month > 2 ? -3 : 9)) + 2u) / 5u + day - 1u;
    const unsigned doe   = yoe * 365u + yoe / 4u - yoe / 100u + doy;
    return static_cast<int64_t>(era) * 146097 + static_cast<int64_t>(doe) - 719468;
}

// Parses an ISO 8601 UTC timestamp ("2026-08-28T19:42:11Z") into epoch seconds.
// Returns false on anything that does not match that exact shape.
bool parseIso8601Utc(const char* text, int64_t& outEpochSeconds)
{
    if (text == nullptr) {
        return false;
    }
    int year = 0, mon = 0, day = 0, hour = 0, min = 0, sec = 0;
    if (sscanf(text, "%4d-%2d-%2dT%2d:%2d:%2dZ", &year, &mon, &day, &hour, &min, &sec) != 6) {
        return false;
    }
    if (mon < 1 || mon > 12 || day < 1 || day > 31 || hour > 23 || min > 59 || sec > 60) {
        return false;
    }
    const int64_t days = daysFromCivil(year, static_cast<unsigned>(mon), static_cast<unsigned>(day));
    outEpochSeconds = days * 86400LL + hour * 3600LL + min * 60LL + sec;
    return true;
}

bool parsePayload(const String& body, DashboardData& out)
{
    JsonDocument doc;
    const DeserializationError error = deserializeJson(doc, body);
    if (error) {
        return false;
    }

    out.overallStatus = parseOverallStatus(doc["overallStatus"]);

    JsonVariantConst generatedAt = doc["generatedAt"];
    if (generatedAt.is<const char*>()) {
        int64_t epoch = 0;
        if (parseIso8601Utc(generatedAt.as<const char*>(), epoch)) {
            out.generatedAtEpochSeconds.set(epoch);
        }
    }

    JsonVariantConst nightly = doc["nightly"];
    if (!nightly.isNull()) {
        const char* runStatus = nightly["lastRunStatus"] | "";
        if (strcmp(runStatus, "completed") == 0) {
            out.nightly.lastRunStatus = NightlyStatus::Completed;
        } else if (strcmp(runStatus, "error") == 0) {
            out.nightly.lastRunStatus = NightlyStatus::Error;
        }
        JsonVariantConst runTime = nightly["lastRunEpochSeconds"];
        if (!runTime.isNull() && runTime.is<int64_t>()) {
            out.nightly.lastRunEpochSeconds.set(runTime.as<int64_t>());
        }
        JsonVariantConst lastSuccess = nightly["lastSuccessEpochSeconds"];
        if (!lastSuccess.isNull() && lastSuccess.is<int64_t>()) {
            out.nightly.lastSuccessEpochSeconds.set(lastSuccess.as<int64_t>());
        }
        JsonVariantConst duration = nightly["durationSeconds"];
        if (!duration.isNull() && duration.is<float>()) {
            out.nightly.durationSeconds.set(duration.as<float>());
        }
        JsonVariantConst stepsOk = nightly["stepsOk"];
        if (!stepsOk.isNull() && stepsOk.is<int32_t>()) {
            out.nightly.stepsOk.set(stepsOk.as<int32_t>());
        }
        JsonVariantConst stepsTotal = nightly["stepsTotal"];
        if (!stepsTotal.isNull() && stepsTotal.is<int32_t>()) {
            out.nightly.stepsTotal.set(stepsTotal.as<int32_t>());
        }
        JsonVariantConst skipped = nightly["stepsSkipped"];
        if (!skipped.isNull() && skipped.is<int32_t>()) {
            out.nightly.stepsSkipped.set(skipped.as<int32_t>());
        }
        JsonVariantConst espi = nightly["espiReportsTotal"];
        if (!espi.isNull() && espi.is<int32_t>()) {
            out.nightly.espiReportsTotal.set(espi.as<int32_t>());
        }
        JsonVariantConst credits = nightly["firecrawlCreditsRemaining"];
        if (!credits.isNull() && credits.is<int32_t>()) {
            out.nightly.firecrawlCreditsRemaining.set(credits.as<int32_t>());
        }
        JsonVariantConst plan = nightly["firecrawlCreditsPlan"];
        if (!plan.isNull() && plan.is<int32_t>()) {
            out.nightly.firecrawlCreditsPlan.set(plan.as<int32_t>());
        }
        JsonArrayConst failed = nightly["failedSteps"];
        out.nightly.failedStepCount = 0;
        for (JsonVariantConst step : failed) {
            if (out.nightly.failedStepCount >= NightlySection::kMaxFailedSteps) {
                break;
            }
            if (step.is<const char*>()) {
                out.nightly.failedSteps[out.nightly.failedStepCount++] = step.as<const char*>();
            }
        }
    }

    JsonVariantConst execution = doc["execution"];
    if (!execution.isNull()) {
        JsonVariantConst twoFactor = execution["twoFactorRequired"];
        if (!twoFactor.isNull() && twoFactor.is<bool>()) {
            out.execution.twoFactorRequired.set(twoFactor.as<bool>());
        }
        JsonVariantConst sessionUp = execution["sessionUp"];
        if (!sessionUp.isNull() && sessionUp.is<bool>()) {
            out.execution.sessionUp.set(sessionUp.as<bool>());
        }
        JsonVariantConst inhibitor = execution["inhibitorActive"];
        if (!inhibitor.isNull() && inhibitor.is<bool>()) {
            out.execution.inhibitorActive.set(inhibitor.as<bool>());
        }
        JsonVariantConst reconcile = execution["lastReconcileEpochSeconds"];
        if (!reconcile.isNull() && reconcile.is<int64_t>()) {
            out.execution.lastReconcileEpochSeconds.set(reconcile.as<int64_t>());
        }
        JsonVariantConst orders = execution["orders"];
        if (!orders.isNull()) {
            parseOrderCount(orders["filled"], out.execution.filled);
            parseOrderCount(orders["unfilled"], out.execution.unfilled);
            parseOrderCount(orders["rejected"], out.execution.rejected);
        }
        JsonVariantConst positions = execution["openPositions"];
        if (!positions.isNull() && positions.is<int32_t>()) {
            out.execution.openPositions.set(positions.as<int32_t>());
        }
    }

    return true;
}

#if USE_MOCK_DATA
// Sample payloads matching the §7.2 contract, used to verify every screen on real
// hardware before the proxy exists. Each mock cycles in on successive fetches so
// the alarm and the awkward "unknown" cases can be seen without a proxy.
//
// Timestamps are filled in at fetch time relative to the current clock, because a
// hard-coded epoch would drift into a false alarm as soon as it aged past 26 h —
// exactly the bug this mock is meant to help catch, not manufacture.

// 1. Healthy run, one failed step, an absent delta (null, not 0).
const char kMockOk[] PROGMEM = R"({
"generatedAt":"%s","overallStatus":"ok",
"nightly":{"lastRunStatus":"completed","lastRunEpochSeconds":%lld,
"lastSuccessEpochSeconds":%lld,"durationSeconds":743.2,
"stepsOk":6,"stepsTotal":7,"stepsSkipped":0,"failedSteps":["sentiment"],
"espiReportsTotal":2747,"firecrawlCreditsRemaining":4827,"firecrawlCreditsPlan":5000},
"execution":{"sessionUp":true,"inhibitorActive":false,"twoFactorRequired":false,
"lastReconcileEpochSeconds":%lld,
"orders":{"filled":{"total":143,"delta24h":3},
"unfilled":{"total":12,"delta24h":0},
"rejected":{"total":2,"delta24h":null}},
"openPositions":7}})";

// 2. Alarm: nightly is 30 h stale, execution latched shut.
const char kMockAlarm[] PROGMEM = R"({
"generatedAt":"%s","overallStatus":"alarm",
"nightly":{"lastRunStatus":"error","lastRunEpochSeconds":%lld,
"lastSuccessEpochSeconds":%lld,"durationSeconds":812.0,
"stepsOk":4,"stepsTotal":7,"stepsSkipped":0,"failedSteps":["sentiment","analyze","resolve"],
"espiReportsTotal":2747,"firecrawlCreditsRemaining":0,"firecrawlCreditsPlan":5000},
"execution":{"sessionUp":false,"inhibitorActive":true,"twoFactorRequired":true,
"lastReconcileEpochSeconds":%lld,
"orders":{"filled":{"total":143,"delta24h":0},
"unfilled":{"total":19,"delta24h":7},
"rejected":{"total":5,"delta24h":3}},
"openPositions":7}})";

// 3. Everything unknown: no probe, no points. Verifies that grey dashes appear
// where a naive parser would have printed a confident zero.
const char kMockUnknown[] PROGMEM = R"({
"generatedAt":"%s","overallStatus":"ok",
"nightly":{"lastRunStatus":null,"lastRunEpochSeconds":null,
"lastSuccessEpochSeconds":null,"durationSeconds":null,
"stepsOk":null,"stepsTotal":null,"stepsSkipped":null,"failedSteps":[],
"espiReportsTotal":null,"firecrawlCreditsRemaining":null,"firecrawlCreditsPlan":null},
"execution":{"sessionUp":null,"inhibitorActive":null,"twoFactorRequired":null,
"lastReconcileEpochSeconds":null,
"orders":{"filled":{"total":null,"delta24h":null},
"unfilled":{"total":null,"delta24h":null},
"rejected":{"total":null,"delta24h":null}},
"openPositions":null}})";

uint8_t g_mockIndex = 0;

// Builds one mock payload with timestamps anchored to the current clock.
String buildMockPayload()
{
    const time_t now = time(nullptr);
    const int64_t nowEpoch = (now > 1600000000) ? static_cast<int64_t>(now) : 1787944000LL;

    char stamp[32];
    const time_t stampTime = static_cast<time_t>(nowEpoch);
    struct tm utc {};
    gmtime_r(&stampTime, &utc);
    strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%SZ", &utc);

    const uint8_t which = g_mockIndex % 3;
    g_mockIndex++;

    char buffer[1024];
    switch (which) {
        case 1:
            // 30 h ago: past the 26 h alarm threshold on purpose.
            snprintf(buffer, sizeof(buffer), kMockAlarm, stamp,
                     static_cast<long long>(nowEpoch - 1LL * 3600LL),
                     static_cast<long long>(nowEpoch - 30LL * 3600LL),
                     static_cast<long long>(nowEpoch - 26LL * 3600LL));
            break;
        case 2:
            snprintf(buffer, sizeof(buffer), kMockUnknown, stamp);
            break;
        default:
            // 8 h ago: a normal overnight run.
            snprintf(buffer, sizeof(buffer), kMockOk, stamp,
                     static_cast<long long>(nowEpoch - 8LL * 3600LL),
                     static_cast<long long>(nowEpoch - 8LL * 3600LL),
                     static_cast<long long>(nowEpoch - 2LL * 3600LL));
            break;
    }
    return String(buffer);
}
#endif

}  // namespace

const char* fetchErrorText(FetchError error)
{
    switch (error) {
        case FetchError::None:
            return "OK";
        case FetchError::NoWifi:
            return "Brak WiFi";
        case FetchError::ConnectionFailed:
            return "Proxy nie odpowiada";
        case FetchError::Unauthorized:
            return "Proxy odrzucilo token";
        case FetchError::ServerError:
            return "Blad po stronie proxy";
        case FetchError::BadResponse:
            return "Bledna odpowiedz";
    }
    return "Nieznany blad";
}

FetchResult fetchDashboard()
{
    FetchResult result;

#if USE_MOCK_DATA
    const String body = buildMockPayload();
    if (!parsePayload(body, result.data)) {
        result.error = FetchError::BadResponse;
        return result;
    }
    result.httpStatus = 200;
    return result;
#else
    if (WiFi.status() != WL_CONNECTED) {
        result.error = FetchError::NoWifi;
        return result;
    }

    WiFiClientSecure client;
    client.setCACert(kGoogleRootCAs);
    client.setTimeout(cfg::kHttpTimeoutMs / 1000);

    HTTPClient http;
    http.setTimeout(cfg::kHttpTimeoutMs);
    http.setConnectTimeout(cfg::kHttpTimeoutMs);
    if (!http.begin(client, PROXY_URL)) {
        result.error = FetchError::ConnectionFailed;
        return result;
    }
    // The token is written straight into the header and never logged.
    http.addHeader("Authorization", String("Bearer ") + PROXY_TOKEN);

    const int status = http.GET();
    result.httpStatus = status;

    if (status <= 0) {
        http.end();
        result.error = FetchError::ConnectionFailed;
        return result;
    }
    if (status == 401 || status == 403) {
        http.end();
        result.error = FetchError::Unauthorized;
        return result;
    }
    if (status >= 500) {
        http.end();
        result.error = FetchError::ServerError;
        return result;
    }
    if (status != 200) {
        http.end();
        result.error = FetchError::BadResponse;
        return result;
    }

    const String body = http.getString();
    http.end();

    if (!parsePayload(body, result.data)) {
        result.error = FetchError::BadResponse;
        return result;
    }
    return result;
#endif
}
