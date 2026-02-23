from __future__ import annotations

from pathlib import Path

from jarvis_engine.owner_guard import (
    read_owner_guard,
    revoke_mobile_device,
    set_master_password,
    trust_mobile_device,
    verify_master_password,
    write_owner_guard,
)


def test_master_password_hash_and_verify(tmp_path: Path) -> None:
    state = set_master_password(tmp_path, "VeryStrongPassword123!")
    assert state["master_password_hash"]
    assert verify_master_password(tmp_path, "VeryStrongPassword123!") is True
    assert verify_master_password(tmp_path, "wrong-password") is False


def test_trust_and_revoke_mobile_device(tmp_path: Path) -> None:
    write_owner_guard(tmp_path, enabled=True, owner_user_id="conner")
    state = trust_mobile_device(tmp_path, "galaxy_s25_primary")
    assert "galaxy_s25_primary" in state["trusted_mobile_devices"]

    state2 = revoke_mobile_device(tmp_path, "galaxy_s25_primary")
    assert "galaxy_s25_primary" not in state2["trusted_mobile_devices"]
    final = read_owner_guard(tmp_path)
    assert final["enabled"] is True
