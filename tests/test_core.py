from cryptography.fernet import Fernet

from bridge.store import Store
from bridge.sync import parse_resource, text_content, ticket_subject


def test_parse_graph_resource_variants():
    assert parse_resource("teams('team')/channels('channel')/messages('message')") == (
        "team",
        "channel",
        "message",
    )
    assert parse_resource("teams/team/channels/channel/messages/message") == (
        "team",
        "channel",
        "message",
    )


def test_text_and_subject_are_bounded():
    assert text_content("<p>Hello <b>world</b></p>") == "Hello world"
    assert len(ticket_subject({"body": {"content": "x" * 200}})) == 120


def test_message_and_communication_ids_are_idempotent(tmp_path):
    store = Store(str(tmp_path / "test.db"), Fernet.generate_key().decode())
    args = ("team", "channel", "message", "message", "ticket", "communication", "inbound")
    assert store.add_link(*args)
    assert not store.add_link(*args)
