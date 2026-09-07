"""Phase 2 classification tools: ``mail_classify``, ``mail_autotriage``,
``mail_rollup``.

Each category maps to exactly one Hermes/* label and one inbox side effect
(design §5):

    action     Hermes/Action      keep unread, keep in inbox
    waiting    Hermes/Waiting     mark read
    fyi        Hermes/FYI         mark read
    reference  Hermes/Reference   mark read, archive
    noise      Hermes/Noise       mark read, archive
"""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP

from hermes_ops import mime
from hermes_ops.policy import rules
from hermes_ops.schemas import (
    AutotriageItem,
    AutotriageResult,
    ClassifyItem,
    ClassifyResult,
    Rollup,
    RollupCategory,
)
from hermes_ops.store import repo
from hermes_ops.timeutil import from_epoch_ms, now, now_iso, parse_window
from hermes_ops.tools import _common

CATEGORY_LABEL = {
    "action": "Hermes/Action",
    "waiting": "Hermes/Waiting",
    "fyi": "Hermes/FYI",
    "reference": "Hermes/Reference",
    "noise": "Hermes/Noise",
}
_MARK_READ = {"waiting", "fyi", "reference", "noise"}
_ARCHIVE = {"reference", "noise"}
_ALL_HERMES_LABEL_IDS_CACHE: dict[str, str] = {}


def _apply_one(
    gmail,
    name_to_id: dict[str, str],
    message_id: str,
    category: str,
) -> None:
    target_label = CATEGORY_LABEL[category]
    add = [name_to_id[target_label]]
    remove: list[str] = [
        lid
        for name, lid in name_to_id.items()
        if name in CATEGORY_LABEL.values() and name != target_label
    ]
    if category in _MARK_READ:
        remove.append("UNREAD")
    if category in _ARCHIVE:
        remove.append("INBOX")
    gmail.modify(message_id, add=add, remove=remove)


