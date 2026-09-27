# GPW Radar Cardputer proxy

Python 3.12 Google Cloud Functions gen2 HTTP backend. Reads `gpw_*` custom metrics
from Google Cloud Monitoring and serves them to the Cardputer as one small JSON
document.

Read-only by construction: the single route is a `GET`, the runtime service
account holds `roles/monitoring.viewer` (never `metricWriter`), and there is no
method on the client Protocol that could write a time series.

PRD: [`../tasks/prd-cardputer-gpw-monitor.md`](../tasks/prd-cardputer-gpw-monitor.md)
(US-101 … US-105).

## Local quality gates

```bash
make install
make check     # ruff format --check + ruff check + pytest
```

The test suite makes no network calls and needs no GCP credentials.

## Endpoint

`GET /v1/dashboard`, `Authorization: Bearer <token>`.

```json
{
  "generatedAt": "2026-08-28T19:42:11Z",
  "overallStatus": "ok",
  "nightly": {
    "lastRunStatus": "completed",
    "lastRunEpochSeconds": 1787012069,
    "lastSuccessEpochSeconds": 1787012040,
    "durationSeconds": 743.2,
    "stepsOk": 7,
    "stepsTotal": 7,
    "stepsSkipped": 0,
    "failedSteps": [],
    "espiReportsTotal": 2747,
    "firecrawlCreditsRemaining": 4827,
    "firecrawlCreditsPlan": 5000
  },
  "execution": {
    "sessionUp": true,
    "twoFactorRequired": false,
    "inhibitorActive": false,
    "lastReconcileEpochSeconds": 1787019000,
    "orders": {
      "filled":   {"total": 143, "delta24h": 3},
      "unfilled": {"total": 12,  "delta24h": 0},
      "rejected": {"total": 2,   "delta24h": null}
    },
    "openPositions": 7
  }
}
```

### The one rule that matters

**`null` means "unknown". It never means zero.**

`gpw_radar` emits one-hot series over a full label domain, so a missing point
means *the run never happened*, not *the value was 0*. Anything that renders a
`null` as `0` turns a dead pipeline into a quiet night. Specifically:

| Field is `null` when… | Why |
|---|---|
| any metric | no point in the 48 h lookback window |
| `sessionUp`, `inhibitorActive`, `twoFactorRequired` | last `exec-health-probe` point older than 36 h |
| `lastRunStatus` | the latest nightly emission lacks a lifecycle metric |
| `firecrawlCreditsPlan` | no plan point from the same observation as the balance |
| `delta24h` | no counter point old enough to subtract from |

A *measured* zero (a failed step, a down Gateway, an unchanged counter) is
returned as `0` and is a different answer.

### Timestamps are seconds

`lastRunEpochSeconds`, `lastSuccessEpochSeconds` and `lastReconcileEpochSeconds` are unix **seconds**,
matching what the `gpw_radar` emitter writes. Treating them as milliseconds is
what once made a Grafana panel report "57 years ago".

### Latest nightly result and additional readings

There is no dedicated overall nightly status metric in GPW Radar. The proxy
infers `lastRunStatus` from its terminal-run emission contract: duration is
emitted for completed and failed attempts; last-success is emitted only for
completed attempts. Both share the bundle's measurement timestamp. For the
latest lifecycle/step emission, a matching last-success means `completed`,
otherwise a matching duration means `error`; without either, the result is
`null`. `lastRunEpochSeconds` is that emission's timestamp, not the start time.
An attempt that emits no metrics cannot be detected by this inference.

Steps and duration belong to the same latest emission; old successful steps
are not reused for a newer failed attempt. Step value `-1` is skipped and counted
in `stepsSkipped`; `0` names a failed step. A noncritical step can fail while the
whole run completes. This result is separate from the existing age alarm below.

| Dashboard field | Existing `custom.googleapis.com/` metric | Meaning |
|---|---|---|
| `execution.twoFactorRequired` | `gpw_ibkr_2fa_required` | Health probe indicates operator login/2FA is needed for live execution; false also covers paper/disabled execution. It does not prove that a challenge is currently displayed. |
| `nightly.espiReportsTotal` | `gpw_espi_reports_total` | Total ESPI records in GPW Radar's database, not today's downloads. |
| `nightly.firecrawlCreditsRemaining` | `gpw_firecrawl_credits_remaining` | Last reported Firecrawl account balance. |
| `nightly.firecrawlCreditsPlan` | `gpw_firecrawl_credits_plan` | Plan allowance reported with that balance. |

