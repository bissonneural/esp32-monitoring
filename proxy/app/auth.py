import hmac
import re

from app.config import Config
from app.ports import SecretClient

_BEARER_PATTERN = re.compile(r"^Bearer ([^\s]+)$", re.IGNORECASE)


class AuthenticationError(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def authenticate(
    authorization: str | None, config: Config, secret_client: SecretClient
) -> None:
    """Reject the request unless it carries the configured bearer token.

    The expected value is read from Secret Manager per request, so rotating the
    secret takes effect without redeploying, and the token never exists in the
    function's environment or deploy arguments.
    """

    if authorization is None:
        raise AuthenticationError(401, "unauthorized")

    match = _BEARER_PATTERN.fullmatch(authorization)
    if match is None:
        raise AuthenticationError(401, "unauthorized")

    response = secret_client.access_secret_version(name=config.secret_version_name)
    try:
        expected = response.payload.data.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("Configured authentication secret is invalid") from error

    if not expected:
        raise RuntimeError("Configured authentication secret is empty")
    # Constant-time comparison: a timing oracle here would leak the token.
    if not hmac.compare_digest(match.group(1), expected):
        raise AuthenticationError(403, "forbidden")
