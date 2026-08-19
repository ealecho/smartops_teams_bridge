from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from .config import Settings
from .store import Store


class GraphClient:
    scopes = "offline_access User.Read User.ReadBasic.All ChannelMessage.Read.All ChannelMessage.Send"

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.http = httpx.AsyncClient(timeout=20)
        self._access_token: str | None = None
        self._access_expires = datetime.min.replace(tzinfo=UTC)

    @property
    def token_url(self):
        return f"https://login.microsoftonline.com/{self.settings.tenant_id}/oauth2/v2.0/token"

    def authorization_url(self, state: str, challenge: str):
        query = urlencode(
            {
                "client_id": self.settings.client_id,
                "response_type": "code",
                "redirect_uri": f"{self.settings.public_url}/oauth/callback",
                "response_mode": "query",
                "scope": self.scopes,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"https://login.microsoftonline.com/{self.settings.tenant_id}/oauth2/v2.0/authorize?{query}"

    async def exchange_code(self, code: str, verifier: str):
        response = await self.http.post(
            self.token_url,
            data={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{self.settings.public_url}/oauth/callback",
                "scope": self.scopes,
                "code_verifier": verifier,
            },
        )
        response.raise_for_status()
        self._save_tokens(response.json())

    def _save_tokens(self, payload: dict):
        if payload.get("refresh_token"):
            self.store.set_secret("refresh_token", payload["refresh_token"])
        self._access_token = payload["access_token"]
        self._access_expires = datetime.now(UTC) + timedelta(seconds=payload.get("expires_in", 3600) - 60)

    async def access_token(self):
        if self._access_token and datetime.now(UTC) < self._access_expires:
            return self._access_token
        refresh_token = self.store.get_secret("refresh_token")
        if not refresh_token:
            raise RuntimeError("Microsoft account is not connected")
        response = await self.http.post(
            self.token_url,
            data={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": self.scopes,
            },
        )
        response.raise_for_status()
        self._save_tokens(response.json())
        return self._access_token

    async def request(self, method: str, path: str, **kwargs):
        token = await self.access_token()
        response = await self.http.request(
            method,
            f"https://graph.microsoft.com/v1.0{path}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )
        if response.status_code == 401:
            self._access_token = None
            token = await self.access_token()
            response = await self.http.request(
                method,
                f"https://graph.microsoft.com/v1.0{path}",
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        response.raise_for_status()
        return response.json() if response.content else {}

    async def me(self):
        return await self.request("GET", "/me?$select=id,displayName,mail,userPrincipalName")

    async def user(self, user_id: str):
        return await self.request(
            "GET", f"/users/{user_id}?$select=id,displayName,mail,userPrincipalName"
        )

    async def message(self, team_id: str, channel_id: str, message_id: str, root_id: str | None = None):
        path = f"/teams/{team_id}/channels/{channel_id}/messages/{root_id or message_id}"
        if root_id:
            path += f"/replies/{message_id}"
        return await self.request("GET", path)

    async def reply(self, team_id: str, channel_id: str, root_id: str, content: str):
        return await self.request(
            "POST",
            f"/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies",
            json={"body": {"contentType": "html", "content": content}},
        )

    async def subscribe(self, team_id: str, channel_id: str, subscription_id: str | None = None):
        expires = datetime.now(UTC) + timedelta(days=2, hours=23)
        payload = {"expirationDateTime": expires.isoformat().replace("+00:00", "Z")}
        if subscription_id:
            data = await self.request("PATCH", f"/subscriptions/{subscription_id}", json=payload)
        else:
            payload.update(
                {
                    "changeType": "created,updated",
                    "notificationUrl": f"{self.settings.public_url}/webhooks/graph",
                    "lifecycleNotificationUrl": f"{self.settings.public_url}/webhooks/graph",
                    "resource": f"/teams/{team_id}/channels/{channel_id}/messages",
                    "includeResourceData": False,
                    "clientState": self.settings.graph_client_state,
                }
            )
            data = await self.request("POST", "/subscriptions", json=payload)
        return data["id"], data["expirationDateTime"]

    async def notification_resource(self, resource: str):
        return await self.request("GET", "/" + resource.lstrip("/"))


class FrappeClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.frappe_url.rstrip("/"),
            headers={
                "Authorization": f"token {settings.frappe_api_key}:{settings.frappe_api_secret}",
                "Content-Type": "application/json",
            },
            timeout=20,
        )

    async def request(self, method: str, path: str, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        response.raise_for_status()
        data = response.json()
        return data.get("data", data.get("message", data))

    async def create_ticket(self, payload: dict):
        return await self.request("POST", "/api/resource/HD%20Ticket", json=payload)

    async def update_ticket(self, ticket_id: str, payload: dict):
        return await self.request("PUT", f"/api/resource/HD%20Ticket/{ticket_id}", json=payload)

    async def create_communication(self, payload: dict):
        return await self.request("POST", "/api/resource/Communication", json=payload)

    async def update_communication(self, communication_id: str, content: str):
        return await self.request(
            "PUT", f"/api/resource/Communication/{communication_id}", json={"content": content}
        )
