"""FastMCP entrypoint.

``main()`` ensures directories exist, runs SQLite migrations, tries a best-effort
``ensure_labels`` (so the first classify can't fail on a missing label), and
starts the stdio server. Registered as the ``hermes-ops`` console script and as
``python -m hermes_ops``.

Register it with Hermes in ``~/.hermes/config.yaml`` under ``mcp_servers:`` with
stdio transport; tools then arrive as ``mcp_hermes-ops_<tool>``. Tag-scoped
surfaces: cron loads tools tagged ``cron`` (currently ``ops_status``, and in
later phases ``mail_rollup`` / ``mail_autotriage`` / ``mail_classify``); the
interactive Telegram surface loads everything.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from hermes_ops import config
from hermes_ops.policy.loader import raw_policy_text
from hermes_ops.store import db
from hermes_ops.tools import calendar as calendar_tools
from hermes_ops.tools import classify as classify_tools
from hermes_ops.tools import drafts as draft_tools
from hermes_ops.tools import mail as mail_tools
from hermes_ops.tools import ops as ops_tools

log = logging.getLogger("hermes_ops")

mcp = FastMCP("hermes-ops")


@mcp.resource("config://policy")
def policy_resource() -> str:
    """The active classification and calendar policy (policy.yaml), read-only.
    Read this to explain why a message was classified a certain way. It cannot be
    changed through a tool — edit policy.yaml on disk; the server hot-reloads it."""
    return raw_policy_text()


ops_tools.register(mcp)
mail_tools.register(mcp)
classify_tools.register(mcp)
calendar_tools.register(mcp)
draft_tools.register(mcp)


def _bootstrap() -> None:
    loaded = config.load_env_file()
    if loaded:
        log.info("loaded env from %s", loaded)
    config.ensure_dirs()
    version = db.init()
    log.info("db ready: schema v%s at %s", version, config.db_path())

    try:
        from hermes_ops.adapters.gmail import GmailAdapter
        from hermes_ops.auth import oauth

        labels = GmailAdapter(oauth.load_credentials()).ensure_labels()
        log.info("Hermes/* labels ensured: %s", ", ".join(sorted(labels)))
    except Exception as exc:  # noqa: BLE001 - server must still start for ops_status
        log.warning("skipped ensure_labels (%s); run `hermes-ops-auth login`", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _bootstrap()
    mcp.run()


if __name__ == "__main__":
    main()
