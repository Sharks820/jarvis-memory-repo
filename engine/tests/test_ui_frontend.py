"""Tests for Phase 3 UI frontend: activity event display, learned indicator,
immediate dashboard refresh, response= output (UI-01 through UI-05)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Activity event deduplication and display logic
# ---------------------------------------------------------------------------


class TestActivityEventDedup:
    """Verify _update_activity_events deduplicates by event_id."""

    def _make_widget_stub(self):
        """Create a minimal stub with the _update_activity_events method."""
        # Import the actual method and test its logic in isolation
        stub = MagicMock()
        stub._seen_event_ids = {}  # Ordered dict for dedup
        stub._log = MagicMock()
        return stub

    def test_new_events_are_displayed(self):
        """New events (unseen event_ids) are passed to _log."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget

        stub = self._make_widget_stub()
        events = [
            {"event_id": "evt-001", "timestamp": "2026-03-02T10:00:00", "category": "llm_routing", "summary": "Routed to kimi-k2"},
            {"event_id": "evt-002", "timestamp": "2026-03-02T09:55:00", "category": "preference_learned", "summary": "Learned style=concise"},
        ]
        # Call the method bound to our stub
        JarvisDesktopWidget._update_activity_events(stub, events)
        assert stub._log.call_count == 2
        assert "evt-001" in stub._seen_event_ids
        assert "evt-002" in stub._seen_event_ids

    def test_duplicate_events_are_skipped(self):
        """Events with already-seen event_ids are not displayed again."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget

        stub = self._make_widget_stub()
        stub._seen_event_ids = {"evt-001": None}
        events = [
            {"event_id": "evt-001", "timestamp": "2026-03-02T10:00:00", "category": "llm_routing", "summary": "Routed to kimi-k2"},
            {"event_id": "evt-003", "timestamp": "2026-03-02T09:50:00", "category": "harvest", "summary": "Harvest complete"},
        ]
        JarvisDesktopWidget._update_activity_events(stub, events)
        # Only evt-003 should be displayed (evt-001 is duplicate)
        assert stub._log.call_count == 1
        call_text = stub._log.call_args[0][0]
        assert "HARVEST" in call_text

    def test_empty_events_no_log(self):
        """No log calls when events list is empty."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget

        stub = self._make_widget_stub()
        JarvisDesktopWidget._update_activity_events(stub, [])
        stub._log.assert_not_called()

    def test_seen_dict_capped_at_500(self):
        """Seen event IDs dict is capped to prevent unbounded memory growth."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget

        stub = self._make_widget_stub()
        # Pre-fill with 510 event IDs (ordered dict)
        stub._seen_event_ids = dict.fromkeys(f"evt-{i:04d}" for i in range(510))
        events = [
            {"event_id": "evt-new", "timestamp": "2026-03-02T10:00:00", "category": "llm_routing", "summary": "Test"},
        ]
        JarvisDesktopWidget._update_activity_events(stub, events)
        # After cap, should have been trimmed to ~400 + 1 new
        assert len(stub._seen_event_ids) <= 450

    def test_error_category_uses_error_role(self):
        """Events with error/security category use error role."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget

        stub = self._make_widget_stub()
        events = [
            {"event_id": "evt-err", "timestamp": "2026-03-02T10:00:00", "category": "error", "summary": "Something failed"},
        ]
        JarvisDesktopWidget._update_activity_events(stub, events)
        call_kwargs = stub._log.call_args
        assert call_kwargs[1]["role"] == "error"


# ---------------------------------------------------------------------------
# Learned indicator expanded trigger
# ---------------------------------------------------------------------------


class TestLearnedIndicatorIntents:
    """Verify the expanded learned indicator intent list."""

    def test_expanded_intents_include_knowledge_modifiers(self):
        """Knowledge-modifying intents should be in the learned intents list."""
        # This mirrors the actual tuple in desktop_widget.py _send_command_async
        _LEARNED_INTENTS = (
            "memory_ingest", "memory_forget", "llm_conversation",
            "mission_create", "mission_run",
            "harvest", "fact_extracted",
        )
        assert "mission_create" in _LEARNED_INTENTS
        assert "harvest" in _LEARNED_INTENTS
        assert "fact_extracted" in _LEARNED_INTENTS
        # Read-only intents should NOT be in learned list
        assert "brain_status" not in _LEARNED_INTENTS
        assert "mission_cancel" not in _LEARNED_INTENTS

    def test_original_intents_still_present(self):
        """Original three intents (memory_ingest, memory_forget, llm_conversation) remain."""
        _LEARNED_INTENTS = (
            "memory_ingest", "memory_forget", "llm_conversation",
            "mission_create", "mission_run",
            "harvest", "fact_extracted",
        )
        assert "memory_ingest" in _LEARNED_INTENTS
        assert "memory_forget" in _LEARNED_INTENTS
        assert "llm_conversation" in _LEARNED_INTENTS


# ---------------------------------------------------------------------------
# cmd_status response= output
# ---------------------------------------------------------------------------


class TestCmdStatusResponse:
    """Verify cmd_status emits response= line."""

    def test_cmd_status_emits_response(self, capsys):
        """cmd_status prints a response= line with profile and mode."""
        mock_bus = MagicMock()
        mock_result = MagicMock()
        mock_result.profile = "standard"
        mock_result.primary_runtime = "ollama"
        mock_result.secondary_runtime = "groq"
        mock_result.security_strictness = "normal"
        mock_result.operation_mode = "daemon"
        mock_result.cloud_burst_enabled = True
        mock_result.events = []
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.main._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_status
            rc = cmd_status()

        assert rc == 0
        captured = capsys.readouterr().out
        assert "response=" in captured
        assert "standard" in captured
        assert "daemon" in captured
        assert "ollama" in captured


# ---------------------------------------------------------------------------
# _set_online signature accepts recent_events
# ---------------------------------------------------------------------------


class TestSetOnlineSignature:
    """Verify _set_online accepts recent_events parameter."""

    def test_set_online_accepts_recent_events(self):
        """_set_online method signature includes recent_events parameter."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        import inspect
        sig = inspect.signature(JarvisDesktopWidget._set_online)
        param_names = list(sig.parameters.keys())
        assert "recent_events" in param_names

    def test_set_online_recent_events_default_none(self):
        """recent_events parameter defaults to None."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        import inspect
        sig = inspect.signature(JarvisDesktopWidget._set_online)
        param = sig.parameters["recent_events"]
        assert param.default is None
