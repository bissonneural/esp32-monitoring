from dataclasses import dataclass
from os import environ


@dataclass(frozen=True)
class Config:
    """Fixed deployment configuration.

    The project whose metrics are read is deployment configuration, never request
    data — a caller cannot point this function at a different project.
    """

    project_id: str
    region: str
    secret_id: str

    @classmethod
    def from_environment(cls) -> "Config":
        values = {
            "project_id": environ.get("GCP_PROJECT_ID", "").strip(),
            "region": environ.get("GCP_REGION", "").strip(),
            "secret_id": environ.get("AUTH_SECRET_ID", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeError(f"Missing deployment configuration: {names}")
        return cls(**values)

    @property
    def secret_version_name(self) -> str:
        return f"projects/{self.project_id}/secrets/{self.secret_id}/versions/latest"

    @property
    def project_name(self) -> str:
        return f"projects/{self.project_id}"
