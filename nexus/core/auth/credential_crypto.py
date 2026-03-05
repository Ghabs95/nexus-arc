"""Encryption helpers for user credentials (GitHub tokens, Codex keys)."""

from __future__ import annotations

import base64
import json
import os
from typing import Any


def _load_aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "cryptography package is required for credential encryption. "
            "Install it with: pip install cryptography"
        ) from exc
    return AESGCM


def _decode_master_key(raw_value: str) -> bytes:
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError("NEXUS_CREDENTIALS_MASTER_KEY is required")

    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception:
        decoded = value.encode("utf-8")

    if len(decoded) != 32:
        raise ValueError("NEXUS_CREDENTIALS_MASTER_KEY must resolve to 32 bytes")
    return decoded


def _key_version() -> int:
    raw = os.getenv("NEXUS_CREDENTIALS_KEY_VERSION", "1")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 1
    return max(1, parsed)


def _master_key() -> bytes:
    return _decode_master_key(os.getenv("NEXUS_CREDENTIALS_MASTER_KEY", ""))


def encrypt_secret(plaintext: str, *, key_version: int | None = None) -> str:
    """Encrypt plaintext into a compact JSON envelope."""
    AESGCM = _load_aesgcm()
    key = _master_key()
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, str(plaintext or "").encode("utf-8"), None)
    payload = {
        "v": int(key_version or _key_version()),
        "n": base64.b64encode(nonce).decode("ascii"),
        "c": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(payload, separators=(",", ":"))


def decrypt_secret(envelope: str) -> str:
    """Decrypt a credential envelope created by :func:`encrypt_secret`."""
    AESGCM = _load_aesgcm()
    payload: dict[str, Any] = json.loads(str(envelope or "{}"))
    nonce = base64.b64decode(str(payload.get("n", "")).encode("ascii"))
    ciphertext = base64.b64decode(str(payload.get("c", "")).encode("ascii"))
    aesgcm = AESGCM(_master_key())
    plain = aesgcm.decrypt(nonce, ciphertext, None)
    return plain.decode("utf-8")
