import asyncio
import base64
import hashlib
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from .clients import FrappeClient, GraphClient
from .config import Settings
from .store import Store
from .sync import Synchronizer

log = logging.getLogger(__name__)
settings = Settings.from_env()
store = Store(settings.database_path, settings.fernet_key)
graph = GraphClient(settings, store)
frappe = FrappeClient(settings)
sync = Synchronizer(settings, store, graph, frappe)


def require_token(expected: str):
    def check(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, f"Bearer {expected}"):
            raise HTTPException(401, "Invalid bearer token")
    return check


admin = require_token(settings.admin_token)
connector = require_token(settings.connector_token)


class Channel(BaseModel):
    team_id: str
    channel_id: str
    helpdesk_team: str = ""


class Reply(BaseModel):
    ticket_id: str
    communication_id: str
    agent_name: str
    content: str
    team_id: str
    channel_id: str
    root_message_id: str


async def renew_loop():
    while True:
        await asyncio.sleep(3600)
        for channel in store.channels():
            try:
                expires = datetime.fromisoformat((channel["expires_at"] or "1970-01-01+00:00").replace("Z", "+00:00"))
                if expires > datetime.now(UTC) + timedelta(hours=24):
                    continue
                sub_id, expiry = await graph.subscribe(
                    channel["team_id"], channel["channel_id"], channel["subscription_id"]
                )
                store.set_subscription(channel["team_id"], channel["channel_id"], sub_id, expiry)
            except Exception:
                log.exception("Failed to renew Teams subscription for %s", channel["channel_id"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(renew_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await graph.http.aclose()
    await frappe.http.aclose()


app = FastAPI(title="SmartOps Teams Support Bridge", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "microsoft_connected": bool(store.get_secret("refresh_token"))}


@app.get("/oauth/start", dependencies=[Depends(admin)])
async def oauth_start():
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    store.set_secret("oauth_state", state)
    store.set_secret("oauth_verifier", verifier)
    return {"authorization_url": graph.authorization_url(state, challenge)}


@app.get("/oauth/callback")
async def oauth_callback(code: str, state: str):
    expected = store.get_secret("oauth_state") or ""
    if not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "Invalid OAuth state")
    verifier = store.get_secret("oauth_verifier")
    await graph.exchange_code(code, verifier)
    profile = await graph.me()
    store.set_secret("connected_user_id", profile["id"])
    store.set_secret("oauth_state", secrets.token_urlsafe(32))
    store.set_secret("oauth_verifier", secrets.token_urlsafe(32))
    return PlainTextResponse(f"Connected Microsoft account: {profile.get('displayName', profile['id'])}")


@app.post("/admin/channels", dependencies=[Depends(admin)])
async def add_channel(channel: Channel):
    store.upsert_channel(channel.team_id, channel.channel_id, channel.helpdesk_team)
    subscription_id, expires_at = await graph.subscribe(channel.team_id, channel.channel_id)
    store.set_subscription(channel.team_id, channel.channel_id, subscription_id, expires_at)
    return {"subscription_id": subscription_id, "expires_at": expires_at}


@app.get("/admin/channels", dependencies=[Depends(admin)])
async def list_channels():
    return [dict(row) for row in store.channels()]


async def process_notifications(items: list[dict]):
    for item in items:
        try:
            if item.get("lifecycleEvent") in {"subscriptionRemoved", "reauthorizationRequired"}:
                channel = store.channel_for_subscription(item.get("subscriptionId", ""))
                if channel:
                    subscription_id, expires_at = await graph.subscribe(
                        channel["team_id"], channel["channel_id"]
                    )
                    store.set_subscription(
                        channel["team_id"], channel["channel_id"], subscription_id, expires_at
                    )
            else:
                await sync.notification(item)
        except Exception:
            log.exception("Failed to process Graph notification")


@app.post("/webhooks/graph")
async def graph_webhook(
    request: Request,
    background: BackgroundTasks,
    validation_token: str | None = Query(default=None, alias="validationToken"),
):
    if validation_token is not None:
        return PlainTextResponse(validation_token)
    payload = await request.json()
    accepted = [
        item
        for item in payload.get("value", [])
        if secrets.compare_digest(str(item.get("clientState", "")), settings.graph_client_state)
    ]
    background.add_task(process_notifications, accepted)
    return PlainTextResponse("", status_code=202)


@app.post("/connector/reply", dependencies=[Depends(connector)])
async def connector_reply(reply: Reply):
    return await sync.outbound(reply.model_dump())
