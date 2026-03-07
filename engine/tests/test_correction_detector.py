"""Tests for the correction detector module."""

from __future__ import annotations

import sqlite3
import threading
from unittest.mock import MagicMock

import pytest

from jarvis_engine.learning.correction_detector import (
    Correction,
    CorrectionDetector,
    _extract_keywords,
)


# ---------------------------------------------------------------------------
# Detection pattern tests
# ---------------------------------------------------------------------------


class TestCorrectionDetector:
    """Tests for CorrectionDetector.detect_correction."""

    def test_detect_actually_pattern(self):
        """'no, actually X' is detected as a correction."""
        detector = CorrectionDetector()

        result = detector.detect_correction("no, actually the capital is Berlin")
        assert result is not None
        assert result.new_claim == "the capital is Berlin"
        assert result.old_claim == ""
        assert result.confidence == 0.9

    def test_detect_actually_pattern_with_its(self):
        """'no, it's actually X' is detected as a correction."""
        detector = CorrectionDetector()

        result = detector.detect_correction("no, it's actually called Kubernetes")
        assert result is not None
        assert "Kubernetes" in result.new_claim

    def test_detect_thats_wrong_pattern(self):
        """'that's wrong, X' is detected as a correction."""
        detector = CorrectionDetector()

        result = detector.detect_correction(
            "that's wrong, the meeting is on Thursday"
        )
        assert result is not None
        assert "meeting is on Thursday" in result.new_claim

    def test_detect_thats_not_right_pattern(self):
        """'that's not right, X' is detected as a correction."""
        detector = CorrectionDetector()

        result = detector.detect_correction(
            "that's not right, it should be 42"
        )
        assert result is not None
        assert "42" in result.new_claim

    def test_detect_i_meant_pattern(self):
        """'I meant X' is detected as a correction."""
        detector = CorrectionDetector()

        result = detector.detect_correction("I meant the blue one")
        assert result is not None
        assert result.new_claim == "the blue one"

    def test_detect_what_i_meant_was(self):
        """'what I meant was X' is detected as a correction."""
        detector = CorrectionDetector()

        result = detector.detect_correction("what I meant was the Python version")
        assert result is not None
        assert "Python version" in result.new_claim

    def test_detect_not_x_but_y_pattern(self):
        """'not X but Y' is detected with both old and new claims."""
        detector = CorrectionDetector()

        result = detector.detect_correction("not Paris but London")
        assert result is not None
        assert result.old_claim == "Paris"
        assert result.new_claim == "London"

    def test_detect_not_x_its_y_pattern(self):
        """'not X, it's Y' is detected with both old and new claims."""
        detector = CorrectionDetector()

        result = detector.detect_correction("not Java, it's Kotlin")
        assert result is not None
        assert result.old_claim == "Java"
        assert result.new_claim == "Kotlin"

    def test_detect_confusing_x_with_y(self):
        """'you're confusing X with Y' is detected."""
        detector = CorrectionDetector()

        result = detector.detect_correction(
            "you're confusing the deadline with the start date"
        )
        assert result is not None
        assert "deadline" in result.old_claim
        assert "start date" in result.new_claim

    def test_detect_correction_prefix(self):
        """'correction: X' is detected."""
        detector = CorrectionDetector()

        result = detector.detect_correction("correction: the server runs on port 8080")
        assert result is not None
        assert "port 8080" in result.new_claim

    def test_detect_wrong_prefix(self):
        """'wrong, X' is detected."""
        detector = CorrectionDetector()

        result = detector.detect_correction("wrong, it uses PostgreSQL not MySQL")
        assert result is not None
        assert "PostgreSQL" in result.new_claim

    def test_detect_incorrect_prefix(self):
        """'incorrect, X' is detected."""
        detector = CorrectionDetector()

        result = detector.detect_correction("incorrect, the version is 3.12")
        assert result is not None
        assert "3.12" in result.new_claim

    def test_no_correction_in_normal_message(self):
        """Ordinary messages return None."""
        detector = CorrectionDetector()

        assert detector.detect_correction("What's the weather like today?") is None
        assert detector.detect_correction("Tell me about Python") is None
        assert detector.detect_correction("Schedule a meeting for Monday") is None
        assert detector.detect_correction("Thanks, that helps a lot") is None

    def test_no_correction_in_empty_message(self):
        """Empty or whitespace-only messages return None."""
        detector = CorrectionDetector()

        assert detector.detect_correction("") is None
        assert detector.detect_correction("   ") is None
        assert detector.detect_correction(None) is None

    # ------------------------------------------------------------------
    # KG application tests
    # ------------------------------------------------------------------

    def test_apply_correction_updates_kg(self):
        """apply_correction updates the matching fact in a mock KG."""
        # Build a minimal in-memory KG mock with real SQLite
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        write_lock = threading.Lock()
        db_lock = threading.Lock()

        db.executescript("""
            CREATE TABLE kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'fact',
                confidence REAL NOT NULL DEFAULT 0.5,
                locked INTEGER NOT NULL DEFAULT 0,
                locked_at TEXT DEFAULT NULL,
                locked_by TEXT DEFAULT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE kg_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source_record TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX idx_kg_edges_unique
                ON kg_edges(source_id, target_id, relation);
        """)

        # Insert a fact to be corrected
        db.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence) VALUES (?, ?, ?)",
            ("location.capital.france", "The capital of France is Lyon", 0.7),
        )
        db.commit()

        # Build mock KG that delegates to real SQLite for reads/writes
        mock_kg = MagicMock()
        mock_kg._db = db
        mock_kg._write_lock = write_lock
        mock_kg._db_lock = db_lock
        mock_kg.query_relevant_facts.return_value = [
            {
                "node_id": "location.capital.france",
                "label": "The capital of France is Lyon",
                "confidence": 0.7,
            }
        ]
        mock_kg.add_edge.return_value = True

        detector = CorrectionDetector(kg=mock_kg)
        correction = Correction(
            old_claim="capital of France is Lyon",
            new_claim="The capital of France is Paris",
            entity=None,
            confidence=0.9,
        )

        result = detector.apply_correction(correction)
        assert result is True

        # Verify the DB was updated
        row = db.execute(
            "SELECT label, confidence FROM kg_nodes WHERE node_id = ?",
            ("location.capital.france",),
        ).fetchone()
        assert row["label"] == "The capital of France is Paris"
        # max(0.7 + 0.1, 0.9) = 0.9
        assert row["confidence"] == pytest.approx(0.9)

        db.close()

    def test_apply_correction_boosts_high_confidence(self):
        """When existing confidence is already high, boost still applies."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        write_lock = threading.Lock()

        db.executescript("""
            CREATE TABLE kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                locked INTEGER NOT NULL DEFAULT 0,
                locked_at TEXT DEFAULT NULL,
                locked_by TEXT DEFAULT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]',
                node_type TEXT NOT NULL DEFAULT 'fact',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        db.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence) VALUES (?, ?, ?)",
            ("pref.color", "Favorite color is blue", 0.95),
        )
        db.commit()

        mock_kg = MagicMock()
        mock_kg._db = db
        mock_kg._write_lock = write_lock
        mock_kg.query_relevant_facts.return_value = [
            {"node_id": "pref.color", "label": "Favorite color is blue", "confidence": 0.95}
        ]

        detector = CorrectionDetector(kg=mock_kg)
        correction = Correction(
            old_claim="color is blue",
            new_claim="Favorite color is green",
            entity=None,
            confidence=0.9,
        )

        result = detector.apply_correction(correction)
        assert result is True

        row = db.execute(
            "SELECT label, confidence FROM kg_nodes WHERE node_id = ?",
            ("pref.color",),
        ).fetchone()
        assert row["label"] == "Favorite color is green"
        # min(max(0.95 + 0.1, 0.9), 1.0) = 1.0 (clamped)
        assert row["confidence"] == pytest.approx(1.0)

        db.close()

    def test_apply_correction_no_kg_returns_false(self):
        """apply_correction returns False when no KG is available."""
        detector = CorrectionDetector(kg=None)
        correction = Correction(
            old_claim="old thing",
            new_claim="new thing",
            entity=None,
            confidence=0.9,
        )

        result = detector.apply_correction(correction)
        assert result is False

    def test_apply_correction_no_matches_returns_false(self):
        """apply_correction returns False when KG has no matching facts."""
        mock_kg = MagicMock()
        mock_kg.query_relevant_facts.return_value = []

        detector = CorrectionDetector(kg=mock_kg)
        correction = Correction(
            old_claim="something totally unknown",
            new_claim="something else",
            entity=None,
            confidence=0.9,
        )

        result = detector.apply_correction(correction)
        assert result is False


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    """Tests for the _extract_keywords helper."""

    def test_extracts_meaningful_words(self):
        keywords = _extract_keywords("The capital of France is Paris")
        assert "capital" in keywords
        assert "france" in keywords
        assert "paris" in keywords

    def test_filters_stopwords(self):
        keywords = _extract_keywords("it is the best one for me")
        assert "best" in keywords
        assert "one" in keywords
        assert "is" not in keywords
        assert "the" not in keywords
        assert "for" not in keywords

    def test_empty_input(self):
        assert _extract_keywords("") == []
        assert _extract_keywords(None) == []
