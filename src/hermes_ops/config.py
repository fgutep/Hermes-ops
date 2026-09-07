"""Filesystem layout and environment wiring.

Everything durable lives under ``$OPS_DATA_DIR`` (token, SQLite) so a single
``hermes backup`` of that directory captures all state. Config (client secret,
policy) lives under ``$OPS_CONFIG_DIR``. Both default to platform dirs when the
env vars are unset, which keeps local development zero-config.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP = "hermes-ops"


def load_env_file() -> Path | None:
    """Populate os.environ from a simple KEY=VALUE .env file, once.

    Real environment variables always win (``setdefault``). Search order:
    ``$OPS_ENV_FILE``, then ``./.env``, then ``$OPS_CONFIG_DIR/.env`` if that
    was itself set via the environment. First file that exists is used.
    """
    candidates = [
        os.environ.get("OPS_ENV_FILE", "").strip(),
        ".env",
    ]
    cfg = os.environ.get("OPS_CONFIG_DIR", "").strip()
    if cfg:
        candidates.append(str(Path(cfg).expanduser() / ".env"))

    for raw in candidates:
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
        return path
    return None


def _env_path(var: str) -> Path | None:
    val = os.environ.get(var, "").strip()
    return Path(val).expanduser() if val else None


def data_dir() -> Path:
    return _env_path("OPS_DATA_DIR") or Path(
        platformdirs.user_data_dir(APP, appauthor=False)
    )


def config_dir() -> Path:
    return _env_path("OPS_CONFIG_DIR") or Path(
        platformdirs.user_config_dir(APP, appauthor=False)
    )


def token_path() -> Path:
    return _env_path("OPS_TOKEN_PATH") or (data_dir() / "google_token.json")


def client_secret_path() -> Path:
    return _env_path("OPS_CLIENT_SECRET") or (config_dir() / "client_secret.json")


def policy_path() -> Path:
    return _env_path("OPS_POLICY_PATH") or (config_dir() / "policy.yaml")


def db_path() -> Path:
    return _env_path("OPS_DB_PATH") or (data_dir() / "state.db")


def ensure_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    config_dir().mkdir(parents=True, exist_ok=True)
