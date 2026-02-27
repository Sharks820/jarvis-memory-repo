"""Tests for jarvis_engine.security.forensic_logger."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


from jarvis_engine.security.forensic_logger import ForensicLogger, _ZERO_HASH


# ---------------------------------------------------------------
# Hash chain integrity
# ---------------------------------------------------------------


class TestHashChainIntegrity:
    def test_first_entry_has_zero_prev_hash(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"action": "test"})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        line = log_path.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        assert entry["prev_hash"] == _ZERO_HASH

    def test_chain_of_five_entries_verifies(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        for i in range(5):
            fl.log_event({"action": "test", "index": i})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        valid, count = ForensicLogger.verify_chain(log_path)
        assert valid is True
        assert count == 5

    def test_tampered_entry_breaks_chain(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        for i in range(5):
            fl.log_event({"action": "test", "index": i})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        # Tamper with the third line
        entry = json.loads(lines[2])
        entry["action"] = "TAMPERED"
        lines[2] = json.dumps(entry, separators=(",", ":"), sort_keys=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        valid, count = ForensicLogger.verify_chain(log_path)
        assert valid is False

    def test_single_entry_verifies(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"data": "single"})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        valid, count = ForensicLogger.verify_chain(log_path)
        assert valid is True
        assert count == 1

    def test_empty_file_verifies(self, tmp_path: Path) -> None:
        log_path = tmp_path / "empty.jsonl"
        log_path.write_text("", encoding="utf-8")
        valid, count = ForensicLogger.verify_chain(log_path)
        assert valid is True
        assert count == 0

    def test_nonexistent_file_verifies(self, tmp_path: Path) -> None:
        valid, count = ForensicLogger.verify_chain(tmp_path / "nope.jsonl")
        assert valid is True
        assert count == 0

    def test_prev_hash_links_correctly(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"a": 1})
        fl.log_event({"b": 2})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        first_hash = hashlib.sha256(lines[0].encode("utf-8")).hexdigest()
        second_entry = json.loads(lines[1])
        assert second_entry["prev_hash"] == first_hash


# ---------------------------------------------------------------
# Timestamp and fields
# ---------------------------------------------------------------


class TestEventFields:
    def test_timestamp_utc_present(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"severity": "HIGH"})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "timestamp_utc" in entry

    def test_custom_fields_preserved(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"severity": "LOW", "ip": "1.2.3.4", "category": "test"})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["severity"] == "LOW"
        assert entry["ip"] == "1.2.3.4"
        assert entry["category"] == "test"

    def test_entries_are_sorted_json(self, tmp_path: Path) -> None:
        """Keys should be sorted in the JSONL output for deterministic hashing."""
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"z_field": 1, "a_field": 2})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        line = log_path.read_text(encoding="utf-8").strip()
        keys = list(json.loads(line).keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------


class TestRotation:
    def test_no_rotation_under_threshold(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"action": "test"})
        fl.rotate_if_needed(max_bytes=10_000_000)
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        assert log_path.exists()
        assert not (tmp_path / "logs" / "forensic_log.jsonl.1").exists()

    def test_rotation_creates_dot_1(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        # Write enough to exceed a small threshold
        for i in range(100):
            fl.log_event({"index": i, "padding": "x" * 100})
        fl.rotate_if_needed(max_bytes=100)  # very low threshold
        rotated = tmp_path / "logs" / "forensic_log.jsonl.1"
        assert rotated.exists()

    def test_rotation_resets_hash_chain(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        for i in range(50):
            fl.log_event({"i": i, "pad": "x" * 200})
        fl.rotate_if_needed(max_bytes=100)
        # Write a new entry after rotation
        fl.log_event({"action": "fresh"})
        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        line = log_path.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        assert entry["prev_hash"] == _ZERO_HASH

    def test_rotation_shifts_existing_files(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        fl = ForensicLogger(log_dir)
        # Create initial content and rotate twice
        for i in range(50):
            fl.log_event({"i": i, "pad": "x" * 200})
        fl.rotate_if_needed(max_bytes=100)
        for i in range(50):
            fl.log_event({"i": i, "pad": "y" * 200})
        fl.rotate_if_needed(max_bytes=100)
        assert (log_dir / "forensic_log.jsonl.1").exists()
        assert (log_dir / "forensic_log.jsonl.2").exists()

    def test_nonexistent_file_rotation_noop(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        # Don't write anything, just try to rotate
        fl.rotate_if_needed(max_bytes=100)
        # No crash, no files created beyond the dir
        assert not (tmp_path / "logs" / "forensic_log.jsonl").exists()


# ---------------------------------------------------------------
# Recovery (resume hash chain after restart)
# ---------------------------------------------------------------


class TestRecovery:
    def test_resume_chain_after_restart(self, tmp_path: Path) -> None:
        """A new ForensicLogger instance should continue the hash chain."""
        fl1 = ForensicLogger(tmp_path / "logs")
        fl1.log_event({"msg": "first"})
        fl1.log_event({"msg": "second"})

        # Simulate restart
        fl2 = ForensicLogger(tmp_path / "logs")
        fl2.log_event({"msg": "third"})

        log_path = tmp_path / "logs" / "forensic_log.jsonl"
        valid, count = ForensicLogger.verify_chain(log_path)
        assert valid is True
        assert count == 3


# ---------------------------------------------------------------
# Export for law enforcement
# ---------------------------------------------------------------


class TestExport:
    def test_export_creates_zip(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"severity": "HIGH", "category": "injection"})
        out = tmp_path / "export.zip"
        fl.export_for_law_enforcement("2020-01-01", "2030-12-31", out)
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert "forensic_log.jsonl" in names
            assert "summary.txt" in names

    def test_export_filters_by_date(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"category": "a"})  # today's date
        out = tmp_path / "export.zip"
        # Use a date range that does NOT include today
        fl.export_for_law_enforcement("1999-01-01", "1999-12-31", out)
        with zipfile.ZipFile(out) as zf:
            content = zf.read("forensic_log.jsonl").decode("utf-8").strip()
            assert content == ""  # no entries in that range

    def test_export_includes_matching_entries(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"category": "test"})
        out = tmp_path / "export.zip"
        fl.export_for_law_enforcement("2020-01-01", "2030-12-31", out)
        with zipfile.ZipFile(out) as zf:
            content = zf.read("forensic_log.jsonl").decode("utf-8").strip()
            assert len(content) > 0
            entry = json.loads(content)
            assert entry["category"] == "test"

    def test_export_summary_has_stats(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        fl.log_event({"severity": "HIGH", "category": "injection"})
        fl.log_event({"severity": "LOW", "category": "scan"})
        out = tmp_path / "export.zip"
        fl.export_for_law_enforcement("2020-01-01", "2030-12-31", out)
        with zipfile.ZipFile(out) as zf:
            summary = zf.read("summary.txt").decode("utf-8")
            assert "Total entries: 2" in summary
            assert "injection" in summary
            assert "scan" in summary

    def test_export_empty_log(self, tmp_path: Path) -> None:
        fl = ForensicLogger(tmp_path / "logs")
        out = tmp_path / "export.zip"
        fl.export_for_law_enforcement("2020-01-01", "2030-12-31", out)
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            summary = zf.read("summary.txt").decode("utf-8")
            assert "Total entries: 0" in summary
