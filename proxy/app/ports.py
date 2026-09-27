"""Structural interfaces for the GCP clients.

Reaching the providers only through these Protocols keeps the handler testable
without the google-cloud libraries installed, and makes the read-only surface
explicit: there is no write method anywhere in this file.
"""

from collections.abc import Iterable
from typing import Any, Protocol


class SecretPayload(Protocol):
    data: bytes


class SecretResponse(Protocol):
    payload: SecretPayload


class SecretClient(Protocol):
    def access_secret_version(self, *, name: str) -> SecretResponse: ...


class MetricServiceClient(Protocol):
    """The single read call this proxy makes against Cloud Monitoring.

    Deliberately narrow: no create_time_series, so the runtime service account's
    monitoring.viewer role has nothing here to exceed.
    """

    def list_time_series(
        self, request: dict[str, Any], *, timeout: float, retry: None
    ) -> Iterable[Any]: ...
