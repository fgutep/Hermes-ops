"""Gmail message payload helpers: headers, body extraction, attachment listing.

Attachment *bytes* never come through here into a tool result — only metadata.
The ICS ingester fetches bytes separately via the adapter.
"""

from __future__ import annotations

import base64
import html
import re
from typing import Any, Iterator

_QUOTE_MARKERS = re.compile(
    r"^\s*(>|On .+ wrote:|El .+ escribi[oó]:|-{3,} ?Original Message ?-{3,}|"
    r"_{10,}|From: .+|De: .+)\s*$"
)


def headers_map(payload: dict[str, Any]) -> dict[str, str]:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in payload.get("headers", [])
    }


def header(payload: dict[str, Any], name: str) -> str:
    return headers_map(payload).get(name.lower(), "")


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _charset(part: dict[str, Any]) -> str | None:
    ctype = headers_map(part).get("content-type", "")
    m = re.search(r'charset="?([\w-]+)"?', ctype, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _decode(raw: bytes, part: dict[str, Any]) -> str:
    for enc in (_charset(part), "utf-8", "cp1252", "latin-1"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def iter_parts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield payload
    for part in payload.get("parts", []) or []:
        yield from iter_parts(part)


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", "", raw)
    return html.unescape(raw)


def collapse_quotes(text: str) -> str:
    out: list[str] = []
    run = 0

    def flush() -> None:
        nonlocal run
        if run:
            out.append(f"[quoted: {run} lines]")
            run = 0

    for line in text.splitlines():
        if _QUOTE_MARKERS.match(line):
            run += 1
        else:
            flush()
            out.append(line)
    flush()
    collapsed = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", collapsed).strip()


def extract_body(payload: dict[str, Any], max_chars: int = 4000) -> tuple[str, bool]:
    """Prefer text/plain; fall back to text/html converted to text. Quoted reply
    chains are collapsed. Returns (text, truncated)."""
    plain: str | None = None
    html_body: str | None = None
    for part in iter_parts(payload):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if not data:
            continue
        try:
            decoded = _decode(_b64url(data), part)
        except Exception:  # noqa: BLE001
            continue
        if mime == "text/plain" and plain is None:
            plain = decoded
        elif mime == "text/html" and html_body is None:
            html_body = decoded

    body = plain if plain is not None else (_html_to_text(html_body) if html_body else "")
    body = collapse_quotes(body)
    if len(body) > max_chars:
        return body[:max_chars].rstrip() + "\n[...truncated]", True
    return body, False


def list_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for part in iter_parts(payload):
        body = part.get("body", {})
        filename = part.get("filename") or ""
        if filename and body.get("attachmentId"):
            found.append(
                {
                    "attachment_id": body["attachmentId"],
                    "filename": filename,
                    "mime_type": part.get("mimeType", "application/octet-stream"),
                    "size_bytes": int(body.get("size", 0)),
                }
            )
    return found


def ics_attachment(payload: dict[str, Any]) -> dict[str, Any] | None:
    for att in list_attachments(payload):
        if att["filename"].lower().endswith(".ics") or att["mime_type"] in (
            "text/calendar",
            "application/ics",
        ):
            return att
    return None
