"""Compile ``mail_find`` hints into Gmail search syntax, and produce the
relaxation ladder used when the first query returns nothing (design §6.1).

The agent never writes Gmail syntax; this module owns it end to end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from hermes_ops.timeutil import now


@dataclass
class FindParams:
    sender: str | None = None
    subject_terms: list[str] = field(default_factory=list)
    body_terms: list[str] = field(default_factory=list)
    after: str | None = None
    before: str | None = None
    has_attachment: bool | None = None
    filename_ext: str | None = None
    label: str | None = None
    unread_only: bool = False
    limit: int = 20


def _term(value: str) -> str:
    value = value.strip()
    return f'"{value}"' if re.search(r"\s", value) else value


def _or_group(field_name: str, terms: list[str]) -> str | None:
    clean = [_term(t) for t in terms if t and t.strip()]
    if not clean:
        return None
    if len(clean) == 1:
        return f"{field_name}{clean[0]}" if field_name else clean[0]
    joined = " OR ".join(clean)
    return f"{field_name}({joined})" if field_name else f"({joined})"


def _date_clause(spec: str, kind: str) -> str | None:
    """kind is 'after' or 'before'."""
    s = spec.strip().lower()
    if re.fullmatch(r"\d+d", s):
        return f"newer_than:{s}" if kind == "after" else f"older_than:{s}"
    if re.fullmatch(r"\d+h", s):
        days = max(1, round(int(s[:-1]) / 24))
        return f"newer_than:{days}d" if kind == "after" else f"older_than:{days}d"
    try:
        dt = datetime.fromisoformat(spec)
    except ValueError:
        return None
    stamp = dt.strftime("%Y/%m/%d")
    return f"{kind}:{stamp}"


def compile_query(p: FindParams) -> str:
    parts: list[str] = []
    if p.sender:
        parts.append(f"from:{p.sender.strip()}")
    sub = _or_group("subject:", p.subject_terms)
    if sub:
        parts.append(sub)
    body = _or_group("", p.body_terms)
    if body:
        parts.append(body)
    if p.after:
        clause = _date_clause(p.after, "after")
        if clause:
            parts.append(clause)
    if p.before:
        clause = _date_clause(p.before, "before")
        if clause:
            parts.append(clause)
    if p.filename_ext:
        parts.append(f"filename:{p.filename_ext.lstrip('.')} has:attachment")
    elif p.has_attachment:
        parts.append("has:attachment")
    if p.label:
        parts.append(f'label:"{p.label}"')  # quoted: robust for nested Hermes/* labels
    if p.unread_only:
        parts.append("is:unread")
    return " ".join(parts).strip()


def _widen_window(p: FindParams) -> FindParams:
    if not p.after:
        return replace(p, after="90d")
    s = p.after.strip().lower()
    if re.fullmatch(r"\d+d", s):
        return replace(p, after=f"{int(s[:-1]) * 4}d")
    if re.fullmatch(r"\d+h", s):
        return replace(p, after=f"{max(1, round(int(s[:-1]) / 24)) * 4}d")
    try:
        dt = datetime.fromisoformat(p.after)
        span = max(timedelta(days=1), now() - dt.replace(tzinfo=now().tzinfo))
        return replace(p, after=(now() - span * 4).date().isoformat())
    except ValueError:
        return replace(p, after="90d")


def relaxation_plan(p: FindParams) -> list[tuple[str, str | None]]:
    """Ordered (query, degraded_note). First entry is the unrelaxed query."""
    plan: list[tuple[str, str | None]] = [(compile_query(p), None)]

    if p.body_terms:
        step = replace(p, body_terms=[])
        plan.append((compile_query(step), "dropped body_terms"))
    else:
        step = p

    widened = _widen_window(step)
    if compile_query(widened) != compile_query(step):
        plan.append(
            (compile_query(widened), f"widened date window to after={widened.after}")
        )
        step = widened

    if p.subject_terms:
        fulltext = replace(step, subject_terms=[], body_terms=p.subject_terms)
        plan.append(
            (compile_query(fulltext), "moved subject_terms to full-text search")
        )

    # de-dupe consecutive identical queries
    out: list[tuple[str, str | None]] = []
    for q, note in plan:
        if not out or out[-1][0] != q:
            out.append((q, note))
    return out
