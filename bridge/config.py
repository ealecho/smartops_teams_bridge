from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Settings:
    public_url: str
    admin_token: str
    connector_token: str
    graph_client_state: str
    fernet_key: str
    database_path: str
    tenant_id: str
    client_id: str
    client_secret: str
    frappe_url: str
    frappe_api_key: str
    frappe_api_secret: str
    fallback_requester: str

    @classmethod
    def from_env(cls) -> "Settings":
        values = {
            "public_url": getenv("PUBLIC_URL", ""),
            "admin_token": getenv("ADMIN_TOKEN", ""),
            "connector_token": getenv("CONNECTOR_TOKEN", ""),
            "graph_client_state": getenv("GRAPH_CLIENT_STATE", ""),
            "fernet_key": getenv("FERNET_KEY", ""),
            "database_path": getenv("DATABASE_PATH", "smartops_teams_support.db"),
            "tenant_id": getenv("MICROSOFT_TENANT_ID", ""),
            "client_id": getenv("MICROSOFT_CLIENT_ID", ""),
            "client_secret": getenv("MICROSOFT_CLIENT_SECRET", ""),
            "frappe_url": getenv("FRAPPE_URL", ""),
            "frappe_api_key": getenv("FRAPPE_API_KEY", ""),
            "frappe_api_secret": getenv("FRAPPE_API_SECRET", ""),
            "fallback_requester": getenv("FRAPPE_FALLBACK_REQUESTER", ""),
        }
        missing = [key for key, value in values.items() if key != "database_path" and not value]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(**values)
