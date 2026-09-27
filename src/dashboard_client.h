#pragma once

#include <Arduino.h>

#include "dashboard_data.h"

// Why a fetch failed. These are distinct because the disconnected screen tells
// the user which one happened — "proxy rejected the token" and "no WiFi" need
// very different fixes (PRD US-206).
enum class FetchError {
    None,
    NoWifi,
    ConnectionFailed,  // DNS, TLS handshake, timeout
    Unauthorized,      // 401/403 — token wrong or missing
    ServerError,       // 5xx, including the proxy's own 502 upstream failure
    BadResponse,       // 200 but unparsable or contract-violating body
};

struct FetchResult {
    FetchError error = FetchError::None;
    int httpStatus   = 0;
    DashboardData data;

    bool ok() const
    {
        return error == FetchError::None;
    }
};

// Performs GET /v1/dashboard over TLS and parses the response.
// Never logs the bearer token or any Authorization header (PRD FR-19).
FetchResult fetchDashboard();

// Human-readable Polish reason for the disconnected screen.
const char* fetchErrorText(FetchError error);
