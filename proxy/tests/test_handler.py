"""Tests for routing, authentication, the error contract and the cache."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from flask import Flask

from app.handler import CACHE_TTL_SECONDS, ResponseCache, handle_request
from tests.conftest import (
    FailingMetricClient,
    FailingSecretClient,
    FakeMetricClient,
    FakeSecretClient,
)
from tests.test_dashboard import healthy_series

NOW = datetime(2026, 8, 28, 20, 0, 0, tzinfo=UTC)

app = Flask(__name__)


def call(
    config,
    *,
    path: str = "/v1/dashboard",
    method: str = "GET",
    authorization: str | None = "Bearer correct-token",
    secret_client=None,
    metric_client=None,
    cache: ResponseCache | None = None,
    monotonic_value: float = 0.0,
):
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    with app.test_request_context(path=path, method=method, headers=headers):
        from flask import request

        return handle_request(
            request,
            config=config,
            secret_client=secret_client or FakeSecretClient(),
            metric_client=metric_client or FakeMetricClient(healthy_series()),
            clock=lambda: NOW,
            cache=cache,
            monotonic=lambda: monotonic_value,
        )


def body(response) -> dict:
    return json.loads(response.get_data(as_text=True))


# --- routing ---------------------------------------------------------------


def test_unknown_path_is_404(config):
    response = call(config, path="/v1/nope")
    assert response.status_code == 404
    assert body(response)["error"]["code"] == "not_found"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_write_methods_are_rejected(config, method):
    """There is no write surface: every mutating verb is refused."""
    response = call(config, method=method)
    assert response.status_code == 405
    assert response.headers["Allow"] == "GET"


# --- authentication --------------------------------------------------------


def test_missing_authorization_is_401(config):
    response = call(config, authorization=None)
    assert response.status_code == 401
    assert body(response)["error"]["code"] == "unauthorized"


def test_malformed_authorization_is_401(config):
    response = call(config, authorization="Token abc")
    assert response.status_code == 401


def test_wrong_token_is_403(config):
    response = call(config, authorization="Bearer wrong-token")
    assert response.status_code == 403
    assert body(response)["error"]["code"] == "forbidden"


def test_secret_backend_failure_is_503(config):
    response = call(config, secret_client=FailingSecretClient())
    assert response.status_code == 503
    assert body(response)["error"]["code"] == "backend_unavailable"


def test_token_is_read_per_request(config):
    """Rotating the secret must take effect without a redeploy."""
    secret_client = FakeSecretClient()
    call(config, secret_client=secret_client)
    call(config, secret_client=secret_client)
    assert secret_client.calls == 2


# --- error contract --------------------------------------------------------


def test_error_bodies_carry_no_provider_detail(config):
    response = call(config, metric_client=FailingMetricClient())
    assert response.status_code == 502
    payload = body(response)
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message"}
    # The provider's message ("monitoring API is down") must not surface, nor
    # may the project id.
    assert "down" not in payload["error"]["message"]
    assert config.project_id not in json.dumps(payload)


# --- success + cache -------------------------------------------------------


def test_successful_response_shape(config):
    response = call(config)
    assert response.status_code == 200
    payload = body(response)
    assert payload["overallStatus"] == "ok"
    assert "nightly" in payload and "execution" in payload
    assert response.headers["Cache-Control"] == f"max-age={int(CACHE_TTL_SECONDS)}"


def test_response_is_compact_and_small(config):
    response = call(config)
    raw = response.get_data(as_text=True)
    assert ", " not in raw  # compact separators
    assert len(raw) < 4096  # PRD US-104: under 4 KB


def test_cache_hit_skips_monitoring(config):
    cache = ResponseCache()
    client = FakeMetricClient(healthy_series())
    call(config, metric_client=client, cache=cache, monotonic_value=100.0)
    first_call_count = len(client.requests)
    assert first_call_count > 0

    call(config, metric_client=client, cache=cache, monotonic_value=130.0)
    assert len(client.requests) == first_call_count  # served from cache


def test_cache_expires(config):
    cache = ResponseCache()
    client = FakeMetricClient(healthy_series())
    call(config, metric_client=client, cache=cache, monotonic_value=100.0)
    first_call_count = len(client.requests)

    call(
        config,
        metric_client=client,
        cache=cache,
        monotonic_value=100.0 + CACHE_TTL_SECONDS + 1,
    )
    assert len(client.requests) > first_call_count


def test_cache_is_not_consulted_before_authentication(config):
    """A cached payload must never be served to an unauthenticated caller."""
    cache = ResponseCache()
    call(config, cache=cache, monotonic_value=0.0)
    response = call(
        config, authorization="Bearer wrong-token", cache=cache, monotonic_value=1.0
    )
    assert response.status_code == 403
