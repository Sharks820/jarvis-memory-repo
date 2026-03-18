"""Tests for voice_extractors.py — phone/URL/weather extraction and text cleaning.

These functions were previously tested indirectly through voice_pipeline in
test_main_helpers.py.  This file tests them directly from their new home module
and covers additional edge cases.
"""
from __future__ import annotations

from jarvis_engine.voice.extractors import (
    PHONE_NUMBER_RE,
    URL_RE,
    _extract_first_phone_number,
    _extract_first_url,
    _extract_weather_location,
    _extract_web_query,
    _is_read_only_voice_request,
    escape_response,
    shorten_urls_for_speech,
)


# ===========================================================================
# _extract_first_phone_number
# ===========================================================================


class TestExtractFirstPhoneNumber:
    def test_international_format(self) -> None:
        assert _extract_first_phone_number("Call +14155551234 please") == "+14155551234"

    def test_no_number(self) -> None:
        assert _extract_first_phone_number("no number here") == ""

    def test_dashed_format(self) -> None:
        assert _extract_first_phone_number("dial 555-123-4567") == "555-123-4567"

    def test_parenthesized_area_code(self) -> None:
        # Regex captures from the first digit; leading '(' is excluded
        assert _extract_first_phone_number("call (555) 123-4567") == "555) 123-4567"

    def test_truncation_at_256_chars(self) -> None:
        """Phone number beyond 256 chars is not found."""
        assert _extract_first_phone_number("x" * 300 + "+14155551234") == ""

    def test_empty_string(self) -> None:
        assert _extract_first_phone_number("") == ""

    def test_short_number_ignored(self) -> None:
        """Numbers with fewer than 9 digits (including separators) are ignored."""
        assert _extract_first_phone_number("call 123") == ""

    def test_multiple_numbers_returns_first(self) -> None:
        result = _extract_first_phone_number("call 555-123-4567 or 555-987-6543")
        assert result == "555-123-4567"

    def test_phone_regex_pattern_exists(self) -> None:
        assert PHONE_NUMBER_RE is not None
        assert PHONE_NUMBER_RE.pattern


# ===========================================================================
# _extract_weather_location
# ===========================================================================


class TestExtractWeatherLocation:
    def test_weather_in_city(self) -> None:
        assert _extract_weather_location("weather in Austin, TX") == "Austin, TX"

    def test_weather_for_city(self) -> None:
        assert _extract_weather_location("weather for New York") == "New York"

    def test_forecast_at_city(self) -> None:
        assert _extract_weather_location("forecast at Chicago") == "Chicago"

    def test_strips_noise_words(self) -> None:
        loc = _extract_weather_location("weather today")
        assert "today" not in loc.lower().split()

    def test_no_location(self) -> None:
        """Text without weather keyword returns empty string."""
        assert _extract_weather_location("hello world") == ""

    def test_strips_trailing_punctuation(self) -> None:
        loc = _extract_weather_location("weather in Paris?")
        assert loc == "Paris"

    def test_truncation_at_120_chars(self) -> None:
        long_loc = "A" * 200
        loc = _extract_weather_location(f"weather in {long_loc}")
        assert len(loc) <= 120

    def test_forecast_for(self) -> None:
        assert _extract_weather_location("forecast for London") == "London"


# ===========================================================================
# _extract_web_query
# ===========================================================================


class TestExtractWebQuery:
    def test_search_web_for(self) -> None:
        result = _extract_web_query("search the web for python asyncio")
        assert "python" in result

    def test_research(self) -> None:
        result = _extract_web_query("research ML frameworks")
        assert "ml" in result.lower()

    def test_look_up(self) -> None:
        result = _extract_web_query("look up rust programming")
        assert "rust" in result

    def test_find_on_web(self) -> None:
        result = _extract_web_query("find on the web react hooks")
        assert "react" in result

    def test_strips_jarvis_prefix(self) -> None:
        result = _extract_web_query("jarvis, what is the weather?")
        assert "jarvis" not in result

    def test_truncation_at_260_chars(self) -> None:
        long_query = "x" * 500
        result = _extract_web_query(f"search the web for {long_query}")
        assert len(result) <= 260

    def test_fallthrough_returns_cleaned_input(self) -> None:
        result = _extract_web_query("some random text")
        assert result == "some random text"

    def test_strips_trailing_punctuation(self) -> None:
        result = _extract_web_query("search the web for python?")
        assert not result.endswith("?")


# ===========================================================================
# _extract_first_url
# ===========================================================================


