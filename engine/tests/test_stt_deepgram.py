"""Tests for Deepgram Nova-3 STT backend (_try_deepgram)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np


def _reset_keyterms_cache():
    """Reset personal vocab caches so tests start with a clean state."""
    import jarvis_engine._shared as shared_mod
    shared_mod._personal_vocab_stripped_cache = None
    shared_mod._personal_vocab_raw_cache = None


# ---------------------------------------------------------------------------
# D1. test_load_keyterms -- reads personal_vocab.txt and returns terms
# ---------------------------------------------------------------------------

def test_load_keyterms():
    """_load_keyterms reads personal_vocab.txt and returns cleaned term list."""
    _reset_keyterms_cache()
    from jarvis_engine.stt import _load_keyterms

    terms = _load_keyterms()
    assert isinstance(terms, list)
    assert len(terms) > 0
    # Should include known terms from personal_vocab.txt
    assert "Conner" in terms
    assert "Jarvis" in terms
    assert "Ollama" in terms
    # Parenthetical annotations should be stripped
    for term in terms:
        assert "(" not in term, f"Parenthetical not stripped: {term}"
        assert ")" not in term, f"Parenthetical not stripped: {term}"
    _reset_keyterms_cache()


# ---------------------------------------------------------------------------
# D2. test_load_keyterms_caching -- file only read once
# ---------------------------------------------------------------------------

def test_load_keyterms_caching():
    """_load_keyterms caches results: second call returns same list without re-reading."""
    _reset_keyterms_cache()
    import jarvis_engine._shared as shared_mod
    from jarvis_engine.stt import _load_keyterms

    # First call: loads from file
    terms1 = _load_keyterms()
    assert shared_mod._personal_vocab_stripped_cache is not None

    # Second call: returns cached (same object from shared cache)
    terms2 = _load_keyterms()
    assert terms1 is terms2  # Same object (cached)
    _reset_keyterms_cache()


# ---------------------------------------------------------------------------
# D3. test_try_deepgram_no_api_key -- returns None immediately
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": ""}, clear=False)
def test_try_deepgram_no_api_key():
    """_try_deepgram returns None immediately when DEEPGRAM_API_KEY is not set."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)
    result = _try_deepgram(fake_audio, language="en")
    assert result is None


# ---------------------------------------------------------------------------
# D4. test_try_deepgram_import_error -- returns None when httpx missing
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_import_error():
    """_try_deepgram returns None when httpx import fails."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def _fail_httpx(name, *args, **kwargs):
        if name == "httpx":
            raise ImportError("No module named 'httpx'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fail_httpx):
        result = _try_deepgram(fake_audio, language="en")

    assert result is None


# ---------------------------------------------------------------------------
# D5. test_try_deepgram_success -- mock client, verify TranscriptionResult
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_success():
    """_try_deepgram returns TranscriptionResult with correct fields on success."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "channels": [{
                "alternatives": [{
                    "transcript": "Hello Conner",
                    "confidence": 0.98,
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.97},
                        {"word": "Conner", "start": 0.5, "end": 0.9, "confidence": 0.99},
                    ],
                }],
            }],
        },
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en")

    assert result is not None
    assert result.text == "Hello Conner"
    assert result.backend == "deepgram-nova3"
    assert result.confidence == 0.98
    assert result.language == "en"
    assert result.duration_seconds >= 0.0
    # Word-level segments should be populated
    assert result.segments is not None
    assert len(result.segments) == 2
    assert result.segments[0]["text"] == "Hello"
    assert result.segments[1]["text"] == "Conner"


# ---------------------------------------------------------------------------
# D6. test_try_deepgram_with_keyterms -- verify keyterms passed in params
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_with_keyterms():
    """_try_deepgram passes keyterms as 'keywords' query params."""
    _reset_keyterms_cache()
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "channels": [{
                "alternatives": [{
                    "transcript": "Jarvis brain status",
                    "confidence": 0.95,
                    "words": [],
                }],
            }],
        },
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        # Pass explicit keyterms
        result = _try_deepgram(
            fake_audio,
            language="en",
            keyterms=["Jarvis", "Conner", "brain status"],
        )

    assert result is not None
    assert result.text == "Jarvis brain status"

    # Verify the 'keywords' params were passed in the API call
    call_kwargs = mock_client.post.call_args
    params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", [])
    keyword_values = [v for k, v in params if k == "keywords"]
    assert "Jarvis:2.0" in keyword_values
    assert "Conner:2.0" in keyword_values
    assert "brain status:2.0" in keyword_values
    _reset_keyterms_cache()


