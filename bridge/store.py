import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet


class Store:
    def __init__(self, path: str, fernet_key: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(fernet_key.encode())
        self.migrate()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def migrate(self):
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS secrets (
                    name TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS channels (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    helpdesk_team TEXT NOT NULL DEFAULT '',
                    subscription_id TEXT,
                    expires_at TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (team_id, channel_id)
                );
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    root_message_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    communication_id TEXT,
                    direction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    modified_at TEXT NOT NULL,
                    UNIQUE(team_id, channel_id, message_id),
                    UNIQUE(communication_id)
                );
                """
            )

    def set_secret(self, name: str, value: str):
        encrypted = self.cipher.encrypt(value.encode())
        with self.connection() as db:
            db.execute(
                "INSERT INTO secrets(name, value) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (name, encrypted),
            )

    def get_secret(self, name: str) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT value FROM secrets WHERE name=?", (name,)).fetchone()
        return self.cipher.decrypt(row["value"]).decode() if row else None

    def upsert_channel(self, team_id: str, channel_id: str, helpdesk_team: str):
        with self.connection() as db:
            db.execute(
                "INSERT INTO channels(team_id, channel_id, helpdesk_team) VALUES(?, ?, ?) "
                "ON CONFLICT(team_id, channel_id) DO UPDATE SET "
                "helpdesk_team=excluded.helpdesk_team, enabled=1",
                (team_id, channel_id, helpdesk_team),
            )

    def channel(self, team_id: str, channel_id: str):
        with self.connection() as db:
            return db.execute(
                "SELECT * FROM channels WHERE team_id=? AND channel_id=? AND enabled=1",
                (team_id, channel_id),
            ).fetchone()

    def channels(self):
        with self.connection() as db:
            return db.execute("SELECT * FROM channels WHERE enabled=1").fetchall()

    def channel_for_subscription(self, subscription_id: str):
        with self.connection() as db:
            return db.execute(
                "SELECT * FROM channels WHERE subscription_id=? AND enabled=1", (subscription_id,)
            ).fetchone()

    def set_subscription(self, team_id: str, channel_id: str, subscription_id: str, expires_at: str):
        with self.connection() as db:
            db.execute(
                "UPDATE channels SET subscription_id=?, expires_at=? WHERE team_id=? AND channel_id=?",
                (subscription_id, expires_at, team_id, channel_id),
            )

    def link(self, team_id: str, channel_id: str, message_id: str):
        with self.connection() as db:
            return db.execute(
                "SELECT * FROM links WHERE team_id=? AND channel_id=? AND message_id=?",
                (team_id, channel_id, message_id),
            ).fetchone()

    def root_link(self, team_id: str, channel_id: str, root_message_id: str):
        with self.connection() as db:
            return db.execute(
                "SELECT * FROM links WHERE team_id=? AND channel_id=? "
                "AND message_id=? AND direction='inbound'",
                (team_id, channel_id, root_message_id),
            ).fetchone()

    def add_link(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        root_message_id: str,
        ticket_id: str,
        communication_id: str | None,
        direction: str,
        status: str = "sent",
    ) -> bool:
        try:
            with self.connection() as db:
                db.execute(
                    "INSERT INTO links(team_id, channel_id, message_id, root_message_id, "
                    "ticket_id, communication_id, direction, status, modified_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        team_id,
                        channel_id,
                        message_id,
                        root_message_id,
                        ticket_id,
                        communication_id,
                        direction,
                        status,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_outbound(self, communication_id: str, message_id: str | None, status: str, error: str | None = None):
        with self.connection() as db:
            if message_id:
                db.execute(
                    "UPDATE links SET message_id=?, status=?, error=?, modified_at=? WHERE communication_id=?",
                    (message_id, status, error, datetime.now(UTC).isoformat(), communication_id),
                )
            else:
                db.execute(
                    "UPDATE links SET status=?, error=?, modified_at=? WHERE communication_id=?",
                    (status, error, datetime.now(UTC).isoformat(), communication_id),
                )
