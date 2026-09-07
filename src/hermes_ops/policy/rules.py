"""``rules-v3`` — the deterministic classifier.

Pure function of (message headers, policy). No I/O, no LLM. Order matters:
noise wins first (cheapest to be sure about), then explicit action signals, then
sender importance, then a low-confidence default of ``fyi``.

``mail_autotriage`` only auto-applies results at/above its confidence threshold
and never auto-applies ``action`` / ``waiting`` — those always want a human or
the agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CLASSIFIER = "rules-v3"


@dataclass
class Verdict:
    category: str          # action | waiting | fyi | reference | noise
    confidence: float      # 0.0 - 1.0
    rule: str              # short human-readable reason


def _addr(from_header: str) -> str:
    return from_header.lower().strip()


def _contains_any(haystack: str, needles: list[str]) -> str | None:
    for n in needles:
        if n and n.lower() in haystack:
            return n
    return None


def classify(headers: dict[str, str], policy: dict) -> Verdict:
    subject = headers.get("subject", "").lower()
    from_addr = _addr(headers.get("from", ""))
    list_unsub = headers.get("list-unsubscribe", "")
    auto_submitted = headers.get("auto-submitted", "").lower()
    precedence = headers.get("precedence", "").lower()

    senders = policy.get("senders", {})
    keywords = policy.get("keywords", {})
    noise_kw = keywords.get("noise", [])
    action_kw = keywords.get("action", [])
    important_domains = senders.get("important_domains", [])
    collaborators = senders.get("collaborators", [])

    # 1. noise -----------------------------------------------------------------
    hit = _contains_any(subject, noise_kw) or _contains_any(from_addr, noise_kw)
    if hit:
        return Verdict("noise", 0.95, f"noise keyword '{hit}'")
    if list_unsub or precedence in {"bulk", "list", "junk"} or auto_submitted not in {
        "",
        "no",
    }:
        return Verdict("noise", 0.9, "bulk/list mail headers")
    if any(tok in from_addr for tok in ("no-reply", "noreply", "donotreply")):
        return Verdict("noise", 0.8, "no-reply sender")

    # 2. explicit action -----------------------------------------------------
    hit = _contains_any(subject, action_kw)
    if hit:
        return Verdict("action", 0.75, f"action keyword '{hit}'")

    # 3. important sender ---------------------------------------------------
    dom = _contains_any(from_addr, important_domains)
    if dom:
        return Verdict("action", 0.6, f"important domain '{dom}'")
    collab = _contains_any(from_addr, collaborators)
    if collab:
        return Verdict("action", 0.6, f"known collaborator '{collab}'")

    # 4. notifications that are not quite noise ---------------------------
    if "notification" in from_addr or re.search(r"\bnotifications?@", from_addr):
        return Verdict("fyi", 0.7, "notification sender")

    # 5. default ---------------------------------------------------------
    return Verdict("fyi", 0.3, "no rule matched; personal mail default")
