"""Thin data-access helpers over the SQLite store. No Google, no policy."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from hermes_ops.timeutil import now_iso


def args_digest(args: dict[str, Any]) -> str:
    blob = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def audit(
    conn: sqlite3.Connection,
    tool: str,
    args: dict[str, Any],
    outcome: str,
    detail: str = "",
) -> None:
    conn.execute(
        "INSERT INTO audit(at, tool, args_digest, outcome, detail) VALUES (?, ?, ?, ?, ?)",
        (now_iso(), tool, args_digest(args), outcome, detail[:500]),
    )
    conn.commit()


# --------------------------------------------------------------------- messages
def upsert_message(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = [
        "message_id",
        "thread_id",
        "sender",
        "subject",
        "received_at",
        "category",
        "reason",
        "classifier",
        "classified_at",
        "label_applied",
        "last_inbound_at",
    ]
    values = [row.get(c) for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "message_id")
    conn.execute(
        f"INSERT INTO messages ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(message_id) DO UPDATE SET {updates}",
        values,
    )
    conn.commit()


def get_message(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM messages WHERE message_id = ?", (message_id,)
    ).fetchone()


def classified_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT message_id FROM messages WHERE message_id IN ({marks}) "
        f"AND category IS NOT NULL",
        ids,
    )
    return {r[0] for r in rows}


# ----------------------------------------------------------------------- events
def get_event_by_dedupe(conn: sqlite3.Connection, dedupe_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM events WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()


def insert_event(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO events (event_id, calendar_id, dedupe_key, title, starts_at, "
        "ends_at, created_at, verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(event_id) DO UPDATE SET title=excluded.title, "
        "starts_at=excluded.starts_at, ends_at=excluded.ends_at, "
        "verified=excluded.verified",
        (
            row["event_id"],
            row["calendar_id"],
            row["dedupe_key"],
            row.get("title"),
            row.get("starts_at"),
            row.get("ends_at"),
            row.get("created_at") or now_iso(),
            1 if row.get("verified") else 0,
        ),
    )
    conn.commit()


def delete_event(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute("DELETE FROM links WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
    conn.commit()


def link(
    conn: sqlite3.Connection, message_id: str, event_id: str, method: str
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO links (message_id, event_id, method, created_at) "
        "VALUES (?, ?, ?, ?)",
        (message_id, event_id, method, now_iso()),
    )
    conn.commit()


def links_for(
    conn: sqlite3.Connection,
    *,
    message_id: str | None = None,
    event_id: str | None = None,
) -> list[dict[str, Any]]:
    if message_id:
        rows = conn.execute("SELECT * FROM links WHERE message_id = ?", (message_id,))
    elif event_id:
        rows = conn.execute("SELECT * FROM links WHERE event_id = ?", (event_id,))
    else:
        return []
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------- drafts
def get_draft(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM drafts WHERE message_id = ?", (message_id,)
    ).fetchone()


def set_draft(conn: sqlite3.Connection, message_id: str, draft_id: str) -> None:
    conn.execute(
        "INSERT INTO drafts (message_id, draft_id, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(message_id) DO UPDATE SET draft_id=excluded.draft_id, "
        "updated_at=excluded.updated_at",
        (message_id, draft_id, now_iso()),
    )
    conn.commit()
