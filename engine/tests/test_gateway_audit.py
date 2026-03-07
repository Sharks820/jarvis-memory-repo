"""Tests for engine/src/jarvis_engine/gateway/audit.py — GatewayAudit."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis_engine.gateway.audit import GatewayAudit, _MAX_AUDIT_LOG_BYTES


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_audit(tmp_path: Path) -> GatewayAudit:
    return GatewayAudit(tmp_path / "audit.jsonl")


def _log_one(audit: GatewayAudit, **overrides) -> None:
    """Log a single decision with sensible defaults."""
    defaults = dict(
        provider="anthropic",
        model="claude-sonnet",
        reason="default",
        latency_ms=120.5,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        success=True,
    )
    defaults.update(overrides)
    audit.log_decision(**defaults)


# ── log_decision() ──────────────────────────────────────────────────────────


class TestLogDecision:
    def test_creates_file_and_parent_dirs(self, tmp_path: Path) -> None:
        audit = GatewayAudit(tmp_path / "sub" / "dir" / "audit.jsonl")
        _log_one(audit)
        assert (tmp_path / "sub" / "dir" / "audit.jsonl").exists()

    def test_writes_valid_jsonl(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit)
        content = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["provider"] == "anthropic"
        assert record["model"] == "claude-sonnet"
        assert record["success"] is True

    def test_all_fields_present(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(
            audit,
            fallback_from="groq",
            privacy_routed=True,
        )
        record = json.loads(
            (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
        )
        expected_keys = {
            "ts",
            "provider",
            "model",
            "reason",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "success",
            "fallback_from",
            "privacy_routed",
        }
        assert set(record.keys()) == expected_keys

    def test_latency_rounded(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit, latency_ms=123.456789)
        record = json.loads(
            (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
        )
        assert record["latency_ms"] == 123.5

    def test_cost_rounded(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit, cost_usd=0.0001234567)
        record = json.loads(
            (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
        )
        assert record["cost_usd"] == 0.000123

    def test_multiple_records(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        for i in range(5):
            _log_one(audit, model=f"model-{i}")
        lines = (
            (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        )
        assert len(lines) == 5

    def test_os_error_logged_not_raised(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        with patch("builtins.open", side_effect=OSError("disk full")):
            _log_one(audit)  # should not raise
        # No file should have been written since open() failed
        audit_file = tmp_path / "audit.jsonl"
        assert not audit_file.exists() or audit_file.stat().st_size == 0


# ── rotation ────────────────────────────────────────────────────────────────


class TestRotation:
    def test_no_rotation_under_limit(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit)
        rotated = tmp_path / "audit.jsonl.1"
        assert not rotated.exists()

    def test_rotation_when_over_limit(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        # Pre-seed a file just over the rotation limit
        path.write_text("x" * (_MAX_AUDIT_LOG_BYTES + 1), encoding="utf-8")
        audit = GatewayAudit(path)
        _log_one(audit)
        rotated = tmp_path / "audit.jsonl.1"
        assert rotated.exists()
        # Original file should exist with only the new record
        content = path.read_text(encoding="utf-8").strip()
        assert len(content.splitlines()) == 1

    def test_rotation_replaces_existing_rotated_file(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        rotated = tmp_path / "audit.jsonl.1"
        rotated.write_text("old-rotation", encoding="utf-8")
        # Seed file over the limit
        path.write_text("x" * (_MAX_AUDIT_LOG_BYTES + 1), encoding="utf-8")
        audit = GatewayAudit(path)
        _log_one(audit)
        # Rotated file should be the oversized content, not "old-rotation"
        assert rotated.read_text(encoding="utf-8") != "old-rotation"

    def test_rotation_on_nonexistent_file(self, tmp_path: Path) -> None:
        """_rotate_if_needed on a missing file should not raise."""
        audit = _make_audit(tmp_path)
        audit._rotate_if_needed()  # file doesn't exist yet — no-op
        # Rotated file should not be created either
        assert not (tmp_path / "audit.jsonl.1").exists()


# ── recent() ────────────────────────────────────────────────────────────────


class TestRecent:
    def test_empty_file(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        assert audit.recent() == []

    def test_returns_last_n(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        for i in range(10):
            _log_one(audit, model=f"model-{i}")
        records = audit.recent(n=3)
        assert len(records) == 3
        assert records[0]["model"] == "model-7"
        assert records[2]["model"] == "model-9"

    def test_skips_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        path.write_text(
            '{"model":"good"}\nnot-valid-json\n{"model":"also-good"}\n',
            encoding="utf-8",
        )
        audit = GatewayAudit(path)
        records = audit.recent()
        assert len(records) == 2
        assert records[0]["model"] == "good"
        assert records[1]["model"] == "also-good"

    def test_recent_all(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        for i in range(5):
            _log_one(audit, model=f"m-{i}")
        records = audit.recent(n=100)
        assert len(records) == 5


# ── summary() ───────────────────────────────────────────────────────────────


class TestSummary:
    def test_empty(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        s = audit.summary(hours=24)
        assert s["total_decisions"] == 0
        assert s["total_cost_usd"] == 0.0
        assert s["avg_latency_ms"] == 0.0
        assert s["failure_count"] == 0
        assert s["failure_rate_pct"] == 0.0
        assert s["privacy_routed_count"] == 0

    def test_counts_providers(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit, provider="anthropic")
        _log_one(audit, provider="anthropic")
        _log_one(audit, provider="ollama")
        s = audit.summary(hours=1)
        assert s["provider_breakdown"]["anthropic"] == 2
        assert s["provider_breakdown"]["ollama"] == 1

    def test_counts_failures(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit, success=True)
        _log_one(audit, success=False)
        _log_one(audit, success=False)
        s = audit.summary(hours=1)
        assert s["failure_count"] == 2
        assert s["failure_rate_pct"] == pytest.approx(66.7)

    def test_counts_privacy_routed(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit, privacy_routed=True)
        _log_one(audit, privacy_routed=False)
        s = audit.summary(hours=1)
        assert s["privacy_routed_count"] == 1

    def test_total_cost_and_avg_latency(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        _log_one(audit, cost_usd=0.01, latency_ms=100.0)
        _log_one(audit, cost_usd=0.02, latency_ms=200.0)
        s = audit.summary(hours=1)
        assert s["total_cost_usd"] == pytest.approx(0.03)
        assert s["avg_latency_ms"] == pytest.approx(150.0)


# ── thread safety ───────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_log_decisions(self, tmp_path: Path) -> None:
        audit = _make_audit(tmp_path)
        errors: list[Exception] = []

        def _writer(n: int) -> None:
            try:
                for i in range(n):
                    _log_one(
                        audit, model=f"model-{threading.current_thread().name}-{i}"
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(15,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        records = audit.recent(n=100)
        assert len(records) == 60
