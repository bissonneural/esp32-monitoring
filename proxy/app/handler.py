import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from flask import Request, Response

from app.auth import AuthenticationError, authenticate
from app.config import Config
from app.dashboard import build_dashboard
from app.metrics import MonitoringError
from app.ports import MetricServiceClient, SecretClient

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]

#: Cloud Monitoring reads are billed, and the underlying data changes at most once
#: per nightly run, so repeated polls inside this window are served from memory.
CACHE_TTL_SECONDS = 60.0


# The single error response contract. Every error body is
# {"error": {"code": <machine readable>, "message": <human readable>}} with fixed
# messages, so no provider text, project id or stack trace can leak to a caller.
ERROR_MESSAGES: dict[str, str] = {
    "not_found": "Requested resource does not exist.",
    "method_not_allowed": "HTTP method is not allowed for this resource.",
    "unauthorized": "Authorization header is missing or malformed.",
    "forbidden": "Presented credentials were rejected.",
    "backend_unavailable": "Backend dependency is unavailable.",
    "monitoring_api_error": "Cloud Monitoring API call failed.",
}
_UNKNOWN_ERROR_MESSAGE = "Request could not be completed."


class ResponseCache:
    """Single-entry in-memory cache for the assembled dashboard payload.

    Cloud Functions gen2 instances are long-lived, so this survives across
    requests on a warm instance. A cold start simply repopulates it.
    """

    def __init__(self, ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._payload: dict[str, Any] | None = None
        self._stored_at: float = 0.0

    def get(self, *, now: float) -> dict[str, Any] | None:
        if self._payload is None:
            return None
        if now - self._stored_at >= self._ttl:
            return None
        return self._payload

    def put(self, payload: dict[str, Any], *, now: float) -> None:
        self._payload = payload
        self._stored_at = now


def _json_response(payload: dict[str, Any], status: int = 200) -> Response:
    return Response(
        json.dumps(payload, separators=(",", ":")),
        status=status,
        content_type="application/json",
    )


def _error(code: str, status: int) -> Response:
    message = ERROR_MESSAGES.get(code, _UNKNOWN_ERROR_MESSAGE)
    return _json_response({"error": {"code": code, "message": message}}, status)


def handle_request(
    request: Request,
    *,
    config: Config,
    secret_client: SecretClient,
    metric_client: MetricServiceClient,
    clock: Clock = lambda: datetime.now(UTC),
    cache: ResponseCache | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Response:
    """Route, authenticate and serve. The only route is a read."""

    routes = {"/v1/dashboard": "GET"}
    expected_method = routes.get(request.path)
    if expected_method is None:
        return _error("not_found", 404)
    if request.method != expected_method:
        response = _error("method_not_allowed", 405)
        response.headers["Allow"] = expected_method
        return response

    try:
        authenticate(request.headers.get("Authorization"), config, secret_client)
    except AuthenticationError as error:
        logger.warning("request_auth_rejected", extra={"auth_outcome": error.code})
        return _error(error.code, error.status_code)
    except Exception as error:  # noqa: BLE001 - secret backend failure, not a 500 leak
        logger.error(
            "authentication_dependency_failed",
            extra={"error_type": type(error).__name__},
        )
        return _error("backend_unavailable", 503)

    now = monotonic()
    if cache is not None:
        cached = cache.get(now=now)
        if cached is not None:
            response = _json_response(cached)
            response.headers["Cache-Control"] = f"max-age={int(CACHE_TTL_SECONDS)}"
            return response

    try:
        payload = build_dashboard(metric_client, config, now=clock())
    except MonitoringError:
        # Deliberately an error, not a payload full of nulls: a body that says
        # "everything is unknown" is indistinguishable from a dead pipeline.
        return _error("monitoring_api_error", 502)
    except Exception as error:  # noqa: BLE001
        logger.error(
            "dashboard_build_failed", extra={"error_type": type(error).__name__}
        )
        return _error("monitoring_api_error", 502)

    if cache is not None:
        cache.put(payload, now=now)

    response = _json_response(payload)
    response.headers["Cache-Control"] = f"max-age={int(CACHE_TTL_SECONDS)}"
    return response