def _persist(
    conn,
    gmail,
    message_id: str,
    category: str,
    reason: str,
    classifier: str,
) -> None:
    meta = gmail.get_message(message_id, fmt="metadata")
    hdr = mime.headers_map(meta.get("payload", {}))
    received = (
        from_epoch_ms(meta["internalDate"]).isoformat(timespec="seconds")
        if meta.get("internalDate")
        else None
    )
    repo.upsert_message(
        conn,
        {
            "message_id": message_id,
            "thread_id": meta.get("threadId", ""),
            "sender": hdr.get("from", ""),
            "subject": hdr.get("subject", ""),
            "received_at": received,
            "category": category,
            "reason": reason,
            "classifier": classifier,
            "classified_at": now_iso(),
            "label_applied": 1,
            "last_inbound_at": received,
        },
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(tags={"cron", "interactive"})
    def mail_classify(
        ids: list[str],
        category: Literal["action", "waiting", "fyi", "reference", "noise"],
        reason: str,
        classifier: str = "agent",
        dry_run: bool = False,
    ) -> ClassifyResult:
        """Durably classify 1..50 messages: apply the Hermes/* label, remove any
        other Hermes/* label, apply the read/archive side effect, and record the
        row in the local store — per message, so a partial failure reports
        per-id status instead of aborting the batch."""
        _common.env()
        ids = ids[:50]
        gmail = _common.gmail()
        name_to_id, _ = gmail.label_maps()
        conn = _common.conn()
        results: list[ClassifyItem] = []
        try:
            for mid in ids:
                if dry_run:
                    results.append(ClassifyItem(message_id=mid, ok=True, category=category))
                    continue
                try:
                    _apply_one(gmail, name_to_id, mid, category)
                    _persist(conn, gmail, mid, category, reason, classifier)
                    results.append(ClassifyItem(message_id=mid, ok=True, category=category))
                except Exception as exc:  # noqa: BLE001
                    results.append(ClassifyItem(message_id=mid, ok=False, error=str(exc)))
            repo.audit(
                conn,
                "mail_classify",
                {"ids": ids, "category": category, "dry_run": dry_run},
                "ok" if all(r.ok for r in results) else "error",
                f"{sum(r.ok for r in results)}/{len(results)} applied",
            )
            return ClassifyResult(results=results, dry_run=dry_run)
        finally:
            conn.close()

    @mcp.tool(tags={"cron", "interactive"})
    def mail_autotriage(
        since: str = "3d",
        apply: bool = True,
        min_confidence: float = 0.9,
        categories: list[str] | None = None,
    ) -> AutotriageResult:
        """Run the deterministic rules-v3 classifier over unclassified messages
        in the window. Auto-apply only verdicts at/above `min_confidence` whose
        category is in `categories` (default noise/reference/fyi). `action` and
        `waiting` are never auto-applied. This is what the no-LLM cron calls."""
        _common.env()
        allowed = set(categories or ["noise", "reference", "fyi"]) - {"action", "waiting"}
        policy = _common.policy()
        gmail = _common.gmail()
        name_to_id, _ = gmail.label_maps()
        conn = _common.conn()
        try:
            after = parse_window(since, default_days=3)
            query = f"after:{after.strftime('%Y/%m/%d')} -label:Hermes"
            stubs, _ = gmail.search(query, limit=100)

            items: list[AutotriageItem] = []
            applied = 0
            remaining = 0
            for stub in stubs:
                if repo.get_message(conn, stub["id"]) and repo.get_message(
                    conn, stub["id"]
                )["category"]:
                    continue
                meta = gmail.get_message(stub["id"], fmt="metadata")
                hdr = mime.headers_map(meta.get("payload", {}))
                verdict = rules.classify(hdr, policy)
                do_apply = (
                    apply
                    and verdict.category in allowed
                    and verdict.confidence >= min_confidence
                )
                if do_apply:
                    _apply_one(gmail, name_to_id, stub["id"], verdict.category)
                    _persist(
                        conn,
                        gmail,
                        stub["id"],
                        verdict.category,
                        verdict.rule,
                        rules.CLASSIFIER,
                    )
                    applied += 1
                else:
                    remaining += 1
                items.append(
                    AutotriageItem(
                        message_id=stub["id"],
                        category=verdict.category,
                        confidence=round(verdict.confidence, 2),
                        rule=verdict.rule,
                        applied=do_apply,
                    )
                )
            repo.audit(
                conn,
                "mail_autotriage",
                {"since": since, "apply": apply, "min_confidence": min_confidence},
                "ok",
                f"{applied} applied, {remaining} left for the agent",
            )
            return AutotriageResult(
                items=items,
                unclassified_remaining=remaining,
                applied_count=applied,
                dry_run=not apply,
            )
        finally:
            conn.close()

    @mcp.tool(tags={"cron", "interactive"})
    def mail_rollup(since: str = "7d", include_unclassified: bool = True) -> Rollup:
        """The "what needs me?" summary the scheduled jobs produce. Counts per
        category, the action queue, waiting items older than 5 days, classified
        threads that got a new inbound message since they were classified, and
        the count of still-unclassified mail in the window."""
        _common.env()
        gmail = _common.gmail()
        conn = _common.conn()
        try:
            after = parse_window(since, default_days=7)
            after_iso = after.isoformat(timespec="seconds")

            counts: dict[str, int] = {}
            for row in conn.execute(
                "SELECT category, COUNT(*) n FROM messages "
                "WHERE received_at >= ? GROUP BY category",
                (after_iso,),
            ):
                counts[row["category"] or "unclassified"] = row["n"]

            def _rows(where: str, params: tuple) -> list[RollupCategory]:
                out = []
                for r in conn.execute(
                    f"SELECT subject, sender, received_at, message_id FROM messages "
                    f"WHERE {where} ORDER BY received_at DESC LIMIT 50",
                    params,
                ):
                    age = (now() - _parse_iso(r["received_at"])).days if r["received_at"] else 0
                    out.append(
                        RollupCategory(
                            subject=r["subject"] or "(no subject)",
                            sender=r["sender"] or "",
                            age_days=max(age, 0),
                            message_id=r["message_id"],
                        )
                    )
                return out

            from datetime import timedelta

            action = _rows("category = 'action'", ())
            waiting_cutoff = (now() - timedelta(days=5)).isoformat(timespec="seconds")
            waiting_stale = _rows(
                "category = 'waiting' AND received_at < ?", (waiting_cutoff,)
            )
            reactivated = _rows(
                "category IS NOT NULL AND last_inbound_at IS NOT NULL "
                "AND last_inbound_at > classified_at",
                (),
            )

            unclassified = 0
            if include_unclassified:
                stubs, est = gmail.search(
                    f"after:{after.strftime('%Y/%m/%d')} -label:Hermes in:inbox", limit=1
                )
                unclassified = est

            repo.audit(conn, "mail_rollup", {"since": since}, "ok", "")
            return Rollup(
                counts=counts,
                action=action,
                waiting_stale=waiting_stale,
                reactivated=reactivated,
                unclassified_in_window=unclassified,
            )
        finally:
            conn.close()


def _parse_iso(value: str):
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return now()
    return dt if dt.tzinfo else dt.replace(tzinfo=now().tzinfo)
