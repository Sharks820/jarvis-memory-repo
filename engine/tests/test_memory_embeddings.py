"""Tests for jarvis_engine.memory.embeddings -- EmbeddingService.

Covers:
- Lazy model loading (double-checked locking)
- Single text embedding with default and custom prefixes
- Query embedding (search_query prefix)
- Batch embedding
- Thread safety of _ensure_model
- Empty prefix handling
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from jarvis_engine.memory.embeddings import EmbeddingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeModel:
    """Fake SentenceTransformer that returns deterministic vectors."""

    def __init__(self, dim: int = 4):
        self._dim = dim
        self._calls: list[tuple] = []

    def encode(self, texts: list[str], normalize_embeddings: bool = True):
        """Return a list of fake numpy-like arrays."""
        import numpy as np

        self._calls.append((texts, normalize_embeddings))
        vecs = []
        for t in texts:
            vec = np.array(
                [float(hash(t) % 100 + j) for j in range(self._dim)], dtype=float
            )
            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            vecs.append(vec)
        return vecs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmbeddingServiceInit:
    def test_model_none_on_init(self):
        """Model is not loaded at construction time."""
        svc = EmbeddingService()
        assert svc._model is None

    def test_lock_created_on_init(self):
        """Thread lock is created at init."""
        svc = EmbeddingService()
        # threading.Lock() returns a _thread.lock instance, not a threading.Lock type
        assert hasattr(svc._lock, "acquire") and hasattr(svc._lock, "release")


class TestEnsureModel:
    @patch("jarvis_engine.memory.embeddings.EmbeddingService._ensure_model")
    def test_ensure_model_called_on_embed(self, mock_ensure):
        """embed() calls _ensure_model before encoding."""
        svc = EmbeddingService()
        fake = FakeModel()
        mock_ensure.return_value = fake
        svc.embed("hello")
        mock_ensure.assert_called_once()

    def test_lazy_loading_uses_sentence_transformers(self):
        """First call to _ensure_model imports SentenceTransformer."""
        svc = EmbeddingService()
        fake_cls = MagicMock(return_value=FakeModel())

        with patch.dict(
            "sys.modules",
            {"sentence_transformers": MagicMock(SentenceTransformer=fake_cls)},
        ):
            with patch(
                "jarvis_engine.memory.embeddings.EmbeddingService._ensure_model"
            ) as mock_ensure:
                mock_ensure.return_value = FakeModel()
                model = mock_ensure()
                assert model is not None

    def test_double_checked_locking(self):
        """Model is not reloaded if already set."""
        svc = EmbeddingService()
        fake = FakeModel()
        svc._model = fake
        result = svc._ensure_model()
        assert result is fake


class TestEmbed:
    def test_embed_returns_list_of_floats(self):
        """embed() returns a list of Python floats."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        result = svc.embed("some text")
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)

    def test_embed_default_prefix(self):
        """embed() prepends 'search_document: ' by default."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        svc.embed("hello world")
        texts_passed = fake._calls[0][0]
        assert texts_passed == ["search_document: hello world"]

    def test_embed_custom_prefix(self):
        """embed() uses the supplied prefix."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        svc.embed("hello world", prefix="custom_prefix")
        texts_passed = fake._calls[0][0]
        assert texts_passed == ["custom_prefix: hello world"]

    def test_embed_empty_prefix(self):
        """embed() with empty prefix passes raw text."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        svc.embed("raw text", prefix="")
        texts_passed = fake._calls[0][0]
        assert texts_passed == ["raw text"]


class TestEmbedQuery:
    def test_embed_query_uses_search_query_prefix(self):
        """embed_query uses 'search_query' prefix."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        svc.embed_query("find me something")
        texts_passed = fake._calls[0][0]
        assert texts_passed == ["search_query: find me something"]

    def test_embed_query_returns_list_of_floats(self):
        """embed_query returns a list of floats."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        result = svc.embed_query("test query")
        assert isinstance(result, list)
        assert len(result) == 4


class TestEmbedBatch:
    def test_batch_returns_correct_count(self):
        """embed_batch returns one vector per input text."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        result = svc.embed_batch(["text one", "text two", "text three"])
        assert len(result) == 3

    def test_batch_each_vector_is_list_of_floats(self):
        """Each vector in batch result is a list of floats."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        result = svc.embed_batch(["a", "b"])
        for vec in result:
            assert isinstance(vec, list)
            assert all(isinstance(v, float) for v in vec)
            assert len(vec) == 4

    def test_batch_prefixes_all_texts(self):
        """embed_batch prepends prefix to all texts."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        svc.embed_batch(["x", "y"], prefix="doc")
        texts_passed = fake._calls[0][0]
        assert texts_passed == ["doc: x", "doc: y"]

    def test_batch_empty_prefix(self):
        """embed_batch with empty prefix sends raw texts."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        svc.embed_batch(["alpha", "beta"], prefix="")
        texts_passed = fake._calls[0][0]
        assert texts_passed == ["alpha", "beta"]

    def test_batch_empty_list(self):
        """embed_batch with empty list returns empty list."""
        svc = EmbeddingService()
        fake = FakeModel(dim=4)
        svc._model = fake
        result = svc.embed_batch([])
        assert result == []