ESPI and Firecrawl are snapshots published by nightly, not live vendor queries.
The proxy requires no Firecrawl key or additional IAM permissions. Firmware
0.1.4 shows these readings on NIGHTLY and the explicit session/2FA states on
EGZEKUCJA; the page count remains two.

### `overallStatus`

Computed here, not on the device, so the alarm threshold can change without
reflashing firmware:

- `alarm` — outside Sunday in `Europe/Warsaw`,
  `gpw_nightly_last_success_timestamp_seconds` is older than 26 h **or** has no
  point in the window;
- `ok` — otherwise.

Nightly is not expected on Sunday. The exception lasts from local Sunday midnight
to Monday midnight, including daylight-saving changes. Timestamps and missing
values (`null`) remain unchanged; metric API failures still return an error.

### Errors

Every error body is `{"error": {"code": ..., "message": ...}}` with a fixed
message, so no provider text, project id or stack trace can leak.

| Status | Code | Meaning |
|---|---|---|
| 401 | `unauthorized` | missing or malformed `Authorization` |
| 403 | `forbidden` | wrong token |
| 404 | `not_found` | unknown path |
| 405 | `method_not_allowed` | wrong verb (response carries `Allow: GET`) |
| 502 | `monitoring_api_error` | Cloud Monitoring could not be read |
| 503 | `backend_unavailable` | Secret Manager could not be read |

A Cloud Monitoring failure is deliberately a 502 rather than a payload of nulls:
a body saying "everything is unknown" is indistinguishable from a dead pipeline.

### Cache

The assembled payload is cached in-process for 60 s (`Cache-Control: max-age=60`).
Cloud Monitoring reads are billed. Nightly data changes after runs; execution
data also changes after health probes and reconciliations. The cache is consulted **after** authentication, so a cached payload
is never served to an unauthenticated caller.

On a cache miss, twelve metric reads use four workers with a common query end
time. Each Monitoring RPC has a 5 s timeout and no automatic client retries;
upstream failures retain the 502 response contract.

## Deploy

Needs Cloud Functions deploy permission and permission to act as the runtime
service account.

### 1. Create the bearer token secret

No token belongs in source, in `--set-env-vars`, or in a shell history:

```bash
export GCP_PROJECT_ID="zippy-shift-411110"
export GCP_REGION="europe-central2"
export AUTH_SECRET_ID="gpw-cardputer-token"

gcloud secrets create "$AUTH_SECRET_ID" --replication-policy=automatic \
  --project="$GCP_PROJECT_ID"
openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add "$AUTH_SECRET_ID" \
  --data-file=- --project="$GCP_PROJECT_ID"
```

Read the value back once, to paste into the firmware's `src/secrets.h`:

```bash
gcloud secrets versions access latest --secret="$AUTH_SECRET_ID" \
  --project="$GCP_PROJECT_ID"
```

### 2. Deploy

```bash
make deploy      # or: ./deploy.sh
```

The script enables the APIs, creates the runtime service account, grants it
`roles/monitoring.viewer` plus a custom role holding only
`secretmanager.versions.access` **scoped to that one secret**, and deploys the
function. It prints the endpoint URL when done.

### 3. Verify

```bash
TOKEN=$(gcloud secrets versions access latest --secret="$AUTH_SECRET_ID" \
  --project="$GCP_PROJECT_ID")
URL=$(gcloud functions describe gpw-cardputer-proxy --gen2 \
  --region="$GCP_REGION" --project="$GCP_PROJECT_ID" \
  --format='value(serviceConfig.uri)')

curl -s -H "Authorization: Bearer $TOKEN" "$URL/v1/dashboard" | python3 -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' "$URL/v1/dashboard"   # expect 401
```

### 4. Point the firmware at it

In `src/secrets.h`:

```c
#define PROXY_URL "<URL>/v1/dashboard"
#define PROXY_TOKEN "<token>"
#define USE_MOCK_DATA 0
```

Build with `pio run -e cardputer`. For the installed Cardputer with Launcher,
follow [the partition-specific update instructions](../docs/cardputer-installation.md);
a standard upload would replace its partition layout.

## Running locally

```bash
gcloud auth application-default login
export GCP_PROJECT_ID="zippy-shift-411110"
export GCP_REGION="europe-central2"
export AUTH_SECRET_ID="gpw-cardputer-token"
make run     # functions-framework on :8080
```

Your ADC identity needs `roles/monitoring.viewer` and access to the secret.
