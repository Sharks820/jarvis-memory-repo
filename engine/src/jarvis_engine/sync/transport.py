"""Encrypted transport layer for sync payloads.

Uses PBKDF2HMAC key derivation + Fernet for authenticated encryption.
Payloads are compressed with zlib before encryption for efficiency.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import zlib
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Maximum raw (pre-compression) payload size to prevent memory exhaustion
# during serialization and encryption.  16 MiB is generous for sync deltas.
MAX_SYNC_PAYLOAD_BYTES = 16 * 1024 * 1024


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
    Uses atomic write-then-rename to avoid TOCTOU race on creation.
    """
    if salt_path.exists():
        return salt_path.read_bytes()

    salt = os.urandom(16)
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: write to temp file then rename to avoid race condition
    tmp = salt_path.with_suffix(".tmp")
    tmp.write_bytes(salt)
    try:
        os.chmod(str(tmp), 0o600)
    except OSError:
        pass  # Windows may not support chmod
    try:
        os.replace(str(tmp), str(salt_path))
    except OSError:
        # If replace fails (e.g. another process won the race), read the winner
        if salt_path.exists():
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return salt_path.read_bytes()
        raise
    logger.info("Created new sync salt at %s", salt_path)
    return salt


def encrypt_sync_payload(payload: dict[str, Any], fernet_key: bytes) -> bytes:
    """Serialize, compress, and encrypt *payload*.

    Pipeline: json.dumps -> size check -> zlib.compress(level=6) -> Fernet.encrypt.

    Raises ``ValueError`` if the serialized payload exceeds
    ``MAX_SYNC_PAYLOAD_BYTES`` (16 MiB) to prevent memory exhaustion.
    """
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(raw) > MAX_SYNC_PAYLOAD_BYTES:
        raise ValueError(
            f"Sync payload too large: {len(raw)} bytes exceeds "
            f"limit of {MAX_SYNC_PAYLOAD_BYTES} bytes ({MAX_SYNC_PAYLOAD_BYTES // (1024 * 1024)} MiB). "
            f"Split into smaller batches."
        )
    compressed = zlib.compress(raw, level=6)
    f = Fernet(fernet_key)
    return f.encrypt(compressed)


MAX_DECOMPRESSED_SIZE = 16 * 1024 * 1024  # 16 MiB


def decrypt_sync_payload(
    token: bytes, fernet_key: bytes, ttl: int = 3600,
) -> dict[str, Any]:
    """Decrypt and decompress a sync payload.

    Pipeline: Fernet.decrypt(ttl) -> zlib.decompress (size-limited) -> json.loads.
    The *ttl* parameter (seconds) rejects tokens older than the given window
    to prevent replay of captured encrypted payloads. Default: 1 hour.

    Decompressed output is limited to ``MAX_DECOMPRESSED_SIZE`` (16 MiB) to
    defend against decompression bomb attacks.
    """
    f = Fernet(fernet_key)
    compressed = f.decrypt(token, ttl=ttl)

    decompressor = zlib.decompressobj()
    chunks: list[bytes] = []
    total_size = 0
    # Feed compressed data in 64 KB blocks
    block_size = 65536
    offset = 0
    while offset < len(compressed):
        block = compressed[offset:offset + block_size]
        chunk = decompressor.decompress(block, MAX_DECOMPRESSED_SIZE - total_size)
        chunks.append(chunk)
        total_size += len(chunk)
        if total_size >= MAX_DECOMPRESSED_SIZE:
            raise ValueError("Decompressed payload exceeds 16 MiB limit")
        offset += block_size
    # Flush remaining with size limit to prevent decompression bombs
    chunk = decompressor.flush(MAX_DECOMPRESSED_SIZE - total_size + 1)
    chunks.append(chunk)
    total_size += len(chunk)
    if total_size > MAX_DECOMPRESSED_SIZE:
        raise ValueError("Decompressed payload exceeds 16 MiB limit")
    raw = b"".join(chunks)
    return json.loads(raw)


class SyncTransport:
    """High-level sync transport with lazy key derivation."""

    def __init__(self, signing_key: str, salt_path: Path) -> None:
        self._signing_key = signing_key
        self._salt_path = salt_path
        self._fernet_key: bytes | None = None
        self._key_lock = threading.Lock()

    def _ensure_key(self) -> bytes:
        """Lazily derive the Fernet key on first use (double-checked locking)."""
        if self._fernet_key is None:
            with self._key_lock:
                if self._fernet_key is None:
                    salt = get_or_create_salt(self._salt_path)
                    self._fernet_key = derive_sync_key(self._signing_key, salt)
        return self._fernet_key

    def encrypt(self, payload: dict[str, Any]) -> bytes:
        """Encrypt a sync payload dict."""
        return encrypt_sync_payload(payload, self._ensure_key())

    def decrypt(self, token: bytes, ttl: int = 3600) -> dict[str, Any]:
        """Decrypt a sync payload token."""
        return decrypt_sync_payload(token, self._ensure_key(), ttl=ttl)
