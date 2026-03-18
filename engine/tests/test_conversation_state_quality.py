"""Tests for the garbled-text quality gate in conversation_state."""

from __future__ import annotations

import pytest

from jarvis_engine.memory.conversation_state import _is_garbled_text


# --- Garbled text should be detected ---


class TestGarbledDetection:
    """Verify that garbled / nonsensical outputs are caught."""

    def test_single_char_repetition(self) -> None:
        assert _is_garbled_text("bbbbbbbbbbbbbbaaaaaaa") is True

    def test_long_single_char(self) -> None:
        assert _is_garbled_text("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") is True

    def test_repeated_substring_pattern(self) -> None:
        assert _is_garbled_text("abcabcabcabcabcabcabcabcabcabcabc") is True

    def test_repeated_two_char_pattern(self) -> None:
        assert _is_garbled_text("ababababababababababababababababab") is True

    def test_low_unique_chars(self) -> None:
        # Only 2 unique chars in 60-char string → ratio < 0.05
        text = "a" * 30 + "b" * 30
        assert _is_garbled_text(text) is True

    def test_no_word_tokens(self) -> None:
        # Longer than 20 chars, no real words
        assert _is_garbled_text("1234567890!@#$%^&*()_+1234") is True

    def test_mixed_garbled_mostly_garbage(self) -> None:
        # One real word buried in garbage
        text = "bbbbbbbbbbbbbbbbbbbbbbbhellobbbbbbbbbbbbbbbbbbbbbb"
        assert _is_garbled_text(text) is True


# --- Legitimate text should pass ---


class TestLegitimateTextPasses:
    """Verify that normal content is not rejected."""

    def test_normal_english_sentence(self) -> None:
        assert _is_garbled_text("The weather today is sunny and warm.") is False

    def test_code_snippet(self) -> None:
        text = "def hello_world():\n    print('Hello, world!')"
        assert _is_garbled_text(text) is False

    def test_short_response_ok(self) -> None:
        assert _is_garbled_text("ok") is False

    def test_short_response_yes(self) -> None:
        assert _is_garbled_text("yes") is False

    def test_empty_string(self) -> None:
        assert _is_garbled_text("") is False

    def test_ten_char_boundary(self) -> None:
        # Exactly 10 chars → should pass (<=10 always accepted)
        assert _is_garbled_text("aaaaaaaaaa") is False

    def test_normal_paragraph(self) -> None:
        text = (
            "Jarvis is a local-first personal AI assistant. "
            "It uses a knowledge graph backed by SQLite and NetworkX. "
            "The desktop engine handles memory and intelligence routing."
        )
        assert _is_garbled_text(text) is False

    def test_technical_text_with_numbers(self) -> None:
        text = "Version 3.14.159 released on 2026-03-17 with 42 improvements."
        assert _is_garbled_text(text) is False

    def test_short_garbled_below_threshold(self) -> None:
        # 8 chars, even though looks garbled, short text always passes
        assert _is_garbled_text("bbbaaaab") is False

    def test_url_not_garbled(self) -> None:
        assert _is_garbled_text("https://example.com/path/to/resource?q=test&r=2") is False

    def test_json_not_garbled(self) -> None:
        text = '{"key":"value","key2":"value2","key3":"value3","key4":"v4"}'
        assert _is_garbled_text(text) is False

    def test_json_array_not_garbled(self) -> None:
        text = '[{"id":1,"name":"test"},{"id":2,"name":"other"}]'
        assert _is_garbled_text(text) is False

    def test_eleven_char_garbled_detected(self) -> None:
        # 11 chars, above threshold — garbled should be detected
        assert _is_garbled_text("aaaaaaaaaaa") is True

    def test_newline_heavy_text_not_garbled(self) -> None:
        text = "line one\nline two\nline three\nline four\nline five\n"
        assert _is_garbled_text(text) is False

    def test_broken_repeat_not_garbled(self) -> None:
        # Near-repeat but broken — should NOT be flagged
        text = "abcabcabcabcabcabcabcabcabcxyz normal text here"
        assert _is_garbled_text(text) is False

    def test_base64_in_context_not_garbled(self) -> None:
        # Base64 embedded in a real response should pass
        text = "The encoded token is dGVzdGluZzEyMzQ1Njc4OTAxMjM0NTY= for authentication."
        assert _is_garbled_text(text) is False