# ---------------------------------------------------------------------------
# D7. test_try_deepgram_api_error -- returns None on error
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_api_error():
    """_try_deepgram returns None when the API returns an error or raises."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Test 1: Non-200 status code
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en")

    assert result is None

    # Test 2: Exception raised
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = OSError("Connection refused")
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en")

    assert result is None


# ---------------------------------------------------------------------------
# D8. test_try_deepgram_with_numpy_audio -- verify WAV conversion
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_with_numpy_audio():
    """_try_deepgram converts numpy audio to WAV bytes before API call."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.random.randn(16000).astype(np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "channels": [{
                "alternatives": [{
                    "transcript": "testing audio",
                    "confidence": 0.92,
                    "words": [],
                }],
            }],
        },
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en", keyterms=[])

    assert result is not None
    assert result.text == "testing audio"

    # Verify the audio was sent as content (WAV bytes)
    call_kwargs = mock_client.post.call_args
    content = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content", b"")
    # WAV bytes should start with RIFF header
    assert content[:4] == b"RIFF"
    assert content[8:12] == b"WAVE"

    # Content-Type header should be audio/wav
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
    assert headers.get("Content-Type") == "audio/wav"


# ---------------------------------------------------------------------------
# D9. RC-4: Deepgram API params include utterances, endpointing, numerals
# ---------------------------------------------------------------------------

def test_deepgram_params_include_required_fields():
    """_build_deepgram_params includes utterances, endpointing=300, numerals=true."""
    from jarvis_engine.stt_backends import _build_deepgram_params

    params = _build_deepgram_params("en", keyterms=[])
    param_dict = {k: v for k, v in params}

    assert param_dict["utterances"] == "true"
    assert param_dict["endpointing"] == "300"
    assert param_dict["filler_words"] == "false"
    assert param_dict["numerals"] == "true"


# ---------------------------------------------------------------------------
# D10. RC-4: Keyword boost uses float intensity (2.0 not 2)
# ---------------------------------------------------------------------------

def test_deepgram_keyword_boost_uses_float():
    """Keywords include float intensity boost (e.g., 'term:2.0')."""
    from jarvis_engine.stt_backends import _build_deepgram_params

    params = _build_deepgram_params("en", keyterms=["Conner", "Jarvis"])
    keyword_values = [v for k, v in params if k == "keywords"]

    assert "Conner:2.0" in keyword_values
    assert "Jarvis:2.0" in keyword_values
    # Ensure no integer-only boost format
    assert "Conner:2" not in keyword_values
    assert "Jarvis:2" not in keyword_values


# ---------------------------------------------------------------------------
# D11. RC-5: Personal vocabulary has at least 50 entries
# ---------------------------------------------------------------------------

def test_personal_vocab_has_minimum_entries():
    """personal_vocab.txt contains at least 50 entries after expansion."""
    _reset_keyterms_cache()
    from jarvis_engine.stt import _load_keyterms

    terms = _load_keyterms()
    assert len(terms) >= 50, f"Expected >= 50 vocab entries, got {len(terms)}"
    _reset_keyterms_cache()


# ---------------------------------------------------------------------------
# D12. RC-5: Personal vocabulary includes tech terms
# ---------------------------------------------------------------------------

def test_personal_vocab_includes_tech_terms():
    """personal_vocab.txt includes common tech terms after expansion."""
    _reset_keyterms_cache()
    from jarvis_engine.stt import _load_keyterms

    terms = _load_keyterms()
    expected_tech = ["API", "GitHub", "Python", "Docker", "Kubernetes", "PyTorch"]
    for term in expected_tech:
        assert term in terms, f"Missing tech term: {term}"
    _reset_keyterms_cache()
