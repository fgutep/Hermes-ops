"""Phase 3 calendar tools: ``event_propose``, ``event_create``, ``event_update``,
``event_delete``, ``cal_query``, ``provenance``.

``event_propose`` never writes. ``event_create`` refuses on a duplicate dedupe
key or a hard time overlap unless ``force=True``, and always reads the event
back from Google before persisting — a 200 on insert is not proof the calendar
holds what you intended.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastmcp import FastMCP

from hermes_ops import ingest
from hermes_ops.schemas import EventProposal, EventResult, Links
from hermes_ops.store import repo
from hermes_ops.timeutil import now, parse_window, to_local_iso, tz
from hermes_ops.tools import _common


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(tz()).isoformat()


def _overlaps(cal, start_iso: str, end_iso: str, exclude_event_id: str | None = None) -> list[str]:
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    hits: list[str] = []
    for cid in _common.conflict_calendar_ids():
        for ev in cal.list_events(cid, _iso_z(start), _iso_z(end), max_results=25):
            if exclude_event_id and ev.get("id") == exclude_event_id:
                continue
            s = ev.get("start", {})
            e = ev.get("end", {})
            label = ev.get("summary", "(busy)")
            when = s.get("dateTime") or s.get("date") or ""
            hits.append(f"{when} {label} [{ev.get('id', '')}]")
    return hits


def _event_body(p: EventProposal) -> dict:
    if p.all_day:
        start_d = datetime.fromisoformat(p.starts_at).date().isoformat()
        end_d = datetime.fromisoformat(p.ends_at).date().isoformat()
        start_block = {"date": start_d}
        end_block = {"date": end_d}
    else:
        start_block = {"dateTime": p.starts_at, "timeZone": tz().key}
        end_block = {"dateTime": p.ends_at, "timeZone": tz().key}
    body = {"summary": p.title, "start": start_block, "end": end_block}
    if p.location:
        body["location"] = p.location
    if p.source_message_id:
        body["description"] = f"[hermes-ops] source gmail message: {p.source_message_id}"
    return body


def register(mcp: FastMCP) -> None:
    @mcp.tool(tags={"interactive"})
    def event_propose(message_id: str) -> EventProposal:
        """Parse a message into an event proposal. Writes nothing. Uses the ICS
        attachment if present (confidence 1.0); otherwise extracts date/time from
        the body, resolving relative dates against the message's received date.
        `duplicate_of` / `conflicts` are filled in from existing state. A second
        explicit `event_create` call is required to actually schedule it."""
        _common.env()
        gmail = _common.gmail()
        proposal = ingest.propose_from_message(gmail, message_id)

        conn = _common.conn()
        try:
            existing = repo.get_event_by_dedupe(conn, proposal.dedupe_key)
            if existing:
                proposal.duplicate_of = existing["event_id"]
        finally:
            conn.close()

        try:
            proposal.conflicts = _overlaps(
                _common.calendar(), proposal.starts_at, proposal.ends_at
            )
        except Exception as exc:  # noqa: BLE001
            proposal.ambiguities.append(f"conflict check failed: {exc}")
        return proposal

    @mcp.tool(tags={"interactive"})
    def event_create(
        proposal: EventProposal,
        calendar: str = "hermes",
        force: bool = False,
        dry_run: bool = False,
    ) -> EventResult:
        """Create the proposed event: dedupe check -> conflict check -> insert ->
        read back from Google -> persist the events row and the email->event
        link. Refuses on a duplicate dedupe key or a hard overlap unless
        `force=True`; refusals are audited."""
        _common.env()
        cal_id = _common.resolve_calendar(calendar)
        conn = _common.conn()
        try:
            dup = repo.get_event_by_dedupe(conn, proposal.dedupe_key)
            if dup and not force:
                repo.audit(conn, "event_create", proposal.model_dump(), "refused", "duplicate")
                return EventResult(refused="duplicate", event_id=dup["event_id"], calendar_id=cal_id)

            cal = _common.calendar()
            conflicts = _overlaps(cal, proposal.starts_at, proposal.ends_at)
            if conflicts and not force:
                repo.audit(conn, "event_create", proposal.model_dump(), "refused", "conflict")
                return EventResult(refused="conflict", calendar_id=cal_id)

            if dry_run:
                return EventResult(calendar_id=cal_id, dry_run=True, verified=False)

            created = cal.insert_event(cal_id, _event_body(proposal))
            event_id = created["id"]
            readback = cal.get_event(cal_id, event_id)
            verified = readback.get("id") == event_id and readback.get("status") != "cancelled"

            repo.insert_event(
                conn,
                {
                    "event_id": event_id,
                    "calendar_id": cal_id,
                    "dedupe_key": proposal.dedupe_key,
                    "title": proposal.title,
                    "starts_at": proposal.starts_at,
                    "ends_at": proposal.ends_at,
                    "verified": verified,
                },
            )
            if proposal.source_message_id:
                repo.link(conn, proposal.source_message_id, event_id, proposal.method)
            repo.audit(conn, "event_create", proposal.model_dump(), "ok", event_id)
            return EventResult(
                event_id=event_id,
                html_link=created.get("htmlLink"),
                verified=verified,
                calendar_id=cal_id,
            )
        finally:
            conn.close()

    @mcp.tool(tags={"interactive"})
    def event_update(
        event_id: str,
        starts_at: str | None = None,
        ends_at: str | None = None,
        title: str | None = None,
        location: str | None = None,
        dry_run: bool = False,
    ) -> EventResult:
        """Patch timing/title/location of an existing event, then re-verify by
        read-back and update the local row. Provenance links are left intact."""
        _common.env()
        conn = _common.conn()
        try:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            cal_id = row["calendar_id"] if row else _common.resolve_calendar("hermes")

            body: dict = {}
            if title is not None:
                body["summary"] = title
            if location is not None:
                body["location"] = location
            if starts_at is not None:
                body["start"] = {"dateTime": starts_at, "timeZone": tz().key}
            if ends_at is not None:
                body["end"] = {"dateTime": ends_at, "timeZone": tz().key}
            if not body:
                return EventResult(event_id=event_id, calendar_id=cal_id, refused="no changes given")
            if dry_run:
                return EventResult(event_id=event_id, calendar_id=cal_id, dry_run=True)

            cal = _common.calendar()
            updated = cal.patch_event(cal_id, event_id, body)
            readback = cal.get_event(cal_id, event_id)
            verified = readback.get("id") == event_id

            if row:
                repo.insert_event(
                    conn,
                    {
                        "event_id": event_id,
                        "calendar_id": cal_id,
                        "dedupe_key": row["dedupe_key"],
                        "title": title if title is not None else row["title"],
                        "starts_at": starts_at if starts_at is not None else row["starts_at"],
                        "ends_at": ends_at if ends_at is not None else row["ends_at"],
                        "verified": verified,
                    },
                )
            repo.audit(conn, "event_update", {"event_id": event_id, **body}, "ok", "")
            return EventResult(
                event_id=event_id,
                html_link=updated.get("htmlLink"),
                verified=verified,
                calendar_id=cal_id,
            )
        finally:
            conn.close()

    @mcp.tool(tags={"interactive"})
    def event_delete(event_id: str, dry_run: bool = False) -> EventResult:
        """Delete a Google event and drop its local `events` row and any
        `links` rows."""
        _common.env()
        conn = _common.conn()
        try:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            cal_id = row["calendar_id"] if row else _common.resolve_calendar("hermes")
            if dry_run:
                return EventResult(event_id=event_id, calendar_id=cal_id, dry_run=True)
            try:
                _common.calendar().delete_event(cal_id, event_id)
            except Exception as exc:  # noqa: BLE001 - already-gone is fine
                if "410" not in str(exc) and "404" not in str(exc):
                    raise
            repo.delete_event(conn, event_id)
            repo.audit(conn, "event_delete", {"event_id": event_id}, "ok", "deleted")
            return EventResult(event_id=event_id, calendar_id=cal_id, verified=True)
        finally:
            conn.close()

    @mcp.tool(tags={"cron", "interactive"})
    def cal_query(
        start: str,
        end: str,
        calendars: list[str] | None = None,
        free_busy_only: bool = False,
    ) -> str:
        """Compact calendar read. One line per event:
        `YYYY-MM-DD|HH:MM-HH:MM|title|location|event_id` (null fields omitted,
        event id last). `start`/`end` accept ISO datetimes or "7d"/"36h".
        `free_busy_only` returns `YYYY-MM-DD|HH:MM-HH:MM|busy` lines instead."""
        _common.env()
        aliases = calendars or ["hermes"]
        cal_ids = [_common.resolve_calendar(a) for a in aliases]
        t0 = parse_window(start, default_days=0)
        t1 = parse_window(end, default_days=-7)
        if t1 <= t0:
            t1 = t0 + timedelta(days=7)
        cal = _common.calendar()
        lines: list[str] = []

        if free_busy_only:
            busy = cal.free_busy(_iso_z(t0), _iso_z(t1), cal_ids)
            for spans in busy.values():
                for span in spans:
                    s = datetime.fromisoformat(span["start"].replace("Z", "+00:00")).astimezone(tz())
                    e = datetime.fromisoformat(span["end"].replace("Z", "+00:00")).astimezone(tz())
                    lines.append(f"{s:%Y-%m-%d}|{s:%H:%M}-{e:%H:%M}|busy")
            return "\n".join(sorted(set(lines)))

        for cid in cal_ids:
            for ev in cal.list_events(cid, _iso_z(t0), _iso_z(t1), max_results=250):
                s = ev.get("start", {})
                e = ev.get("end", {})
                title = (ev.get("summary") or "(no title)").replace("|", "/")
                loc = (ev.get("location") or "").replace("|", "/")
                eid = ev.get("id", "")
                if "date" in s and "dateTime" not in s:
                    row = f"{s['date']}|all-day|{title}"
                else:
                    sd = datetime.fromisoformat(s["dateTime"]).astimezone(tz())
                    ed = datetime.fromisoformat(e["dateTime"]).astimezone(tz())
                    row = f"{sd:%Y-%m-%d}|{sd:%H:%M}-{ed:%H:%M}|{title}"
                if loc:
                    row += f"|{loc}"
                row += f"|{eid}"
                lines.append(row)
        return "\n".join(lines)

    @mcp.tool(tags={"interactive"})
    def provenance(
        message_id: str | None = None,
        event_id: str | None = None,
    ) -> Links:
        """Bidirectional email<->event lookup. "Why is this on my calendar?" and
        "did that invitation ever get scheduled?"."""
        _common.env()
        conn = _common.conn()
        try:
            found = repo.links_for(conn, message_id=message_id, event_id=event_id)
            return Links(message_id=message_id, event_id=event_id, links=found)
        finally:
            conn.close()
