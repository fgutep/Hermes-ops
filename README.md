# hermes-ops

A single [FastMCP](https://github.com/jlowin/fastmcp) server that gives an agent
Gmail retrieval, durable classification, and calendar manipulation through a
small, opinionated tool surface — designed for the Hermes personal-assistant
agent but usable by any MCP client.

**Status:** all 13 tools implemented and validated against a live Google
account. 34 offline tests pass.

Design principles:

- **One OAuth identity, one state DB, one process.** Scopes are `gmail.modify` +
  `gmail.compose` + `calendar.events` — there is deliberately **no send scope**,
  so nothing can send mail; `draft_reply` only drafts.
- **Retrieval fails loudly.** `mail_find` compiles Gmail query syntax from
  structured hints and, on zero hits, walks a relaxation ladder, reporting every
  step in `degraded` and the final `query_used`.
- **Calendar writes are verified.** `event_create` dedupes, checks conflicts,
  inserts, then **reads the event back from Google** before persisting, and links
  it to the source message for provenance.
- **Policy is data, not code.** `policy.yaml` (senders, keywords, calendars) is
  hot-reloaded and exposed read-only at `config://policy`.

## Tools

| Tool | Tags | What it does |
|---|---|---|
| `ops_status` | cron, interactive | health snapshot: token, scopes, labels, calendar probe, DB |
| `mail_find` | interactive | hint-based retrieval; compiles Gmail syntax; relaxation ladder on zero hits |
| `mail_read` | interactive | normalized plain-text for a message/thread; quotes collapsed; attachment metadata only |
| `mail_classify` | cron, interactive | durably classify 1–50 messages (label + side effect + local row), per-id status |
| `mail_autotriage` | cron, interactive | deterministic `rules-v3` pass; auto-applies only high-confidence noise/reference/fyi |
| `mail_rollup` | cron, interactive | "what needs me?" — counts, action queue, stale `waiting`, reactivated threads |
| `event_propose` | interactive | message → `EventProposal` (ICS or text extraction); writes nothing |
| `event_create` | interactive | dedupe → conflict → insert → read-back → persist row + provenance link |
| `event_update` | interactive | patch timing/title/location, re-verify by read-back |
| `event_delete` | interactive | delete Google event + local `events`/`links` rows |
| `cal_query` | cron, interactive | compact `date|time|title|loc|id` lines, or free/busy |
| `provenance` | interactive | bidirectional email ↔ event lookup |
| `draft_reply` | interactive | in-thread Gmail draft, idempotent (one per source message); never sends |

Plus the read-only resource `config://policy`.

## Categories

Five, fixed. Each maps to one Gmail label and one inbox side effect:

| Category | Label | Read? | Archive? |
|---|---|---|---|
| `action` | `Hermes/Action` | no | no |
| `waiting` | `Hermes/Waiting` | yes | no |
| `fyi` | `Hermes/FYI` | yes | no |
| `reference` | `Hermes/Reference` | yes | yes |
| `noise` | `Hermes/Noise` | yes | yes |

The server creates any missing `Hermes/*` label on startup.

## Layout

```
src/hermes_ops/
  server.py        FastMCP instance, startup (migrate + ensure-labels), stdio run
  config.py        $OPS_DATA_DIR / $OPS_CONFIG_DIR wiring + .env loader
  schemas.py       Pydantic tool contract
  query.py         Gmail query compiler + relaxation ladder
  mime.py          payload headers / body extraction / attachment listing
  ingest.py        message -> EventProposal (ICS parse, text extraction, dedupe key)
  timeutil.py      timezone helpers, window parsing
  policy/          loader.py (policy.yaml + defaults), rules.py (rules-v3 classifier)
  auth/            crypto.py (AES-256-GCM token), oauth.py, __main__.py (CLI)
  store/           db.py (SQLite WAL, migrations), repo.py (data access + audit)
  adapters/        gmail.py, calendar.py  — Google calls only, no policy
  tools/           ops.py, mail.py, classify.py, calendar.py, drafts.py, _common.py
tests/             offline: crypto, migrations, policy merge, query compiler, rules, mime/ingest
policy.example.yaml
```

## Quick start

```bash
git clone https://github.com/fgutep/Hermes-ops.git
cd Hermes-ops
uv sync --extra dev
uv run pytest                 # 34 offline tests, no network
```

### 1. Google Cloud project

- Enable the **Gmail API** and **Google Calendar API**.
- **OAuth consent screen**: add scopes `.../auth/gmail.modify`,
  `.../auth/gmail.compose`, `.../auth/calendar.events`; add the account that will
  be managed as a test user. Set publishing status to **Production** — in
  **Testing** the refresh token expires after 7 days, which a long-running server
  cannot tolerate.
- **Credentials → OAuth client ID → Desktop app** → download the JSON.

### 2. Local config

```bash
cp .env.example .env          # then edit it:
#   OPS_DATA_DIR      where the token + SQLite live (back this up)
#   OPS_CONFIG_DIR    where policy.yaml lives
#   OPS_CLIENT_SECRET path to the downloaded Desktop-app client JSON
uv run hermes-ops-auth keygen # -> put the 64-hex value in .env as OPS_ENCRYPTION_KEY

cp policy.example.yaml "$OPS_CONFIG_DIR/policy.yaml"   # then set real values
```

`.env`, `secrets/`, and the data/config dirs are gitignored — no secret is ever
committed. The token file is AES-256-GCM encrypted; the **same
`OPS_ENCRYPTION_KEY` must be present wherever the token is read** (e.g. dev
machine and deployment host).

### 3. Consent

```bash
uv run hermes-ops-auth login    # opens a browser; --no-browser prints the URL
uv run hermes-ops-auth status   # verify token validity / scopes / expiry
```

### 4. Smoke test

```bash
uv run python -c "
import asyncio, json
from fastmcp import Client
from hermes_ops.server import mcp, _bootstrap
_bootstrap()
async def go():
    async with Client(mcp) as c:
        out = await c.call_tool('ops_status', {})
        print(json.dumps(out.structured_content, indent=2, ensure_ascii=False))
asyncio.run(go())
"
```

Expect `ok: true`, `degraded: []`, the managed Gmail address, `calendar_ok: true`
with the `default` calendar's name, and all five `Hermes/*` labels `true`.

## Register with an MCP client / Hermes

```yaml
mcp_servers:
  hermes-ops:
    command: uv
    args: ["run", "--project", "/path/to/Hermes-ops", "hermes-ops"]
    transport: stdio
    env:
      OPS_DATA_DIR: /path/to/data
      OPS_CONFIG_DIR: /path/to/config
      OPS_ENCRYPTION_KEY: "<same key used at consent time>"
```

Tools arrive namespaced as `mcp_hermes-ops_<tool>`. Scope the cron surface to the
`cron`-tagged tools (`ops_status`, `mail_rollup`, `mail_autotriage`,
`mail_classify`, `cal_query`); give the interactive surface all 13.

## Notes

- `rules-v3` (`policy/rules.py`) is deterministic and policy-driven. Tune the
  `important_domains` rule before its `action` verdicts feed an agent queue —
  domain-only matching over-fires. `mail_autotriage` is conservative by default
  (auto-applies only `noise`/`reference`/`fyi` at confidence ≥ 0.9).
- Deferred: `cal_free_slots`, `policy_explain`, attachment fetch, `mail_snooze`,
  thread-level classification, Gmail `watch()` push.
