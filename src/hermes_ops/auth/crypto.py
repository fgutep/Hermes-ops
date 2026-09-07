"""Token-at-rest encryption: AES-256-GCM, key from ``$OPS_ENCRYPTION_KEY``.

On-disk format is ``nonce (12 bytes) || ciphertext`` written as raw bytes. The
key is 32 bytes, supplied as 64 hex characters. Losing the key means re-running
``hermes-ops-auth login``; the key must be identical on every host that reads the
same token file (e.g. your laptop where you run consent, and the Pi).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12
_KEY_LEN = 32


class CryptoError(RuntimeError):
    pass


def _key() -> bytes:
    raw = os.environ.get("OPS_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise CryptoError(
            "OPS_ENCRYPTION_KEY is not set. Generate one with `hermes-ops-auth keygen`."
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise CryptoError("OPS_ENCRYPTION_KEY must be hex characters") from exc
    if len(key) != _KEY_LEN:
        raise CryptoError(
            f"OPS_ENCRYPTION_KEY must be {_KEY_LEN} bytes ({_KEY_LEN * 2} hex chars), "
            f"got {len(key)}"
        )
    return key


def generate_key() -> str:
    """A fresh 32-byte key as 64 hex characters."""
    return os.urandom(_KEY_LEN).hex()


def encrypt_json(obj: dict) -> bytes:
    aes = AESGCM(_key())
    nonce = os.urandom(_NONCE_LEN)
    plaintext = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return nonce + aes.encrypt(nonce, plaintext, None)


def decrypt_json(blob: bytes) -> dict:
    if len(blob) <= _NONCE_LEN:
        raise CryptoError("token blob is too short to be valid")
    aes = AESGCM(_key())
    nonce, ciphertext = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    try:
        plaintext = aes.decrypt(nonce, ciphertext, None)
    except Exception as exc:  # cryptography raises InvalidTag et al.
        raise CryptoError(
            "token decryption failed — wrong OPS_ENCRYPTION_KEY or corrupt file"
        ) from exc
    return json.loads(plaintext)


def write_encrypted(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_json(obj))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best effort; no-op on Windows


def read_encrypted(path: Path) -> dict:
    if not path.exists():
        raise CryptoError(f"token file not found at {path}; run `hermes-ops-auth login`")
    return decrypt_json(path.read_bytes())
