from flask import Request, Response
from google.cloud import monitoring_v3, secretmanager

from app.config import Config
from app.handler import ResponseCache, handle_request
from app.ports import MetricServiceClient, SecretClient

# Cloud Functions gen2 instances are long-lived, so clients and the response cache
# are created once and shared across requests on a warm instance.
_secret_client: SecretClient | None = None
_metric_client: MetricServiceClient | None = None
_cache = ResponseCache()


def _get_secret_client() -> SecretClient:
    global _secret_client
    if _secret_client is None:
        _secret_client = secretmanager.SecretManagerServiceClient()
    return _secret_client


def _get_metric_client() -> MetricServiceClient:
    global _metric_client
    if _metric_client is None:
        _metric_client = monitoring_v3.MetricServiceClient()
    return _metric_client


def api(request: Request) -> Response:
    """Google Cloud Functions gen2 HTTP entry point."""
    return handle_request(
        request,
        config=Config.from_environment(),
        secret_client=_get_secret_client(),
        metric_client=_get_metric_client(),
        cache=_cache,
    )
