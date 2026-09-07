"""Phase 1 read tools: ``mail_find`` and ``mail_read``."""

from __future__ import annotations

from fastmcp import FastMCP

from hermes_ops import mime
from hermes_ops.query import FindParams, compile_query, relaxation_plan
from hermes_ops.schemas import (
    Attachment,
    FindResult,
    MessagePart,
    ThreadContent,
    ThreadSummary,
)
from hermes_ops.store import repo
from hermes_ops.timeutil import from_epoch_ms
from hermes_ops.tools import _common


def _summarize(gmail, stub: dict, id_to_name: dict[str, str], conn) -> ThreadSummary:
    msg = gmail.get_message(stub["id"], fmt="metadata")
    payload = msg.get("payload", {})
    hdr = mime.headers_map(payload)
    labels = [id_to_name.get(x, x) for x in msg.get("labelIds", [])]
    row = repo.get_message(conn, stub["id"])
    return ThreadSummary(
        thread_id=msg.get("threadId", ""),
        message_id=msg["id"],
        subject=hdr.get("subject", "(no subject)"),
        sender=hdr.get("from", ""),
        received_at=from_epoch_ms(msg["internalDate"]).isoformat(timespec="seconds")
        if msg.get("internalDate")
        else "",
        message_count=1,
        labels=labels,
        attachments=[a["filename"] for a in mime.list_attachments(payload)],
        snippet=(msg.get("snippet", "") or "")[:200],
        category=row["category"] if row and row["category"] else None,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(tags={"interactive"})
    def mail_find(
        sender: str | None = None,
        subject_terms: list[str] | None = None,
        body_terms: list[str] | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        filename_ext: str | None = None,
        label: str | None = None,
        unread_only: bool = False,
        limit: int = 20,
    ) -> FindResult:
        """Find mail from structured hints. The server compiles Gmail query
        syntax — do not pass raw Gmail queries here.

        On zero hits the search is retried down a relaxation ladder (drop
        body_terms -> widen the date window -> subject terms as full text); each
        step taken is recorded in `degraded`. NEVER tell the user "no such email"
        without reporting what `degraded` contains and the final `query_used`.

        Dates: "7d"/"36h" for relative, "2026-09-01" for absolute.
        """
        _common.env()
        params = FindParams(
            sender=sender,
            subject_terms=subject_terms or [],
            body_terms=body_terms or [],
            after=after,
            before=before,
            has_attachment=has_attachment,
            filename_ext=filename_ext,
            label=label,
            unread_only=unread_only,
            limit=max(1, min(limit, 100)),
        )
        gmail = _common.gmail()
        _, id_to_name = gmail.label_maps()
        conn = _common.conn()
        try:
            degraded: list[str] = []
            plan = relaxation_plan(params)
            stubs: list[dict] = []
            estimate = 0
            used = plan[0][0]
            for query, note in plan:
                if note:
                    degraded.append(note)
                used = query
                stubs, estimate = gmail.search(query, params.limit)
                if stubs:
                    break

            hits = [_summarize(gmail, s, id_to_name, conn) for s in stubs]
            return FindResult(
                hits=hits,
                query_used=used or compile_query(params),
                total_estimate=estimate,
                truncated=len(hits) >= params.limit and estimate > len(hits),
                degraded=degraded,
            )
        finally:
            conn.close()

    @mcp.tool(tags={"interactive"})
    def mail_read(
        id: str,
        thread: bool = True,
        max_chars: int = 4000,
    ) -> ThreadContent:
        """Return normalized plain-text content for a message id or thread id.
        HTML is converted to text and quoted reply chains are collapsed to
        `[quoted: N lines]`. Attachment metadata only — bytes never returned."""
        _common.env()
        gmail = _common.gmail()

        if thread:
            probe = gmail.get_message(id, fmt="metadata")
            thread_id = probe.get("threadId", id)
            data = gmail.get_thread(thread_id, fmt="full")
            raw_messages = data.get("messages", [])
        else:
            data = gmail.get_message(id, fmt="full")
            thread_id = data.get("threadId", id)
            raw_messages = [data]

        parts: list[MessagePart] = []
        attachments: list[Attachment] = []
        subject = ""
        for m in raw_messages:
            payload = m.get("payload", {})
            hdr = mime.headers_map(payload)
            subject = subject or hdr.get("subject", "(no subject)")
            body, truncated = mime.extract_body(payload, max_chars=max_chars)
            parts.append(
                MessagePart(
                    message_id=m["id"],
                    sender=hdr.get("from", ""),
                    to=[x.strip() for x in hdr.get("to", "").split(",") if x.strip()],
                    received_at=from_epoch_ms(m["internalDate"]).isoformat(
                        timespec="seconds"
                    )
                    if m.get("internalDate")
                    else "",
                    body=body,
                    truncated=truncated,
                )
            )
            for a in mime.list_attachments(payload):
                attachments.append(Attachment(**a))

        return ThreadContent(
            thread_id=thread_id,
            subject=subject,
            messages=parts,
            attachments=attachments,
        )
