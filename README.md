# SmartOps Teams Support

Bidirectional support bridge between PEAS Microsoft Teams channels and SmartOps Frappe Helpdesk.

```text
PEAS Teams channel <-> standalone bridge <-> SmartOps Helpdesk
                                      ^
                         minimal Frappe connector
```

New Teams root posts create Helpdesk tickets. Teams thread replies become received ticket communications. ERP Champions use Helpdesk's normal **Reply** action; only Teams-linked tickets are routed through the bridge instead of email.

## Components

- `bridge/`: FastAPI service, Microsoft OAuth/Graph webhooks, Frappe REST calls, subscription renewal, and SQLite idempotency state.
- [`smartops_teams_connector`](https://github.com/ealecho/smartops_teams_connector): small Frappe app that marks Teams tickets and routes their normal Helpdesk replies to the bridge.

## Microsoft Entra setup

1. Create an app registration in the PEAS tenant with a Web redirect URI:
   `https://<bridge-host>/oauth/callback`.
2. Add delegated Microsoft Graph permissions and grant tenant admin consent:
   - `ChannelMessage.Read.All`
   - `ChannelMessage.Send`
   - `User.ReadBasic.All`
   - `offline_access`
   - `User.Read`
3. Create a client secret.
4. Use a licensed PEAS support account that is a member of every configured Team/channel.

Normal channel replies require delegated `ChannelMessage.Send`; application-only message creation is reserved for migration scenarios.

## Bridge setup

```bash
cd /path/to/smartops_teams_support
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Fill `.env`, export its values using your process manager, then run:

```bash
uvicorn bridge.app:app --host 0.0.0.0 --port 8000 --proxy-headers
```

The public URL must be HTTPS. Persist `DATABASE_PATH`; it contains encrypted OAuth credentials and synchronization state.

Connect the PEAS support account:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" "$PUBLIC_URL/oauth/start"
```

Open the returned `authorization_url` and complete Microsoft sign-in.

Register each support channel (IDs are available from the Teams channel link):

```bash
curl -X POST "$PUBLIC_URL/admin/channels" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"team_id":"TEAM_ID","channel_id":"CHANNEL_ID","helpdesk_team":"Finance"}'
```

The bridge creates and renews one Graph subscription per configured channel. Only new posts received after registration are imported.

## SmartOps connector setup

Install the connector in the bench that hosts Helpdesk:

```bash
bench get-app https://github.com/ealecho/smartops_teams_connector
bench --site <site> install-app smartops_teams_connector
bench --site <site> migrate
```

Open **SmartOps Teams Connector Settings**, enter the public bridge URL and the same `CONNECTOR_TOKEN`, then enable it.

Create a dedicated Frappe API user with permission to create/update `HD Ticket` and `Communication` records. Put its API key and secret in the bridge environment. Do not use Administrator credentials.

## Operations

- Health: `GET /health`
- Channels: `GET /admin/channels` with the admin bearer token
- Bridge logs show inbound processing and renewal failures.
- Connector delivery failures appear in Frappe Error Log and add an internal ticket comment.
- An outbound reply is never automatically replayed after an ambiguous network failure, avoiding duplicate customer-visible Teams messages.

## Scope

V1 synchronizes text/HTML root posts, replies, and edits. Attachments, reactions, cards, deletions, status synchronization, and historical import are intentionally excluded.

## Tests

```bash
pytest
```
