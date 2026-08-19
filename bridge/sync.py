import html
import re
from html.parser import HTMLParser

from .clients import FrappeClient, GraphClient
from .config import Settings
from .store import Store

RESOURCE_RE = re.compile(
    r"teams(?:\('([^']+)'\)|/([^/]+))/channels(?:\('([^']+)'\)|/([^/]+))/messages(?:\('([^']+)'\)|/([^/]+))"
)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


def text_content(value: str) -> str:
    parser = _Text()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


def safe_html(value: str) -> str:
    return html.escape(text_content(value)).replace("\n", "<br>")


def parse_resource(resource: str) -> tuple[str, str, str]:
    match = RESOURCE_RE.search(resource or "")
    if not match:
        raise ValueError("Unsupported Graph notification resource")
    team_id, team_alt, channel_id, channel_alt, message_id, message_alt = match.groups()
    return team_id or team_alt, channel_id or channel_alt, message_id or message_alt


def ticket_subject(message: dict) -> str:
    value = text_content(message.get("subject") or message.get("body", {}).get("content", ""))
    return (value[:117] + "...") if len(value) > 120 else (value or "Teams support request")


class Synchronizer:
    def __init__(self, settings: Settings, store: Store, graph: GraphClient, frappe: FrappeClient):
        self.settings = settings
        self.store = store
        self.graph = graph
        self.frappe = frappe

    async def notification(self, item: dict):
        team_id, channel_id, resource_message_id = parse_resource(item.get("resource", ""))
        message_id = (item.get("resourceData") or {}).get("id") or resource_message_id
        channel = self.store.channel(team_id, channel_id)
        if not channel:
            return

        message = await self.graph.notification_resource(item["resource"])
        root_id = message.get("replyToId")
        sender = (message.get("from") or {}).get("user") or {}
        if sender.get("id") == self.store.get_secret("connected_user_id"):
            return

        existing = self.store.link(team_id, channel_id, message_id)
        if existing:
            await self._update(existing, message)
        elif root_id:
            await self._reply(channel, message, root_id)
        else:
            await self._root(channel, message)

    async def _author(self, message: dict):
        sender = (message.get("from") or {}).get("user") or {}
        profile = {}
        if sender.get("id"):
            try:
                profile = await self.graph.user(sender["id"])
            except Exception:
                pass
        return {
            "name": profile.get("displayName") or sender.get("displayName") or "Teams user",
            "email": profile.get("mail") or profile.get("userPrincipalName") or self.settings.fallback_requester,
        }

    async def _root(self, channel, message: dict):
        team_id, channel_id = channel["team_id"], channel["channel_id"]
        message_id = message["id"]
        if self.store.link(team_id, channel_id, message_id):
            return
        author = await self._author(message)
        body = safe_html(message.get("body", {}).get("content", ""))
        teams_url = message.get("webUrl") or ""
        description = f"<p><strong>{html.escape(author['name'])}</strong> via Microsoft Teams</p><p>{body}</p>"
        if teams_url:
            description += f'<p><a href="{html.escape(teams_url, quote=True)}">Open thread in Teams</a></p>'
        payload = {
            "subject": ticket_subject(message),
            "description": description,
            "raised_by": author["email"],
            "agent_group": channel["helpdesk_team"] or None,
            "is_teams_ticket": 1,
            "teams_team_id": team_id,
            "teams_channel_id": channel_id,
            "teams_root_message_id": message_id,
            "teams_thread_url": teams_url,
        }
        ticket = await self.frappe.create_ticket({key: value for key, value in payload.items() if value is not None})
        self.store.add_link(team_id, channel_id, message_id, message_id, str(ticket["name"]), None, "inbound")

    async def _reply(self, channel, message: dict, root_id: str):
        team_id, channel_id = channel["team_id"], channel["channel_id"]
        root = self.store.root_link(team_id, channel_id, root_id)
        if not root:
            return
        author = await self._author(message)
        content = f"<p><strong>{html.escape(author['name'])}</strong> via Microsoft Teams</p><p>{safe_html(message.get('body', {}).get('content', ''))}</p>"
        communication = await self.frappe.create_communication(
            {
                "communication_type": "Communication",
                "communication_medium": "Chat",
                "sent_or_received": "Received",
                "subject": "Teams reply",
                "sender": author["email"],
                "content": content,
                "status": "Linked",
                "reference_doctype": "HD Ticket",
                "reference_name": root["ticket_id"],
            }
        )
        self.store.add_link(
            team_id,
            channel_id,
            message["id"],
            root_id,
            root["ticket_id"],
            str(communication["name"]),
            "inbound",
        )

    async def _update(self, link, message: dict):
        content = safe_html(message.get("body", {}).get("content", ""))
        if link["communication_id"]:
            await self.frappe.update_communication(link["communication_id"], content)
        elif link["message_id"] == link["root_message_id"]:
            await self.frappe.update_ticket(
                link["ticket_id"], {"subject": ticket_subject(message), "description": content}
            )

    async def outbound(self, payload: dict):
        communication_id = str(payload["communication_id"])
        placeholder = f"outbound:{communication_id}"
        created = self.store.add_link(
            payload["team_id"],
            payload["channel_id"],
            placeholder,
            payload["root_message_id"],
            str(payload["ticket_id"]),
            communication_id,
            "outbound",
            "pending",
        )
        if not created:
            return {"status": "duplicate"}
        content = f"<p><strong>ERP Champions – {html.escape(payload['agent_name'])}</strong></p><p>{safe_html(payload['content'])}</p>"
        try:
            response = await self.graph.reply(
                payload["team_id"], payload["channel_id"], payload["root_message_id"], content
            )
        except Exception as exc:
            self.store.update_outbound(communication_id, None, "failed", str(exc)[:500])
            raise
        self.store.update_outbound(communication_id, response["id"], "sent")
        return {"status": "sent", "message_id": response["id"]}