class TestExtractFirstUrl:
    def test_https_url(self) -> None:
        assert _extract_first_url("go to https://example.com") == "https://example.com"

    def test_www_url_gets_https_prefix(self) -> None:
        assert _extract_first_url("visit www.google.com") == "https://www.google.com"

    def test_no_url(self) -> None:
        assert _extract_first_url("no url here") == ""

    def test_truncation_at_1200_chars(self) -> None:
        assert _extract_first_url("x" * 1300 + "https://late.com") == ""

    def test_strips_trailing_punctuation(self) -> None:
        result = _extract_first_url("visit https://example.com.")
        assert not result.endswith(".")

    def test_http_url(self) -> None:
        assert _extract_first_url("go to http://test.org/page") == "http://test.org/page"

    def test_url_with_path(self) -> None:
        result = _extract_first_url("open https://example.com/path/to/page")
        assert result == "https://example.com/path/to/page"

    def test_url_regex_pattern_exists(self) -> None:
        assert URL_RE is not None
        assert URL_RE.pattern

    def test_long_url_truncated_to_500(self) -> None:
        long_path = "a" * 600
        result = _extract_first_url(f"go to https://example.com/{long_path}")
        assert len(result) <= 500


# ===========================================================================
# escape_response
# ===========================================================================


class TestEscapeResponse:
    def test_escapes_newlines(self) -> None:
        assert escape_response("line1\nline2") == "line1\\nline2"

    def test_escapes_carriage_returns(self) -> None:
        assert escape_response("line1\rline2") == "line1\\rline2"

    def test_escapes_backslashes(self) -> None:
        assert escape_response("path\\to\\file") == "path\\\\to\\\\file"

    def test_empty_string(self) -> None:
        assert escape_response("") == ""

    def test_no_special_chars(self) -> None:
        assert escape_response("hello world") == "hello world"

    def test_combined_escaping(self) -> None:
        result = escape_response("a\\b\nc\rd")
        assert result == "a\\\\b\\nc\\rd"


# ===========================================================================
# shorten_urls_for_speech
# ===========================================================================


class TestShortenUrlsForSpeech:
    def test_replaces_https_url(self) -> None:
        result = shorten_urls_for_speech("Visit https://www.example.com/page today")
        assert "[example.com link]" in result
        assert "https://" not in result

    def test_replaces_www_url(self) -> None:
        result = shorten_urls_for_speech("Go to www.google.com now")
        assert "[google.com link]" in result

    def test_no_url_unchanged(self) -> None:
        text = "No URLs in this text"
        assert shorten_urls_for_speech(text) == text

    def test_multiple_urls(self) -> None:
        text = "See https://a.com and https://b.com"
        result = shorten_urls_for_speech(text)
        assert "[a.com link]" in result
        assert "[b.com link]" in result


# ===========================================================================
# _is_read_only_voice_request
# ===========================================================================


class TestIsReadOnlyVoiceRequest:
    def test_read_only_status(self) -> None:
        assert _is_read_only_voice_request("runtime status", execute=False, approve_privileged=False) is True

    def test_mutation_pause(self) -> None:
        assert _is_read_only_voice_request("pause daemon", execute=False, approve_privileged=False) is False

    def test_execute_flag_still_allows_read_only_status(self) -> None:
        assert _is_read_only_voice_request("runtime status", execute=True, approve_privileged=False) is True

    def test_approve_privileged_forces_non_readonly(self) -> None:
        assert _is_read_only_voice_request("runtime status", execute=False, approve_privileged=True) is False

    def test_bare_wake_word(self) -> None:
        assert _is_read_only_voice_request("jarvis", execute=False, approve_privileged=False) is True

    def test_hey_wake_word(self) -> None:
        assert _is_read_only_voice_request("hey jarvis", execute=False, approve_privileged=False) is True

    def test_conversational_fallthrough_default_deny(self) -> None:
        """Unrecognised input is not read-only (default-deny)."""
        assert _is_read_only_voice_request("what is the meaning of life", execute=False, approve_privileged=False) is False

    def test_weather_is_read_only(self) -> None:
        assert _is_read_only_voice_request("weather", execute=False, approve_privileged=False) is True

    def test_send_text_is_mutation(self) -> None:
        assert _is_read_only_voice_request("send text", execute=False, approve_privileged=False) is False

    def test_my_schedule_is_read_only(self) -> None:
        assert _is_read_only_voice_request("my schedule", execute=False, approve_privileged=False) is True

    def test_open_website_is_mutation(self) -> None:
        assert _is_read_only_voice_request("open website", execute=False, approve_privileged=False) is False

    def test_generate_code_is_mutation(self) -> None:
        assert _is_read_only_voice_request("generate code", execute=False, approve_privileged=False) is False

    def test_sentence_shaped_brain_status_is_read_only(self) -> None:
        assert _is_read_only_voice_request(
            "hey jarvis can you check how your memory is holding up today",
            execute=True,
            approve_privileged=False,
        ) is True

    def test_sentence_shaped_system_status_is_read_only(self) -> None:
        assert _is_read_only_voice_request(
            "jarvis are you still running okay right now",
            execute=True,
            approve_privileged=False,
        ) is True

    def test_sync_calendar_and_inbox_stays_mutating(self) -> None:
        assert _is_read_only_voice_request(
            "Jarvis, sync my calendar and inbox",
            execute=True,
            approve_privileged=False,
        ) is False
