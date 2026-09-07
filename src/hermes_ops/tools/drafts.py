"""Phase 4: ``draft_reply`` — an in-thread Gmail draft. Never sends (there is no
send tool and the OAuth scope cannot send). Idempotent: one draft per source
message, updated in place on a repeat call."""

from __future__ import annotations

from fastmcp import FastMCP

from hermes_ops import mime
from hermes_ops.schemas import DraftResult
from hermes_ops.store import repo
from hermes_ops.tools import _common


def register(mcp: FastMCP) -> None:
    @mcp.tool(tags={"interactive"})
    def draft_reply(
        message_id: str,
        body: str,
        cc: list[str] | None = None,
        dry_run: bool = False,
    ) -> DraftResult:
        """Create (or update) a Gmail draft replying in-thread to `message_id`,
        with correct In-Reply-To / References headers. Calling twice for the same
        message updates the existing draft rather than making a second one."""
        _common.env()
        gmail = _common.gmail()
        src = gmail.get_message(message_id, fmt="metadata")
        hdr = mime.headers_map(src.get("payload", {}))
        thread_id = src.get("threadId", "")

        to_addr = hdr.get("reply-to") or hdr.get("from") or ""
        subject = hdr.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        parent_mid = hdr.get("message-id", "")
        refs = " ".join(x for x in [hdr.get("references", ""), parent_mid] if x).strip()
        headers = {"In-Reply-To": parent_mid, "References": refs}

        if dry_run:
            return DraftResult(draft_id="", gmail_link="", updated=False, to=[to_addr])

        conn = _common.conn()
        try:
            existing = repo.get_draft(conn, message_id)
            if existing:
                res = gmail.update_draft(
                    existing["draft_id"], thread_id, [to_addr], subject, body, cc, headers
                )
                updated = True
            else:
                res = gmail.create_draft(
                    thread_id, [to_addr], subject, body, cc, headers
                )
                updated = False
            draft_id = res["id"]
            repo.set_draft(conn, message_id, draft_id)
            repo.audit(
                conn, "draft_reply", {"message_id": message_id, "updated": updated}, "ok", draft_id
            )
            return DraftResult(
                draft_id=draft_id,
                gmail_link=f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}",
                updated=updated,
                to=[to_addr],
            )
        finally:
            conn.close()
