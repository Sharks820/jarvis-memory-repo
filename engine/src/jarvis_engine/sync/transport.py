"""Encrypted transport layer for sync payloads.

Uses PBKDF2HMAC key derivation + Fernet for authenticated encryption.
Payloads are compressed with zlib before encryption for efficiency.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import zlib
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


def derive_sync_key(signing_key: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from *signing_key* and *salt*.

    Uses PBKDF2HMAC with SHA-256, 32-byte output, 480,000 iterations.
    Returns a base64-urlsafe-encoded key suitable for ``Fernet(key)``.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    raw_key = kdf.derive(signing_key.encode("utf-8"))
    return base64.urlsafe_b64encode(raw_key)


def get_or_create_salt(salt_path: Path) -> bytes:
    """Return salt bytes from *salt_path*, creating the file if needed.

    New salt is 16 random bytes via ``os.urandom``.
    File permissions are set to 0o600 (owner-only).
    """
    if salt_path.exists():
        return salt_path.read_bytes()

    salt = os.urandom(16)
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_bytes(salt)
    try:
        os.chmod(str(salt_path), 0o600)
    except OSError:
        pass  # Windows may not support chmod
    logger.info("Created new sync salt at %s", salt_path)
    return salt


def encrypt_sync_payload(payload: dict[str, Any], fernet_key: bytes) -> bytes:
    """Serialize, compress, and encrypt *payload*.

    Pipeline: json.dumps -> zlib.compress(level=6) -> Fernet.encrypt.
    """
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    f = Fernet(fernet_key)
    return f.encrypt(compressed)


def decrypt_sync_payload(token: bytes, fernet_key: bytes) -> dict[str, Any]:
    """Decrypt and decompress a sync payload.

    Pipeline: Fernet.decrypt -> zlib.decompress -> json.loads.
    """
    f = Fernet(fernet_key)
    compressed = f.decrypt(token)
    raw = zlib.decompress(compressed)
    return json.loads(raw)


class SyncTransport:
    """High-level sync transport with lazy key derivation."""

    def __init__(self, signing_key: str, salt_path: Path) -> None:
        self._signing_key = signing_key
        self._salt_path = salt_path
        self._fernet_key: bytes | None = None

    def _ensure_key(self) -> bytes:
        """Lazily derive the Fernet key on first use."""
        if self._fernet_key is None:
            salt = get_or_create_salt(self._salt_path)
            self._fernet_key = derive_sync_key(self._signing_key, salt)
        return self._fernet_key

    def encrypt(self, payload: dict[str, Any]) -> bytes:
        """Encrypt a sync payload dict."""
        return encrypt_sync_payload(payload, self._ensure_key())

    def decrypt(self, token: bytes) -> dict[str, Any]:
        """Decrypt a sync payload token."""
        return decrypt_sync_payload(token, self._ensure_key())
