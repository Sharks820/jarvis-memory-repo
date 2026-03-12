from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class OwnerGuardState(TypedDict):
    enabled: bool
    owner_user_id: str
    trusted_mobile_devices: list[str]
    master_password_hash: str
    master_password_salt_b64: str
    master_password_iterations: int
    updated_utc: str


DEFAULT_OWNER_GUARD: OwnerGuardState = {
    "enabled": False,
    "owner_user_id": "",
    "trusted_mobile_devices": [],
    "master_password_hash": "",  # nosec B105
    "master_password_salt_b64": "",  # nosec B105
    "master_password_iterations": 200000,  # nosec B105
    "updated_utc": "",
}


from jarvis_engine._shared import atomic_write_json as _atomic_write_json
from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine._shared import safe_int as _safe_int


def owner_guard_path(root: Path) -> Path:
    return root / ".planning" / "security" / "owner_guard.json"


def read_owner_guard(root: Path) -> OwnerGuardState:
    from jarvis_engine._shared import load_json_file

    path = owner_guard_path(root)
    raw = load_json_file(path, None, expected_type=dict)
    if raw is None:
        return {
            "enabled": False,
            "owner_user_id": "",
            "trusted_mobile_devices": [],
            "master_password_hash": "",
            "master_password_salt_b64": "",
            "master_password_iterations": 200000,
            "updated_utc": "",
        }
    devices = raw.get("trusted_mobile_devices", [])
    if not isinstance(devices, list):
        devices = []
    return {
        "enabled": bool(raw.get("enabled", False)),
        "owner_user_id": str(raw.get("owner_user_id", "")).strip()[:64],
        "trusted_mobile_devices": [str(d).strip()[:128] for d in devices if str(d).strip()],
        "master_password_hash": str(raw.get("master_password_hash", "")).strip(),
        "master_password_salt_b64": str(raw.get("master_password_salt_b64", "")).strip(),
        "master_password_iterations": _safe_int(raw.get("master_password_iterations", 200000), 200000),
        "updated_utc": str(raw.get("updated_utc", "")),
    }


def write_owner_guard(
    root: Path,
    *,
    enabled: bool | None = None,
    owner_user_id: str | None = None,
    trusted_mobile_devices: list[str] | None = None,
) -> OwnerGuardState:
    state = read_owner_guard(root)
    if enabled is not None:
        state["enabled"] = enabled
    if owner_user_id is not None:
        state["owner_user_id"] = owner_user_id.strip()[:64]
    if trusted_mobile_devices is not None:
        state["trusted_mobile_devices"] = [
            str(d).strip()[:128] for d in trusted_mobile_devices if str(d).strip()
        ]
    state["updated_utc"] = _now_iso()
    _atomic_write_json(owner_guard_path(root), dict(state))
    return state


def _hash_master_password(password: str, *, salt: bytes, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return digest.hex()


def set_master_password(root: Path, password: str, *, iterations: int = 200000) -> OwnerGuardState:
    cleaned = password.strip()
    if len(cleaned) < 10:
        raise ValueError("master password must be at least 10 characters")
    salt = secrets.token_bytes(16)
    state = read_owner_guard(root)
    state["master_password_salt_b64"] = base64.b64encode(salt).decode("ascii")
    state["master_password_iterations"] = int(max(100000, iterations))
    state["master_password_hash"] = _hash_master_password(
        cleaned,
        salt=salt,
        iterations=int(state["master_password_iterations"]),
    )
    state["updated_utc"] = _now_iso()
    _atomic_write_json(owner_guard_path(root), dict(state))
    return state


def clear_master_password(root: Path) -> OwnerGuardState:
    state = read_owner_guard(root)
    state["master_password_hash"] = ""  # nosec B105
    state["master_password_salt_b64"] = ""  # nosec B105
    state["master_password_iterations"] = 200000
    _atomic_write_json(owner_guard_path(root), dict(state))
    return state


def verify_master_password(root: Path, password: str) -> bool:
    state = read_owner_guard(root)
    expected = str(state.get("master_password_hash", "")).strip()
    salt_b64 = str(state.get("master_password_salt_b64", "")).strip()
    iterations = int(state.get("master_password_iterations", 200000))
    if not expected or not salt_b64:
        return False
    try:
        salt = base64.b64decode(salt_b64.encode("ascii"), validate=True)
    except ValueError as exc:
        logger.debug("Invalid master password salt encoding: %s", exc)
        return False
    actual = _hash_master_password(password.strip(), salt=salt, iterations=max(100000, iterations))
    return hmac.compare_digest(actual, expected)


def trust_mobile_device(root: Path, device_id: str) -> OwnerGuardState:
    state = read_owner_guard(root)
    cleaned = device_id.strip()[:128]
    if not cleaned:
        raise ValueError("device_id is required")
    trusted = {str(d).strip()[:128] for d in state.get("trusted_mobile_devices", []) if str(d).strip()}
    trusted.add(cleaned)
    return write_owner_guard(root, trusted_mobile_devices=sorted(trusted))


def revoke_mobile_device(root: Path, device_id: str) -> OwnerGuardState:
    state = read_owner_guard(root)
    cleaned = device_id.strip()[:128]
    trusted = [d for d in state.get("trusted_mobile_devices", []) if d != cleaned]
    return write_owner_guard(root, trusted_mobile_devices=trusted)
