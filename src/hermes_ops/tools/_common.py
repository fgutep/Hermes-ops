"""Shared plumbing for tool modules: credential/adapter access, DB connections,
policy, calendar-alias resolution."""

from __future__ import annotations

from hermes_ops import config
from hermes_ops.adapters.calendar import CalendarAdapter
from hermes_ops.adapters.gmail import GmailAdapter
from hermes_ops.auth import oauth
from hermes_ops.policy.loader import load_policy
from hermes_ops.store import db


def env() -> None:
    config.load_env_file()


def gmail() -> GmailAdapter:
    return GmailAdapter(oauth.load_credentials())


def calendar() -> CalendarAdapter:
    return CalendarAdapter(oauth.load_credentials())


def conn():
    return db.connect()


def policy() -> dict:
    return load_policy()


def resolve_calendar(alias_or_id: str | None) -> str:
    """Map a policy alias ('hermes', 'personal', 'default') to a calendar id.
    An unknown value is returned unchanged (assumed to already be an id)."""
    cals = load_policy().get("calendars", {})
    if not alias_or_id:
        alias_or_id = cals.get("default", "hermes")
    seen: set[str] = set()
    current = alias_or_id
    while current in cals and current not in seen:
        seen.add(current)
        current = cals[current]
    return current


def conflict_calendar_ids() -> list[str]:
    cals = load_policy().get("calendars", {})
    aliases = load_policy().get("conflict_calendars", ["hermes"])
    out: list[str] = []
    for alias in aliases:
        cid = resolve_calendar(alias)
        if cid and cid not in out:
            out.append(cid)
    return out
