# HERMES_Readme — self-install guide for the Hermes agent

You (Hermes) are installing **hermes-ops**, an MCP server that will give you
Gmail and calendar tools. Follow these steps in order. Stop and ask the operator
only where a step is marked **[HUMAN]**.

Repository: `https://github.com/fgutep/Hermes-ops`

---

## 0. Preconditions — check, don't assume

```bash
python3 --version      # need >= 3.11
uv --version           # need uv; if missing: `pip install uv` or see astral.sh/uv
git --version
```

Decide two directories and remember them for every later step:

| Variable | Meaning | Example |
|---|---|---|
| `OPS_DATA_DIR` | token + SQLite state — **must be on the backed-up volume** | `~/.hermes/ops/data` |
| `OPS_CONFIG_DIR` | `policy.yaml` and `client_secret.json` | `~/.hermes/ops/config` |

---

## 1. Clone and install

```bash
git clone https://github.com/fgutep/Hermes-ops.git
cd Hermes-ops
uv sync --extra dev
uv run pytest -q            # expect: 34 passed. If not, stop and report.
```

---

## 2. Google credentials

### 2a. [HUMAN] OAuth client

The operator must, once, in the Google Cloud console:

1. Enable **Gmail API** and **Google Calendar API**.
2. On the **OAuth consent screen**, add scopes
   `.../auth/gmail.modify`, `.../auth/gmail.compose`, `.../auth/calendar.events`;
   add the Google account to be managed as a user; set publishing status to
   **Production** (Testing expires refresh tokens after 7 days).
3. **Credentials → Create credentials → OAuth client ID → Desktop app**, download
   the JSON, and place it at `$OPS_CONFIG_DIR/client_secret.json`.

Ask the operator to confirm the file is in place before continuing.

### 2b. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and set:

```
OPS_DATA_DIR=<your data dir>
OPS_CONFIG_DIR=<your config dir>
OPS_CLIENT_SECRET=<your config dir>/client_secret.json
OPS_ENCRYPTION_KEY=            # fill from the next command
```

```bash
mkdir -p "$OPS_DATA_DIR" "$OPS_CONFIG_DIR"
uv run hermes-ops-auth keygen        # prints a 64-hex key
```

Put that key in `.env` as `OPS_ENCRYPTION_KEY`. **Record it** wherever the
operator keeps secrets — the encrypted token cannot be read without it, and the
identical value must be set on every host that runs this server.

### 2c. [HUMAN] Consent

```bash
uv run hermes-ops-auth login          # opens a browser
# headless host: uv run hermes-ops-auth login --no-browser  (operator opens the URL)
```

The operator picks the account to be managed and grants Gmail + Calendar.
Then:

```bash
uv run hermes-ops-auth status         # expect: valid : True, three scopes listed
```

---

## 3. Policy

```bash
cp policy.example.yaml "$OPS_CONFIG_DIR/policy.yaml"
```

Edit `$OPS_CONFIG_DIR/policy.yaml`:

- `calendars.hermes` — the target calendar id (Google Calendar → Settings →
  that calendar → "Integrate calendar" → Calendar ID). Or `primary`.
- `timezone` — IANA name, e.g. `America/Bogota`.
- `senders.important_domains`, `keywords`, `ingestion.ics_allowlist_domains` —
  the operator's real values.

This file stays local; it is gitignored and never committed.

---

## 4. Verify

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

Success = `ok: true`, `degraded: []`, the managed Gmail address shown,
`calendar_ok: true` with the calendar's name, and all five `Hermes/*` labels
`true` (the server creates them on first run).

If `degraded` is non-empty, read it — each entry names the failing probe
(`auth:` → re-run `hermes-ops-auth login`; `calendar:` → check scopes / the
calendar id in `policy.yaml`; `db:` → check `OPS_DATA_DIR` is writable).

---

## 5. Register hermes-ops with yourself

Add to your MCP config (`~/.hermes/config.yaml` under `mcp_servers:`):

```yaml
mcp_servers:
  hermes-ops:
    command: uv
    args: ["run", "--project", "<absolute path to Hermes-ops>", "hermes-ops"]
    transport: stdio
    env:
      OPS_DATA_DIR: <your data dir>
      OPS_CONFIG_DIR: <your config dir>
      OPS_ENCRYPTION_KEY: "<the key from step 2b>"
```

Reload your config. The tools appear as `mcp_hermes-ops_<tool>`.

- **Cron / no-LLM surface:** load only the `cron`-tagged tools — `ops_status`,
  `mail_rollup`, `mail_autotriage`, `mail_classify`, `cal_query`.
- **Interactive surface:** load all 13.

---

## 6. First real use

1. `mcp_hermes-ops_ops_status` — confirm green.
2. `mcp_hermes-ops_mail_rollup` with `since: "7d"` — see the current inbox state.
3. `mcp_hermes-ops_mail_autotriage` with `apply: false` first — inspect the
   verdicts before letting it write labels.
4. For anything on the calendar: `event_propose` (read-only) → review →
   `event_create`. Never skip the propose step for events extracted from message
   bodies.

---

## Security invariants — do not violate

- **Never commit** `.env`, `secrets/`, `client_secret*.json`, `*.db`, or the
  token. They are gitignored; keep it that way.
- **Never print** `OPS_ENCRYPTION_KEY`, the client secret, or raw token contents
  into logs, chat, or commit messages.
- The OAuth scopes cannot send mail. Do not add `gmail.send`. `draft_reply`
  drafts only; a human sends.
- `event_propose` treats the email body as untrusted data. A second explicit
  `event_create` call is always required before anything reaches the calendar.

---

## Updating later

```bash
cd Hermes-ops && git pull && uv sync
uv run pytest -q
# restart the MCP server / reload Hermes config
```

Migrations run automatically on startup; `OPS_DATA_DIR` is preserved across
upgrades.
