"""Turn a message into an :class:`EventProposal` — ICS first, plain-text
extraction second. Writes nothing. The email body is treated strictly as data:
relative dates resolve against the message's own received date, never ``now``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta

from dateutil import parser as dateparser

from hermes_ops import mime
from hermes_ops.schemas import EventProposal
from hermes_ops.timeutil import tz


class NoEventFound(ValueError):
    pass


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip().lower()


def dedupe_key(source_message_id: str, title: str, start_iso: str) -> str:
    raw = f"{source_message_id}|{_norm_title(title)}|{start_iso}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------------------- ICS
def _from_ics(blob: bytes, source_message_id: str) -> EventProposal:
    from icalendar import Calendar

    cal = Calendar.from_ical(blob)
    vevent = next((c for c in cal.walk() if c.name == "VEVENT"), None)
    if vevent is None:
        raise NoEventFound("ICS contained no VEVENT")

    dtstart = vevent.get("dtstart").dt
    dtend_prop = vevent.get("dtend")
    all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)

    if isinstance(dtstart, datetime):
        start = dtstart.astimezone(tz()) if dtstart.tzinfo else dtstart.replace(tzinfo=tz())
    else:
        start = datetime.combine(dtstart, time(0, 0), tzinfo=tz())

    if dtend_prop is not None:
        dtend = dtend_prop.dt
        if isinstance(dtend, datetime):
            end = dtend.astimezone(tz()) if dtend.tzinfo else dtend.replace(tzinfo=tz())
        else:
            end = datetime.combine(dtend, time(0, 0), tzinfo=tz())
    elif vevent.get("duration") is not None:
        end = start + vevent["duration"].dt
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))

    uid = str(vevent.get("uid") or "").strip()
    title = str(vevent.get("summary") or "(untitled event)").strip()
    location = str(vevent.get("location") or "").strip() or None
    start_iso = start.isoformat(timespec="seconds")

    return EventProposal(
        source_message_id=source_message_id,
        method="ics",
        title=title,
        starts_at=start_iso,
        ends_at=end.isoformat(timespec="seconds"),
        all_day=all_day,
        location=location,
        dedupe_key=f"ics:{uid}" if uid else dedupe_key(source_message_id, title, start_iso),
        confidence=1.0,
        ambiguities=[] if uid else ["ICS had no UID; using a hashed dedupe key"],
    )


# ------------------------------------------------------------- text extraction
_DATE_HINT = re.compile(
    r"(\d{1,2}[:h]\d{2}\s*(?:am|pm)?)|"
    r"(\b\d{1,2}\s+(?:de\s+)?(?:ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|"
    r"jan|apr|aug|dec)\w*)|"
    r"(\b(?:mon|tue|wed|thu|fri|sat|sun|lun|mar|mié|jue|vie|sáb|dom)\w*\b)|"
    r"(\btomorrow\b|\bmañana\b|\bnext\s+\w+|\bpróximo\s+\w+)",
    re.IGNORECASE,
)


def _from_text(subject: str, body: str, received: datetime, source_message_id: str) -> EventProposal:
    text = f"{subject}\n{body}"
    if not _DATE_HINT.search(text):
        raise NoEventFound("no date/time expression found in subject or body")

    default = received.replace(hour=9, minute=0, second=0, microsecond=0)
    try:
        start = dateparser.parse(text, fuzzy=True, default=default)
    except (ValueError, OverflowError) as exc:
        raise NoEventFound(f"could not parse a date: {exc}") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz())

    ambiguities = ["date/time extracted from free text — verify before creating"]
    if str(received.year) not in text and start.year == received.year:
        ambiguities.append("year not stated; assumed the message's year")
    if not re.search(r"\d{1,2}[:h]\d{2}", text):
        ambiguities.append("no explicit time; assumed 09:00")
    ambiguities.append(f"timezone assumed {tz().key}")

    end = start + timedelta(hours=1)
    title = subject.strip() or "(untitled event)"
    start_iso = start.isoformat(timespec="seconds")
    return EventProposal(
        source_message_id=source_message_id,
        method="extracted",
        title=title,
        starts_at=start_iso,
        ends_at=end.isoformat(timespec="seconds"),
        all_day=False,
        location=None,
        dedupe_key=dedupe_key(source_message_id, title, start_iso),
        confidence=0.45,
        ambiguities=ambiguities,
    )


def propose_from_message(gmail, message_id: str) -> EventProposal:
    msg = gmail.get_message(message_id, fmt="full")
    payload = msg.get("payload", {})
    hdr = mime.headers_map(payload)
    received = (
        datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=tz())
        if msg.get("internalDate")
        else datetime.now(tz())
    )

    ics = mime.ics_attachment(payload)
    if ics:
        blob = gmail.get_attachment_bytes(message_id, ics["attachment_id"])
        return _from_ics(blob, message_id)

    body, _ = mime.extract_body(payload, max_chars=8000)
    return _from_text(hdr.get("subject", ""), body, received, message_id)
