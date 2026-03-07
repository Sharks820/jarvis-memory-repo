"""Tests for owner_guard: state read/write, device trust, master password lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_engine.owner_guard import (
    DEFAULT_OWNER_GUARD,
    clear_master_password,
    owner_guard_path,
    read_owner_guard,
    revoke_mobile_device,
    set_master_password,
    trust_mobile_device,
    verify_master_password,
    write_owner_guard,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Project root with the security directory pre-created."""
    security = tmp_path / ".planning" / "security"
    security.mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# owner_guard_path
# ---------------------------------------------------------------------------


class TestOwnerGuardPath:
    def test_returns_expected_path(self, tmp_path: Path) -> None:
        result = owner_guard_path(tmp_path)
        assert result == tmp_path / ".planning" / "security" / "owner_guard.json"


# ---------------------------------------------------------------------------
# read_owner_guard
# ---------------------------------------------------------------------------


class TestReadOwnerGuard:
    def test_defaults_when_file_missing(self, tmp_path: Path) -> None:
        state = read_owner_guard(tmp_path)
        assert state["enabled"] is False
        assert state["owner_user_id"] == ""
        assert state["trusted_mobile_devices"] == []
        assert state["master_password_hash"] == ""
        assert state["master_password_salt_b64"] == ""
        assert state["master_password_iterations"] == 200000

    def test_defaults_on_corrupt_json(self, root: Path) -> None:
        path = owner_guard_path(root)
        path.write_text("CORRUPT!!!", encoding="utf-8")
        state = read_owner_guard(root)
        assert state == DEFAULT_OWNER_GUARD

    def test_defaults_when_json_is_not_dict(self, root: Path) -> None:
        path = owner_guard_path(root)
        path.write_text("[1,2,3]", encoding="utf-8")
        state = read_owner_guard(root)
        assert state == DEFAULT_OWNER_GUARD

    def test_reads_valid_state(self, root: Path) -> None:
        path = owner_guard_path(root)
        data = {
            "enabled": True,
            "owner_user_id": "conner",
            "trusted_mobile_devices": ["galaxy_s25"],
            "master_password_hash": "abc123",
            "master_password_salt_b64": "c2FsdA==",
            "master_password_iterations": 200000,
            "updated_utc": "2026-01-15T00:00:00",
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_owner_guard(root)
        assert state["enabled"] is True
        assert state["owner_user_id"] == "conner"
        assert state["trusted_mobile_devices"] == ["galaxy_s25"]

    def test_owner_user_id_truncated_at_64(self, root: Path) -> None:
        path = owner_guard_path(root)
        data = {"owner_user_id": "u" * 100}
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_owner_guard(root)
        assert len(state["owner_user_id"]) == 64

    def test_device_ids_truncated_at_128(self, root: Path) -> None:
        path = owner_guard_path(root)
        data = {"trusted_mobile_devices": ["d" * 200]}
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_owner_guard(root)
        assert len(state["trusted_mobile_devices"][0]) == 128

    def test_empty_device_ids_filtered(self, root: Path) -> None:
        path = owner_guard_path(root)
        data = {"trusted_mobile_devices": ["valid", "", "  ", "also_valid"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_owner_guard(root)
        assert state["trusted_mobile_devices"] == ["valid", "also_valid"]

    def test_non_list_devices_becomes_empty_list(self, root: Path) -> None:
        path = owner_guard_path(root)
        data = {"trusted_mobile_devices": "not_a_list"}
        path.write_text(json.dumps(data), encoding="utf-8")
        state = read_owner_guard(root)
        assert state["trusted_mobile_devices"] == []


# ---------------------------------------------------------------------------
# write_owner_guard
# ---------------------------------------------------------------------------


class TestWriteOwnerGuard:
    def test_write_creates_file(self, root: Path) -> None:
        state = write_owner_guard(root, enabled=True)
        assert state["enabled"] is True
        assert owner_guard_path(root).exists()

    def test_partial_update_enabled_only(self, root: Path) -> None:
        write_owner_guard(root, enabled=False, owner_user_id="conner")
        state = write_owner_guard(root, enabled=True)
        assert state["enabled"] is True
        assert state["owner_user_id"] == "conner"  # unchanged

    def test_partial_update_owner_user_id_only(self, root: Path) -> None:
        write_owner_guard(root, enabled=True)
        state = write_owner_guard(root, owner_user_id="bob")
        assert state["enabled"] is True  # unchanged
        assert state["owner_user_id"] == "bob"

    def test_write_sets_updated_utc(self, root: Path) -> None:
        state = write_owner_guard(root, enabled=True)
        assert state["updated_utc"] != ""

    def test_write_trusted_mobile_devices(self, root: Path) -> None:
        state = write_owner_guard(
            root, trusted_mobile_devices=["galaxy_s25", "pixel_9"]
        )
        assert state["trusted_mobile_devices"] == ["galaxy_s25", "pixel_9"]

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        state = write_owner_guard(tmp_path, enabled=True)
        assert state["enabled"] is True
        assert owner_guard_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# trust_mobile_device / revoke_mobile_device
# ---------------------------------------------------------------------------


class TestDeviceTrust:
    def test_trust_adds_device(self, root: Path) -> None:
        state = trust_mobile_device(root, "galaxy_s25_primary")
        assert "galaxy_s25_primary" in state["trusted_mobile_devices"]

    def test_trust_duplicate_device_is_idempotent(self, root: Path) -> None:
        trust_mobile_device(root, "galaxy_s25")
        state = trust_mobile_device(root, "galaxy_s25")
        assert state["trusted_mobile_devices"].count("galaxy_s25") == 1

    def test_trust_empty_device_raises(self, root: Path) -> None:
        with pytest.raises(ValueError, match="device_id is required"):
            trust_mobile_device(root, "   ")

    def test_trust_truncates_long_device_id(self, root: Path) -> None:
        long_id = "d" * 200
        state = trust_mobile_device(root, long_id)
        assert len(state["trusted_mobile_devices"][0]) == 128

    def test_revoke_removes_device(self, root: Path) -> None:
        trust_mobile_device(root, "galaxy_s25")
        state = revoke_mobile_device(root, "galaxy_s25")
        assert "galaxy_s25" not in state["trusted_mobile_devices"]

    def test_revoke_nonexistent_is_noop(self, root: Path) -> None:
        trust_mobile_device(root, "galaxy_s25")
        state = revoke_mobile_device(root, "nonexistent_device")
        assert "galaxy_s25" in state["trusted_mobile_devices"]

    def test_trust_multiple_devices(self, root: Path) -> None:
        trust_mobile_device(root, "device_a")
        state = trust_mobile_device(root, "device_b")
        assert "device_a" in state["trusted_mobile_devices"]
        assert "device_b" in state["trusted_mobile_devices"]


# ---------------------------------------------------------------------------
# Master password
# ---------------------------------------------------------------------------


class TestMasterPassword:
    def test_set_and_verify_password(self, root: Path) -> None:
        set_master_password(root, "SecurePass123!")
        assert verify_master_password(root, "SecurePass123!") is True

    def test_wrong_password_fails(self, root: Path) -> None:
        set_master_password(root, "SecurePass123!")
        assert verify_master_password(root, "WrongPassword!") is False

    def test_password_too_short_raises(self, root: Path) -> None:
        with pytest.raises(ValueError, match="at least 10 characters"):
            set_master_password(root, "short")

    def test_password_stripped(self, root: Path) -> None:
        set_master_password(root, "  SecurePass123!  ")
        assert verify_master_password(root, "SecurePass123!") is True

    def test_verify_returns_false_when_no_password_set(self, root: Path) -> None:
        assert verify_master_password(root, "anything") is False

    def test_clear_password(self, root: Path) -> None:
        set_master_password(root, "SecurePass123!")
        clear_master_password(root)
        assert verify_master_password(root, "SecurePass123!") is False

    def test_clear_password_zeroes_hash_fields(self, root: Path) -> None:
        set_master_password(root, "SecurePass123!")
        state = clear_master_password(root)
        assert state["master_password_hash"] == ""
        assert state["master_password_salt_b64"] == ""
        assert state["master_password_iterations"] == 200000

    def test_minimum_iterations_enforced(self, root: Path) -> None:
        state = set_master_password(root, "SecurePass123!", iterations=50)
        assert state["master_password_iterations"] >= 100000

    def test_verify_with_corrupted_salt_returns_false(self, root: Path) -> None:
        set_master_password(root, "SecurePass123!")
        path = owner_guard_path(root)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["master_password_salt_b64"] = "NOT_VALID_BASE64!!!"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert verify_master_password(root, "SecurePass123!") is False

    def test_set_password_persists_salt_and_hash(self, root: Path) -> None:
        set_master_password(root, "SecurePass123!")
        state = read_owner_guard(root)
        assert state["master_password_hash"] != ""
        assert state["master_password_salt_b64"] != ""


# ---------------------------------------------------------------------------
# Guard enabling/disabling
# ---------------------------------------------------------------------------


class TestGuardEnableDisable:
    def test_enable_guard(self, root: Path) -> None:
        state = write_owner_guard(root, enabled=True)
        assert state["enabled"] is True
        persisted = read_owner_guard(root)
        assert persisted["enabled"] is True

    def test_disable_guard(self, root: Path) -> None:
        write_owner_guard(root, enabled=True)
        state = write_owner_guard(root, enabled=False)
        assert state["enabled"] is False
        persisted = read_owner_guard(root)
        assert persisted["enabled"] is False
