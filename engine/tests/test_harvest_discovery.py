"""Tests for harvest_discovery.py — topic extraction, dedup, and SQL constants.

Covers _extract_topic_phrases, _get_recently_harvested_topics, and the
named SQL constants used by _discover_harvest_topics in daemon_loop.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.harvest_discovery import (
    _SQL_NODE_BY_RELATION,
    _SQL_RARE_RELATIONS,
    _SQL_RECENT_SUMMARIES,
    _SQL_SPARSE_NODES,
    _SQL_STRONG_LABELS,
    _extract_topic_phrases,
    _get_recently_harvested_topics,
)


# ===========================================================================
# SQL constants sanity checks
# ===========================================================================


class TestSQLConstants:
    def test_recent_summaries_query(self) -> None:
        assert "records" in _SQL_RECENT_SUMMARIES
        assert "summary" in _SQL_RECENT_SUMMARIES
        assert "LIMIT" in _SQL_RECENT_SUMMARIES

    def test_sparse_nodes_query(self) -> None:
        assert "kg_nodes" in _SQL_SPARSE_NODES
        assert "kg_edges" in _SQL_SPARSE_NODES
        assert "HAVING" in _SQL_SPARSE_NODES

    def test_rare_relations_query(self) -> None:
        assert "kg_edges" in _SQL_RARE_RELATIONS
        assert "relation" in _SQL_RARE_RELATIONS
        assert "GROUP BY" in _SQL_RARE_RELATIONS

    def test_node_by_relation_query(self) -> None:
        assert "kg_nodes" in _SQL_NODE_BY_RELATION
        assert "kg_edges" in _SQL_NODE_BY_RELATION
        assert "?" in _SQL_NODE_BY_RELATION  # parameterized

    def test_strong_labels_query(self) -> None:
        assert "kg_nodes" in _SQL_STRONG_LABELS
        assert "confidence" in _SQL_STRONG_LABELS
        assert "0.5" in _SQL_STRONG_LABELS


# ===========================================================================
# _extract_topic_phrases
# ===========================================================================


class TestExtractTopicPhrases:
    def test_extracts_multi_word_phrases(self) -> None:
        text = "Machine learning and deep neural networks"
        phrases = _extract_topic_phrases(text)
        assert len(phrases) >= 1
        # Should contain meaningful multi-word phrase
        assert any(len(p.split()) >= 2 for p in phrases)

    def test_filters_stop_words(self) -> None:
        text = "the weather is very nice today"
        phrases = _extract_topic_phrases(text)
        # "the", "is", "very" are stop words — should be filtered
        for phrase in phrases:
            words = phrase.lower().split()
            assert "the" not in words
            assert "is" not in words

    def test_empty_string(self) -> None:
        assert _extract_topic_phrases("") == []

    def test_single_word_no_phrases(self) -> None:
        """Single words don't produce phrases (need 2+ meaningful words)."""
        phrases = _extract_topic_phrases("hello")
        assert phrases == []

    def test_punctuation_splits_fragments(self) -> None:
        text = "Python programming! Data science; Machine learning"
        phrases = _extract_topic_phrases(text)
        assert len(phrases) >= 1

    def test_deduplication(self) -> None:
        text = "Python programming. python programming"
        phrases = _extract_topic_phrases(text)
        # Should deduplicate case-insensitively
        lower_phrases = [p.lower() for p in phrases]
        assert len(lower_phrases) == len(set(lower_phrases))

    def test_max_five_words_per_phrase(self) -> None:
        text = "very long topic about advanced machine learning algorithms implementation details"
        phrases = _extract_topic_phrases(text)
        for phrase in phrases:
            assert len(phrase.split()) <= 5

    def test_short_tokens_filtered(self) -> None:
        """Single-char tokens are filtered out."""
        text = "a b c programming language"
        phrases = _extract_topic_phrases(text)
        for phrase in phrases:
            for word in phrase.split():
                assert len(word) > 1

    def test_multiple_sentences(self) -> None:
        text = "Natural language processing is great. Computer vision applications."
        phrases = _extract_topic_phrases(text)
        assert len(phrases) >= 1

    def test_delimiters_handled(self) -> None:
        """Various delimiters (pipes, brackets, etc.) split text."""
        text = "topic alpha | topic beta [topic gamma]"
        phrases = _extract_topic_phrases(text)
        assert isinstance(phrases, list)


# ===========================================================================
# _get_recently_harvested_topics
# ===========================================================================


class TestGetRecentlyHarvestedTopics:
    def test_returns_set(self, tmp_path: Path) -> None:
        result = _get_recently_harvested_topics(tmp_path)
        assert isinstance(result, set)

    def test_returns_empty_when_no_db(self, tmp_path: Path) -> None:
        """When activity feed DB does not exist, returns empty set."""
        result = _get_recently_harvested_topics(tmp_path)
        assert result == set()

    def test_extracts_topics_from_feed(self, tmp_path: Path) -> None:
        """Reads HARVEST events and extracts topic names."""
        mock_event = MagicMock()
        mock_event.details = {"topics": ["machine learning", "Python"]}
        mock_event.summary = "Auto-harvest: Python basics"

        mock_feed = MagicMock()
        mock_feed.query.return_value = [mock_event]

        # Create the DB path so the exists() check passes
        db_dir = tmp_path / ".planning" / "brain"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "activity_feed.db").write_text("")

        with patch("jarvis_engine.activity_feed.ActivityFeed", return_value=mock_feed), \
             patch("jarvis_engine.activity_feed.ActivityCategory") as mock_cat:
            mock_cat.HARVEST = "HARVEST"
            result = _get_recently_harvested_topics(tmp_path)

        assert "machine learning" in result
        assert "python" in result
        assert "auto-harvest: python basics" in result

    def test_handles_import_error(self, tmp_path: Path) -> None:
        """Gracefully handles missing activity_feed module."""
        db_dir = tmp_path / ".planning" / "brain"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "activity_feed.db").write_text("")

        with patch("jarvis_engine.activity_feed.ActivityFeed", side_effect=ImportError("no module")):
            result = _get_recently_harvested_topics(tmp_path)

        assert result == set()

    def test_handles_sqlite_error(self, tmp_path: Path) -> None:
        """Gracefully handles database errors."""
        import sqlite3

        db_dir = tmp_path / ".planning" / "brain"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "activity_feed.db").write_text("")

        with patch("jarvis_engine.activity_feed.ActivityFeed", side_effect=sqlite3.Error("db locked")):
            result = _get_recently_harvested_topics(tmp_path)

        assert result == set()

    def test_topics_lowercased(self, tmp_path: Path) -> None:
        """All topic strings are lowercased for comparison."""
        mock_event = MagicMock()
        mock_event.details = {"topics": ["UPPERCASE Topic"]}
        mock_event.summary = ""

        mock_feed = MagicMock()
        mock_feed.query.return_value = [mock_event]

        db_dir = tmp_path / ".planning" / "brain"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "activity_feed.db").write_text("")

        with patch("jarvis_engine.activity_feed.ActivityFeed", return_value=mock_feed), \
             patch("jarvis_engine.activity_feed.ActivityCategory") as mock_cat:
            mock_cat.HARVEST = "HARVEST"
            result = _get_recently_harvested_topics(tmp_path)

        assert "uppercase topic" in result
        assert "UPPERCASE Topic" not in result
