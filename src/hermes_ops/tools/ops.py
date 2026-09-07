"""``ops_status`` — the first call to run when validating live credentials.

Every probe is wrapped so one failure never masks the others; anything that goes
wrong is appended to ``degraded`` and ``ok`` is set False.
"""

from __future__ import annotations

from fastmcp import FastMCP

from hermes_ops import config
from hermes_ops.adapters.calendar import CalendarAdapter
from hermes_ops.adapters.gmail import HERMES_LABELS, GmailAdapter
from hermes_ops.auth import oauth
from hermes_ops.policy.loader import load_policy
from hermes_ops.schemas import OpsStatus
from hermes_ops.store import db


def _resolve_default_calendar() -> str:
    cals = load_policy().get("calendars", {})
    alias = cals.get("default", "hermes")
    return cals.get(alias, alias)


def register(mcp: FastMCP) -> None:
    @mcp.tool(tags={"cron", "interactive"})
    def ops_status() -> OpsStatus:
        """Health snapshot of the hermes-ops server: OAuth token validity and
        scopes, resolved Gmail address, visible calendars, which Hermes/* labels
        exist, SQLite path + schema version, message counts per category, and the
        last audit timestamp. Run this first when checking that live credentials
        work. Any failed probe appears in `degraded`."""
        config.load_env_file()
        degraded: list[str] = []

        creds = None
        token_valid = False
        token_expiry: str | None = None
        scopes: list[str] = []
        try:
            creds = oauth.load_credentials()
            token_valid = bool(creds.valid)
            token_expiry = creds.expiry.isoformat() if creds.expiry else None
            scopes = list(creds.scopes or [])
        except Exception as exc:  # noqa: BLE001
            degraded.append(f"auth: {exc}")

        gmail_address: str | None = None
        labels_present: dict[str, bool] = {}
        calendar_ok = False
        calendar_target: str | None = None
        calendar_name: str | None = None
        if creds is not None:
            try:
                gmail = GmailAdapter(creds)
                gmail_address = gmail.profile().get("emailAddress")
                names = {label["name"] for label in gmail.list_labels()}
                labels_present = {name: name in names for name in HERMES_LABELS}
            except Exception as exc:  # noqa: BLE001
                degraded.append(f"gmail: {exc}")
            try:
                calendar_target = _resolve_default_calendar()
                resp = CalendarAdapter(creds).probe(calendar_target)
                calendar_ok = True
                calendar_name = resp.get("summary")
            except Exception as exc:  # noqa: BLE001
                degraded.append(f"calendar: {exc}")

        schema_version: int | None = None
        counts: dict[str, int] = {}
        last_audit_at: str | None = None
        try:
            conn = db.connect()
            try:
                schema_version = db.migrate(conn)
                for row in conn.execute(
                    "SELECT COALESCE(category, 'unclassified') AS c, COUNT(*) AS n "
                    "FROM messages GROUP BY c"
                ):
                    counts[row["c"]] = row["n"]
                row = conn.execute("SELECT MAX(at) AS a FROM audit").fetchone()
                last_audit_at = row["a"] if row else None
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            degraded.append(f"db: {exc}")

        return OpsStatus(
            ok=token_valid and not degraded,
            token_valid=token_valid,
            token_expiry=token_expiry,
            scopes=scopes,
            gmail_address=gmail_address,
            calendar_ok=calendar_ok,
            calendar_target=calendar_target,
            calendar_name=calendar_name,
            labels_present=labels_present,
            db_path=str(config.db_path()),
            db_schema_version=schema_version,
            counts_by_category=counts,
            last_audit_at=last_audit_at,
            degraded=degraded,
        )
