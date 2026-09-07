"""Single OAuth identity: load, refresh (single-flight), and the one-time
interactive installed-app login.

Scopes are deliberately minimal (design §3):
  - gmail.modify   read, label add/remove, read-state
  - gmail.compose  draft creation *without* the ability to send
  - calendar.events  event CRUD, but never calendar CRUD

There is no ``gmail.send`` scope, so no code path — or prompt-injected
instruction — can send mail.
"""

from __future__ import annotations

import json
import threading

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from hermes_ops import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]

_refresh_lock = threading.Lock()


class AuthError(RuntimeError):
    pass


def _load_raw() -> Credentials:
    from hermes_ops.auth import crypto

    data = crypto.read_encrypted(config.token_path())
    return Credentials.from_authorized_user_info(data, SCOPES)


def load_credentials() -> Credentials:
    """Return valid credentials, refreshing in-process under a lock if needed."""
    from hermes_ops.auth import crypto

    creds = _load_raw()
    if creds.valid:
        return creds

    with _refresh_lock:
        creds = _load_raw()  # re-read: another thread may have refreshed
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            crypto.write_encrypted(config.token_path(), json.loads(creds.to_json()))
            return creds

    raise AuthError(
        "Google credentials are invalid and cannot be refreshed. "
        "Run `hermes-ops-auth login`."
    )


def run_login(open_browser: bool = True) -> Credentials:
    """One-time interactive consent. Writes the encrypted token file."""
    from hermes_ops.auth import crypto

    secret = config.client_secret_path()
    if not secret.exists():
        raise AuthError(
            f"OAuth client secret not found at {secret}.\n"
            "Download a *Desktop app* OAuth client JSON from Google Cloud Console "
            "and save it there (or set OPS_CLIENT_SECRET)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=open_browser, prompt="consent")
    crypto.write_encrypted(config.token_path(), json.loads(creds.to_json()))
    return creds
