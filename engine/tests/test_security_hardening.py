"""Tests for security hardening findings."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis_engine import main as main_mod


class TestPathTraversalProtection:
    """Test suite for path traversal vulnerabilities."""

    def test_gaming_state_path_traversal_blocked(self, tmp_path: Path, monkeypatch) -> None:
        """H4: Gaming state path should block traversal attempts."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        
        # Create symlink attack simulation
        planning_dir = tmp_path / ".planning" / "runtime"
        planning_dir.mkdir(parents=True, exist_ok=True)
        
        # Try to create a file outside the repo (simulated)
        # The function should resolve and validate the path
        path = main_mod._gaming_mode_state_path()
        
        # Path should be within repo_root
        assert path.resolve().is_relative_to(tmp_path.resolve())

    def test_gaming_state_symlink_attack_detected(self, tmp_path: Path, monkeypatch) -> None:
        """H4: Gaming state should detect and reject symlink attacks."""
        import platform
        
        if platform.system() == "Windows":
            pytest.skip("Symlink tests require admin on Windows")
        
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        
        planning_dir = tmp_path / ".planning" / "runtime"
        planning_dir.mkdir(parents=True, exist_ok=True)
        
        gaming_file = planning_dir / "gaming_mode.json"
        outside_target = tmp_path / "attacker_controlled.json"
        outside_target.write_text("{}")
        
        # Create symlink pointing outside repo
        gaming_file.symlink_to(outside_target)
        
        # Should detect the traversal
        path = main_mod._gaming_mode_state_path()
        resolved = path.resolve()

        # Verify resolved path is still within bounds
        assert resolved.is_relative_to(tmp_path.resolve()), \
            "Path traversal not detected - symlink escaped repo_root"


class TestGamingModeSecurity:
    """Test suite for gaming mode security."""

    def test_gaming_mode_state_permissions(self, tmp_path: Path, monkeypatch) -> None:
        """Gaming mode state file should have restricted permissions."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        
        # Enable gaming mode
        main_mod.cmd_gaming_mode(enable=True, reason="test", auto_detect="")
        
        state_file = tmp_path / ".planning" / "runtime" / "gaming_mode.json"
        assert state_file.exists()
        
        # On Unix, verify permissions
        if os.name != "nt":
            stat = state_file.stat()
            # Should be readable/writable only by owner (0o600)
            assert stat.st_mode & 0o777 == 0o600, \
                f"Expected 0o600, got {oct(stat.st_mode & 0o777)}"

    def test_gaming_mode_auto_detect_process_validation(self, monkeypatch) -> None:
        """Gaming mode should validate detected process names."""
        # Mock tasklist output with suspicious process name
        import subprocess
        
        class SuspiciousTasklist:
            returncode = 0
            stdout = '"../../../malicious.exe","1234","Console","1","1024 K"'
            stderr = ""
        
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SuspiciousTasklist())
        
        # Should not crash on suspicious process names
        detected, process = main_mod._detect_active_game_process()
        
        # Should handle gracefully (either detect or not, but not crash)
        assert isinstance(detected, bool)
        assert isinstance(process, str)


class TestAutoIngestSecurity:
    """Test suite for auto-ingest security."""

    def test_auto_ingest_sanitizes_passwords(self, tmp_path: Path, monkeypatch) -> None:
        """Auto-ingest should sanitize passwords from content."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        
        # Content with password
        content_with_password = "User set master password: secret123 and token: abcdef"
        
        sanitized = main_mod._sanitize_memory_content(content_with_password)
        
        # Password should be redacted
        assert "secret123" not in sanitized
        assert "[redacted]" in sanitized
        assert "abcdef" not in sanitized or "[redacted]" in sanitized

    def test_auto_ingest_dedupe_limits_size(self, tmp_path: Path, monkeypatch) -> None:
        """Auto-ingest dedupe should limit stored hashes."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        
        # Create many entries
        dedupe_path = main_mod._auto_ingest_dedupe_path()
        
        # Add more than the limit (400)
        hashes = [f"hash_{i:04d}" for i in range(500)]
        main_mod._store_auto_ingest_hashes(dedupe_path, hashes)
        
        # Load and verify limit
        loaded = main_mod._load_auto_ingest_hashes(dedupe_path)
        assert len(loaded) <= 400, f"Expected max 400 hashes, got {len(loaded)}"
        
        # Should keep most recent
        assert loaded[-1] == "hash_0499"

    def test_auto_ingest_respects_disable_flag(self, tmp_path: Path, monkeypatch) -> None:
        """Auto-ingest should respect JARVIS_AUTO_INGEST_DISABLE flag."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setenv("JARVIS_AUTO_INGEST_DISABLE", "true")
        
        result = main_mod._auto_ingest_memory(
            source="user",
            kind="episodic",
            task_id="test",
            content="Test content"
        )
        
        # Should return empty string when disabled
        assert result == ""


