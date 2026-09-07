"""Time helpers. All stored/returned timestamps are ISO 8601 in the policy
timezone (America/Bogota by default)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hermes_ops.policy.loader import load_policy

_DEFAULT_TZ = "America/Bogota"


def tz() -> ZoneInfo:
    name = load_policy().get("timezone") or _DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - bad tz in policy shouldn't crash a tool
        return ZoneInfo(_DEFAULT_TZ)


def now() -> datetime:
    return datetime.now(tz())


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def from_epoch_ms(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).astimezone(tz())


def to_local_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return dt.astimezone(tz()).isoformat(timespec="seconds")


def parse_window(spec: str | None, *, default_days: int = 7) -> datetime:
    """Resolve a lower-bound datetime from '7d', '36h', an ISO date, or None."""
    if not spec:
        return now() - timedelta(days=default_days)
    s = spec.strip().lower()
    if s.endswith("d") and s[:-1].isdigit():
        return now() - timedelta(days=int(s[:-1]))
    if s.endswith("h") and s[:-1].isdigit():
        return now() - timedelta(hours=int(s[:-1]))
    try:
        dt = datetime.fromisoformat(spec)
        return dt if dt.tzinfo else dt.replace(tzinfo=tz())
    except ValueError:
        return now() - timedelta(days=default_days)
