"""SQLite state store (design §4), WAL mode, migrated in place on startup.

Migration 1 is the design §4 schema verbatim. Migration 2 adds the v0.2 hooks:
``messages.last_inbound_at`` (for the "classified thread got a new reply" signal
in ``mail_rollup``) and a ``drafts`` table (so ``draft_reply`` is idempotent —
one draft per source message, updated in place).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hermes_ops import config

_MIGRATION_1 = """
CREATE TABLE messages (
    message_id      TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    sender          TEXT,
    subject         TEXT,
    received_at     TEXT,               -- ISO 8601, America/Bogota
    category        TEXT,               -- action|waiting|reference|noise|fyi
    reason          TEXT,
    classifier      TEXT,               -- rules-v3 | agent | human | migrated
    classified_at   TEXT,
    label_applied   INTEGER DEFAULT 0   -- Gmail write confirmed
);
CREATE INDEX idx_messages_category ON messages(category, received_at);
CREATE INDEX idx_messages_thread   ON messages(thread_id);

CREATE TABLE events (
    event_id        TEXT PRIMARY KEY,   -- Google-assigned
    calendar_id     TEXT NOT NULL,
    dedupe_key      TEXT NOT NULL UNIQUE,
    title           TEXT,
    starts_at       TEXT,
    ends_at         TEXT,
    created_at      TEXT,
    verified        INTEGER DEFAULT 0   -- read-back succeeded
);

CREATE TABLE links (
    message_id      TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    method          TEXT,               -- ics | extracted | manual
    created_at      TEXT,
    PRIMARY KEY (message_id, event_id)
);

CREATE TABLE audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              TEXT NOT NULL,
    tool            TEXT NOT NULL,
    args_digest     TEXT,               -- hash, not raw args
    outcome         TEXT,               -- ok | refused | error
    detail          TEXT
);
"""

_MIGRATION_2 = """
ALTER TABLE messages ADD COLUMN last_inbound_at TEXT;

CREATE TABLE drafts (
    message_id  TEXT PRIMARY KEY,
    draft_id    TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_1),
    (2, _MIGRATION_2),
]

LATEST = MIGRATIONS[-1][0]


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    current = _schema_version(conn)
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        conn.commit()
        current = version
    return current


def init(path: Path | None = None) -> int:
    """Open the DB, run pending migrations, return the resulting schema version."""
    conn = connect(path)
    try:
        return migrate(conn)
    finally:
        conn.close()