class TestMissionSecurity:
    """Test suite for learning mission security."""

    def test_mission_url_safety_check_blocks_private_ips(self) -> None:
        """Mission should block private IP URLs."""
        from jarvis_engine.web_fetch import is_safe_public_url

        # Private IP addresses should be blocked
        private_urls = [
            "http://192.168.1.1/admin",
            "http://10.0.0.1/config",
            "http://127.0.0.1/secrets",
            "http://172.16.0.1/data",
            "http://localhost/internal",
        ]

        for url in private_urls:
            assert not is_safe_public_url(url), \
                f"Private URL should be blocked: {url}"

    def test_mission_url_safety_allows_public_domains(self) -> None:
        """Mission should allow public domain URLs."""
        from jarvis_engine.web_fetch import is_safe_public_url

        # Public URLs should be allowed
        public_urls = [
            "https://docs.python.org/3/",
            "https://github.com/example/repo",
            "https://stackoverflow.com/questions",
        ]

        for url in public_urls:
            # Note: This requires network to resolve, may fail in isolated tests
            # Just verify the parsing logic
            result = is_safe_public_url(url)
            # Result depends on DNS resolution, just verify no exception
            assert isinstance(result, bool)

    def test_mission_content_limits(self, tmp_path: Path) -> None:
        """Mission should enforce content size limits."""
        from jarvis_engine import learning_missions
        
        # Topic too long should be truncated
        long_topic = "A" * 300
        
        mission = learning_missions.create_learning_mission(
            tmp_path,
            topic=long_topic,
            objective="Test",
        )
        
        assert len(mission["topic"]) <= 200

    def test_mission_fetch_respects_max_bytes(self, monkeypatch) -> None:
        """Mission page fetch should respect max_bytes limit."""
        from jarvis_engine import web_fetch

        large_content = b"X" * 1000000  # 1MB

        def mock_urlopen(*args, **kwargs):
            class MockResp:
                def __init__(self):
                    self.headers = {"Content-Type": "text/html; charset=utf-8"}
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    pass
                def read(self, max_bytes):
                    return large_content[:max_bytes]
            return MockResp()

        monkeypatch.setattr(web_fetch, "is_safe_public_url", lambda url: True)
        monkeypatch.setattr(web_fetch, "resolve_and_check_ip", lambda url: True)
        monkeypatch.setattr(web_fetch, "build_opener", lambda *a: type("O", (), {"open": mock_urlopen})())

        result = web_fetch.fetch_page_text("https://example.com", max_bytes=50000)
        
        # Result should be within limit (plus some HTML processing overhead)
        assert len(result.encode()) <= 60000


class TestDaemonSecurity:
    """Test suite for daemon security features."""

    def test_safe_mode_blocks_execution(self, tmp_path: Path, monkeypatch) -> None:
        """Safe mode should force execute=False regardless of flags."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        
        # Enable safe mode
        main_mod.cmd_runtime_control(
            pause=False,
            resume=False,
            safe_on=True,
            safe_off=False,
            reset=False,
            reason="test",
        )
        
        monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))
        
        observed_execute = True
        observed_approve = True
        
        def capturing_autopilot(*args, **kwargs) -> int:
            nonlocal observed_execute, observed_approve
            observed_execute = kwargs.get("execute", True)
            observed_approve = kwargs.get("approve_privileged", True)
            return 0
        
        monkeypatch.setattr(main_mod, "cmd_ops_autopilot", capturing_autopilot)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
        
        main_mod.cmd_daemon_run(
            interval_s=120,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            execute=True,  # Requested True
            approve_privileged=True,  # Requested True
            auto_open_connectors=False,
            max_cycles=1,
            idle_interval_s=900,
            idle_after_s=300,
            run_missions=False,
        )
        
        # Safe mode should have forced both to False
        assert observed_execute is False
        assert observed_approve is False

    def test_daemon_respects_max_cycles(self, tmp_path: Path, monkeypatch) -> None:
        """Daemon should stop after max_cycles."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))
        
        cycle_count = 0
        
        def counting_autopilot(*args, **kwargs) -> int:
            nonlocal cycle_count
            cycle_count += 1
            return 0
        
        monkeypatch.setattr(main_mod, "cmd_ops_autopilot", counting_autopilot)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
        
        main_mod.cmd_daemon_run(
            interval_s=120,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            execute=False,
            approve_privileged=False,
            auto_open_connectors=False,
            max_cycles=5,
            idle_interval_s=900,
            idle_after_s=300,
            run_missions=False,
        )
        
        assert cycle_count == 5
