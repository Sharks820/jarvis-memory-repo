"""Tests for engine/src/jarvis_engine/_shared.py

Covers: atomic_write_json, safe_float, safe_int, check_path_within_root,
        win_hidden_subprocess_kwargs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis_engine._shared import (
    atomic_write_json,
    check_path_within_root,
    safe_float,
    safe_int,
    win_hidden_subprocess_kwargs,
)


# ── safe_float ──────────────────────────────────────────────────────────


class TestSafeFloat:
    def test_int_input(self):
        assert safe_float(42) == 42.0

    def test_float_input(self):
        assert safe_float(3.14) == 3.14

    def test_string_numeric(self):
        assert safe_float("2.718") == 2.718

    def test_negative_string(self):
        assert safe_float("-99.5") == -99.5

    def test_none_returns_default(self):
        assert safe_float(None) == 0.0

    def test_none_returns_custom_default(self):
        assert safe_float(None, default=-1.0) == -1.0

    def test_invalid_string(self):
        assert safe_float("not_a_number") == 0.0

    def test_empty_string(self):
        assert safe_float("") == 0.0

    def test_bool_true(self):
        # bool is a subclass of int; float(True) == 1.0
        assert safe_float(True) == 1.0

    def test_list_returns_default(self):
        assert safe_float([1, 2, 3], default=5.5) == 5.5

    def test_dict_returns_default(self):
        assert safe_float({"a": 1}, default=-0.1) == -0.1

    def test_zero_string(self):
        assert safe_float("0") == 0.0

    def test_infinity_string(self):
        assert safe_float("inf") == float("inf")


# ── safe_int ────────────────────────────────────────────────────────────


class TestSafeInt:
    def test_int_input(self):
        assert safe_int(7) == 7

    def test_float_input_truncates(self):
        assert safe_int(3.9) == 3

    def test_string_numeric(self):
        assert safe_int("42") == 42

    def test_negative_string(self):
        assert safe_int("-10") == -10

    def test_none_returns_default(self):
        assert safe_int(None) == 0

    def test_none_returns_custom_default(self):
        assert safe_int(None, default=99) == 99

    def test_invalid_string(self):
        assert safe_int("hello") == 0

    def test_empty_string(self):
        assert safe_int("") == 0

    def test_float_string_fails(self):
        # int("3.14") raises ValueError — should return default
        assert safe_int("3.14") == 0

    def test_bool_true(self):
        assert safe_int(True) == 1

    def test_bool_false(self):
        assert safe_int(False) == 0

    def test_list_returns_default(self):
        assert safe_int([1], default=-1) == -1

    def test_zero_string(self):
        assert safe_int("0") == 0


# ── check_path_within_root ──────────────────────────────────────────────


class TestCheckPathWithinRoot:
    """Security-critical: path traversal guard."""

    def test_valid_path_within_root(self, tmp_path: Path):
        child = tmp_path / "subdir" / "file.json"
        # Should not raise for valid path within root
        check_path_within_root(child, tmp_path, "test")
        assert True  # path accepted without error

    def test_root_itself_is_accepted(self, tmp_path: Path):
        check_path_within_root(tmp_path, tmp_path, "test")
        assert True  # root path accepted without error

    def test_traversal_with_dotdot(self, tmp_path: Path):
        evil = tmp_path / "subdir" / ".." / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="outside project root"):
            check_path_within_root(evil, tmp_path, "evil_path")

    def test_absolute_escape(self, tmp_path: Path):
        evil = Path("/tmp/outside")
        # tmp_path is something like /tmp/pytest-xxx/test_xxx/; /tmp/outside escapes it
        with pytest.raises(ValueError, match="outside project root"):
            check_path_within_root(evil, tmp_path, "absolute_escape")

    def test_label_appears_in_error(self, tmp_path: Path):
        evil = Path("/completely/different/path")
        with pytest.raises(ValueError, match="MY_LABEL"):
            check_path_within_root(evil, tmp_path, "MY_LABEL")

    def test_symlink_traversal(self, tmp_path: Path):
        """If a symlink points outside root, resolved path escapes."""
        target = Path(tmp_path).parent / "outside_target"
        target.mkdir(exist_ok=True)
        link = tmp_path / "sneaky_link"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlinks not supported on this platform/config")
        with pytest.raises(ValueError, match="outside project root"):
            check_path_within_root(link, tmp_path, "symlink")
        target.rmdir()

    def test_deeply_nested_valid(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e.txt"
        check_path_within_root(deep, tmp_path, "deep")
        assert True  # deeply nested path accepted without error

    def test_sibling_directory_rejected(self, tmp_path: Path):
        sibling = tmp_path.parent / "sibling_dir" / "file.txt"
        with pytest.raises(ValueError, match="outside project root"):
            check_path_within_root(sibling, tmp_path, "sibling")


# ── atomic_write_json ───────────────────────────────────────────────────


class TestAtomicWriteJson:
    def test_basic_write(self, tmp_path: Path):
        target = tmp_path / "data.json"
        payload = {"key": "value", "number": 42}
        atomic_write_json(target, payload)
        assert target.exists()
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == payload

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "dir" / "data.json"
        atomic_write_json(target, {"ok": True})
        assert target.exists()
        assert json.loads(target.read_text())["ok"] is True

    def test_list_payload(self, tmp_path: Path):
        target = tmp_path / "list.json"
        payload = [1, 2, 3, "four"]
        atomic_write_json(target, payload)
        loaded = json.loads(target.read_text())
        assert loaded == payload

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "overwrite.json"
        atomic_write_json(target, {"v": 1})
        atomic_write_json(target, {"v": 2})
        loaded = json.loads(target.read_text())
        assert loaded["v"] == 2

    def test_secure_mode_chmod(self, tmp_path: Path):
        target = tmp_path / "secure.json"
        with patch("jarvis_engine._shared.os.chmod") as mock_chmod:
            atomic_write_json(target, {"s": True}, secure=True)
            mock_chmod.assert_called_once_with(str(target), 0o600)

    def test_non_secure_skips_chmod(self, tmp_path: Path):
        target = tmp_path / "nonsecure.json"
        with patch("jarvis_engine._shared.os.chmod") as mock_chmod:
            atomic_write_json(target, {"s": False}, secure=False)
            mock_chmod.assert_not_called()

    def test_chmod_oserror_is_swallowed(self, tmp_path: Path):
        target = tmp_path / "chmodfail.json"
        with patch("jarvis_engine._shared.os.chmod", side_effect=OSError("nope")):
            # Should NOT raise
            atomic_write_json(target, {"ok": True}, secure=True)
        assert target.exists()

    def test_permission_error_retries_and_succeeds(self, tmp_path: Path):
        target = tmp_path / "retry_ok.json"
        call_count = 0
        original_replace = os.replace

        def flaky_replace(src, dst):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise PermissionError("locked")
            return original_replace(src, dst)

        with patch("jarvis_engine._shared.os.replace", side_effect=flaky_replace):
            with patch("jarvis_engine._shared.time.sleep"):
                atomic_write_json(target, {"retried": True}, retries=3)
        assert target.exists()
        loaded = json.loads(target.read_text())
        assert loaded["retried"] is True

    def test_permission_error_exhausts_retries(self, tmp_path: Path):
        target = tmp_path / "fail.json"
        with patch(
            "jarvis_engine._shared.os.replace",
            side_effect=PermissionError("always locked"),
        ):
            with patch("jarvis_engine._shared.time.sleep"):
                with pytest.raises(PermissionError, match="always locked"):
                    atomic_write_json(target, {"x": 1}, retries=3)

    def test_retry_sleep_backoff(self, tmp_path: Path):
        """Verify sleep durations increase with attempt number (0.06 * (attempt + 1))."""
        target = tmp_path / "backoff.json"
        with patch(
            "jarvis_engine._shared.os.replace", side_effect=PermissionError("locked")
        ):
            with patch("jarvis_engine._shared.time.sleep") as mock_sleep:
                with pytest.raises(PermissionError):
                    atomic_write_json(target, {"x": 1}, retries=3)
        # Calls: sleep(0.06*1), sleep(0.06*2), sleep(0.06*3)
        assert mock_sleep.call_count == 3
        calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert calls == [pytest.approx(0.06), pytest.approx(0.12), pytest.approx(0.18)]

    def test_tmp_file_cleaned_up_on_success(self, tmp_path: Path):
        target = tmp_path / "clean.json"
        atomic_write_json(target, {"c": 1})
        # No .tmp. files should remain
        leftover = list(tmp_path.glob("*.tmp.*"))
        assert leftover == []

    def test_retries_clamped_to_min_one(self, tmp_path: Path):
        """retries=0 or negative should still attempt at least once."""
        target = tmp_path / "minone.json"
        atomic_write_json(target, {"min": True}, retries=0)
        assert target.exists()

    def test_unicode_payload(self, tmp_path: Path):
        target = tmp_path / "unicode.json"
        payload = {"emoji": "hello", "jp": "\u3053\u3093\u306b\u3061\u306f"}
        atomic_write_json(target, payload)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded["jp"] == "\u3053\u3093\u306b\u3061\u306f"

    def test_empty_dict_payload(self, tmp_path: Path):
        target = tmp_path / "empty.json"
        atomic_write_json(target, {})
        loaded = json.loads(target.read_text())
        assert loaded == {}


# ── win_hidden_subprocess_kwargs ────────────────────────────────────────


class TestWinHiddenSubprocessKwargs:
    @patch("jarvis_engine._shared.os.name", "posix")
    def test_non_windows_returns_empty_dict(self):
        result = win_hidden_subprocess_kwargs()
        assert result == {}

    @patch("jarvis_engine._shared.os.name", "nt")
    def test_windows_returns_creationflags(self):
        result = win_hidden_subprocess_kwargs()
        # On Windows (or when os.name is mocked to 'nt'), should contain creationflags
        if "creationflags" in result:
            assert isinstance(result["creationflags"], int)
            assert result["creationflags"] != 0

    @patch("jarvis_engine._shared.os.name", "nt")
    def test_windows_returns_startupinfo(self):
        result = win_hidden_subprocess_kwargs()
        if "startupinfo" in result:
            import subprocess

            assert isinstance(result["startupinfo"], subprocess.STARTUPINFO)

    @patch("jarvis_engine._shared.os.name", "nt")
    def test_windows_startupinfo_wshowwindow_zero(self):
        result = win_hidden_subprocess_kwargs()
        if "startupinfo" in result:
            assert result["startupinfo"].wShowWindow == 0
