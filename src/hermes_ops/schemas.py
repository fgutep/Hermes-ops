"""Pydantic models — the tool contract. FastMCP turns these into the JSON
schemas the agent sees, so field names and docstrings here *are* the API.

Phase 0 uses only ``OpsStatus``. The rest are the design §6 / §13 shapes,
included now so the contract is visible in one place; later phases fill in the
tools that return them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["action", "waiting", "fyi", "reference", "noise"]


# --------------------------------------------------------------------------- ops
class OpsStatus(BaseModel):
    """Health snapshot. Each check is isolated: a Gmail failure still lets the
    calendar and DB results through. ``degraded`` lists whatever failed."""

    ok: bool
    token_valid: bool
    token_expiry: str | None = None
    scopes: list[str] = []
    gmail_address: str | None = None
    calendar_ok: bool = False
    calendar_target: str | None = None  # resolved id of the 'default' alias
    calendar_name: str | None = None  # its title, if the probe succeeded
    labels_present: dict[str, bool] = {}
    db_path: str
    db_schema_version: int | None = None
    counts_by_category: dict[str, int] = {}
    last_audit_at: str | None = None
    degraded: list[str] = []


# ------------------------------------------------------------------- mail: read
class ThreadSummary(BaseModel):
    thread_id: str
    message_id: str
    subject: str
    sender: str
    received_at: str
    message_count: int
    labels: list[str] = []
    attachments: list[str] = []
    snippet: str = ""
    category: Category | None = None


class FindResult(BaseModel):
    hits: list[ThreadSummary] = []
    query_used: str
    total_estimate: int = 0
    truncated: bool = False
    degraded: list[str] = Field(
        default_factory=list,
        description="Constraints relaxed, in order. NEVER report 'no such email' "
        "to the user without surfacing this.",
    )


class MessagePart(BaseModel):
    message_id: str
    sender: str
    to: list[str] = []
    received_at: str
    body: str
    truncated: bool = False


class Attachment(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int


class ThreadContent(BaseModel):
    thread_id: str
    subject: str
    messages: list[MessagePart] = []
    attachments: list[Attachment] = []


# ------------------------------------------------------------- mail: classify
class ClassifyItem(BaseModel):
    message_id: str
    ok: bool
    category: Category | None = None
    error: str | None = None


class ClassifyResult(BaseModel):
    results: list[ClassifyItem] = []
    dry_run: bool = False


class AutotriageItem(BaseModel):
    message_id: str
    category: Category
    confidence: float
    rule: str
    applied: bool


class AutotriageResult(BaseModel):
    items: list[AutotriageItem] = []
    unclassified_remaining: int = 0
    applied_count: int = 0
    dry_run: bool = False


class RollupCategory(BaseModel):
    subject: str
    sender: str
    age_days: int
    message_id: str


class Rollup(BaseModel):
    counts: dict[str, int] = {}
    action: list[RollupCategory] = []
    waiting_stale: list[RollupCategory] = []
    reactivated: list[RollupCategory] = Field(
        default_factory=list,
        description="Classified threads with a new inbound message since classified_at.",
    )
    unclassified_in_window: int = 0


# ----------------------------------------------------------------- calendar
class EventProposal(BaseModel):
    source_message_id: str
    method: Literal["ics", "extracted"]
    title: str
    starts_at: str
    ends_at: str
    all_day: bool = False
    location: str | None = None
    dedupe_key: str
    confidence: float
    ambiguities: list[str] = []
    duplicate_of: str | None = None
    conflicts: list[str] = []


class EventResult(BaseModel):
    event_id: str | None = None
    html_link: str | None = None
    verified: bool = False
    calendar_id: str | None = None
    refused: str | None = None
    dry_run: bool = False


class Links(BaseModel):
    message_id: str | None = None
    event_id: str | None = None
    links: list[dict] = []


# ------------------------------------------------------------------- drafts
class DraftResult(BaseModel):
    draft_id: str
    gmail_link: str
    updated: bool = False  # True = an existing draft for this message was replaced
    to: list[str] = []
