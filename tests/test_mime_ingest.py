"""mime body extraction + ingest (ICS parse, dedupe key) — offline."""

from __future__ import annotations

import base64
from datetime import datetime

import pytest

from hermes_ops import mime
from hermes_ops.ingest import NoEventFound, _from_ics, _from_text, dedupe_key


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_extract_plain_body_and_collapse_quotes():
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("Hello there.\nOn Mon wrote:\n> old\n> stuff")},
    }
    body, truncated = mime.extract_body(payload, max_chars=1000)
    assert "Hello there." in body
    assert "[quoted:" in body
    assert not truncated


def test_extract_prefers_plain_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("PLAINTEXT")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>HTML</p>")}},
        ],
    }
    body, _ = mime.extract_body(payload)
    assert body.strip() == "PLAINTEXT"


def test_truncation_flag():
    payload = {"mimeType": "text/plain", "body": {"data": _b64("x" * 50)}}
    body, truncated = mime.extract_body(payload, max_chars=10)
    assert truncated and body.endswith("[...truncated]")


def test_list_attachments_and_ics_detection():
    payload = {
        "parts": [
            {
                "filename": "invite.ics",
                "mimeType": "text/calendar",
                "body": {"attachmentId": "att1", "size": 321},
            }
        ]
    }
    atts = mime.list_attachments(payload)
    assert atts[0]["filename"] == "invite.ics"
    assert mime.ics_attachment(payload)["attachment_id"] == "att1"


def test_dedupe_key_stable_and_normalized():
    a = dedupe_key("m1", "  Project   Review ", "2026-09-08T09:00:00-05:00")
    b = dedupe_key("m1", "project review", "2026-09-08T09:00:00-05:00")
    assert a == b and a.startswith("sha256:")


ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:abc-123@example.edu
SUMMARY:Project sync
DTSTART:20260908T140000Z
DTEND:20260908T150000Z
LOCATION:Room B-12
END:VEVENT
END:VCALENDAR
"""


def test_from_ics_uses_uid_and_confidence_1():
    p = _from_ics(ICS.encode(), "msg-9")
    assert p.method == "ics"
    assert p.confidence == 1.0
    assert p.dedupe_key == "ics:abc-123@example.edu"
    assert p.title == "Project sync"
    assert p.location == "Room B-12"
    assert not p.all_day


def test_from_text_requires_a_date_hint():
    with pytest.raises(NoEventFound):
        _from_text("Coffee?", "let's grab coffee sometime", datetime(2026, 9, 1), "m2")


def test_from_text_resolves_against_received_date():
    p = _from_text(
        "Reunión de proyecto",
        "Nos vemos el viernes a las 3pm en la oficina",
        datetime(2026, 9, 1, 10, 0),
        "m3",
    )
    assert p.method == "extracted"
    assert p.confidence < 0.6
    assert p.starts_at.startswith("2026-09")
    assert any("timezone assumed" in a for a in p.ambiguities)
