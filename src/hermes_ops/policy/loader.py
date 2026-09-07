"""Load ``policy.yaml`` (design §8) with defaults merged in.

The policy is exposed to the agent as the read-only MCP resource
``config://policy`` — it can be read to explain a classification, never mutated
through a tool. Phase 2 adds the rules engine that consumes ``senders`` /
``keywords``; Phase 0 just needs a valid shape and the raw text for the resource.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from hermes_ops import config

DEFAULT_POLICY: dict = {
    "timezone": "America/Bogota",
    "calendars": {
        "hermes": "primary",
        "personal": "primary",
        "default": "hermes",
    },
    # v0.2: availability is checked across all of these, not just the write target
    "conflict_calendars": ["hermes", "personal"],
    "senders": {
        "important_domains": [],
        "collaborators": [],
    },
    "keywords": {
        "action": [],
        "noise": [],
    },
    "ingestion": {
        "ics_allowlist_domains": [],
        "extracted_requires_confirmation": True,
        "max_future_days": 365,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_policy(path: Path | None = None) -> dict:
    target = path or config.policy_path()
    if not target.exists():
        return copy.deepcopy(DEFAULT_POLICY)
    loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return _deep_merge(DEFAULT_POLICY, loaded)


def raw_policy_text(path: Path | None = None) -> str:
    target = path or config.policy_path()
    if target.exists():
        return target.read_text(encoding="utf-8")
    return yaml.safe_dump(DEFAULT_POLICY, sort_keys=False, allow_unicode=True)
