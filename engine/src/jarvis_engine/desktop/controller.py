from __future__ import annotations

from dataclasses import dataclass, field
import threading
from collections.abc import Callable
from enum import Enum
from typing import Any


class DesktopWidgetState(str, Enum):
    """Authoritative desktop interaction states for widget/voice flow."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    ERROR = "error"

    @classmethod
    def coerce(cls, value: "DesktopWidgetState | str") -> "DesktopWidgetState":
        """Return a valid state, defaulting unknown input to ``IDLE``."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return cls.IDLE


@dataclass(frozen=True)
class DesktopMissionSnapshot:
    """Structured mission status surfaced to the desktop UI."""

    count: int = 0
    topics: tuple[str, ...] = ()
    current_topic: str = ""
    current_step: str = ""
    progress_pct: int = 0
    artifacts_so_far: int = 0


@dataclass(frozen=True)
class DesktopActivitySnapshot:
    """Recent activity digest for live desktop surfaces."""

    summary: str = ""
    category: str = ""
    timestamp: str = ""


@dataclass(frozen=True)
class DesktopSessionSnapshot:
    """Desktop interaction posture surfaced to the widget hero capsule."""

    route_label: str = "Auto Router"
    route_accent: str = "#12c9b1"
    route_family: str = "auto"
    control_mode: str = "Advisory only"
    control_armed: bool = False
    approval_mode: str = "Approval required"
    auto_approve: bool = False
    voice_mode: str = "Push to talk"
    wakeword_enabled: bool = False
    speech_mode: str = "Voice replies on"
    speech_enabled: bool = True


@dataclass(frozen=True)
class DesktopContinuitySnapshot:
    """Conversation continuity state surfaced to the desktop UI."""

    rolling_summary: str = ""
    anchor_entities: tuple[str, ...] = ()
    unresolved_goals: tuple[str, ...] = ()
    prior_decisions: tuple[str, ...] = ()
    timeline_count: int = 0


@dataclass(frozen=True)
class DesktopDiagnosticsSnapshot:
    """Quick diagnostic state surfaced to the desktop UI."""

    score: int | None = None
    healthy: bool = False
    issue_count: int = 0
    top_issue: str = ""


@dataclass(frozen=True)
class DesktopLiveSnapshot:
    """Single desktop truth model for status, missions, and live activity."""

    state: DesktopWidgetState = DesktopWidgetState.IDLE
    online: bool = False
    intelligence_score_pct: int | None = None
    intelligence_regression: bool = False
    facts_total: int = 0
    facts_last_7d: int = 0
    kg_nodes: int = 0
    kg_edges: int = 0
    memory_records: int = 0
    self_test_score_pct: int | None = None
    growth_trend: str = "stable"
    mission: DesktopMissionSnapshot = field(default_factory=DesktopMissionSnapshot)
    activity: DesktopActivitySnapshot = field(default_factory=DesktopActivitySnapshot)
    session: DesktopSessionSnapshot = field(default_factory=DesktopSessionSnapshot)
    continuity: DesktopContinuitySnapshot = field(default_factory=DesktopContinuitySnapshot)
    diagnostics: DesktopDiagnosticsSnapshot = field(default_factory=DesktopDiagnosticsSnapshot)


class DesktopInteractionController:
    """Own the desktop interaction state machine and command lifecycle.

    The controller is intentionally UI-agnostic.  It tracks the current state,
    command generation, cancel/hotword guards, and exposes safe transitions so
    the Tk widget can render from a single source of truth.
    """

    def __init__(
        self,
        *,
        on_state_change: Callable[[DesktopWidgetState], None] | None = None,
    ) -> None:
        self._on_state_change = on_state_change
        self._lock = threading.RLock()
        self._state = DesktopWidgetState.IDLE
        self._command_generation = 0
        self._cancel_event = threading.Event()
        self._hotword_active = threading.Event()
        self._online = False
        self._intelligence_score_pct: int | None = None
        self._intelligence_regression = False
        self._facts_total = 0
        self._facts_last_7d = 0
        self._kg_nodes = 0
        self._kg_edges = 0
        self._memory_records = 0
        self._self_test_score_pct: int | None = None
        self._growth_trend = "stable"
        self._mission = DesktopMissionSnapshot()
        self._activity = DesktopActivitySnapshot()
        self._session = DesktopSessionSnapshot()
        self._continuity = DesktopContinuitySnapshot()
        self._diagnostics = DesktopDiagnosticsSnapshot()
        self._seen_activity_event_ids: dict[str, None] = {}

    @property
    def state(self) -> DesktopWidgetState:
        with self._lock:
            return self._state

    @property
    def command_generation(self) -> int:
        with self._lock:
            return self._command_generation

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    @property
    def hotword_event(self) -> threading.Event:
        return self._hotword_active

    def owns_generation(self, generation: int) -> bool:
        with self._lock:
            return self._command_generation == generation

    def snapshot(self) -> DesktopLiveSnapshot:
        with self._lock:
            return DesktopLiveSnapshot(
                state=self._state,
                online=self._online,
                intelligence_score_pct=self._intelligence_score_pct,
                intelligence_regression=self._intelligence_regression,
                facts_total=self._facts_total,
                facts_last_7d=self._facts_last_7d,
                kg_nodes=self._kg_nodes,
                kg_edges=self._kg_edges,
                memory_records=self._memory_records,
                self_test_score_pct=self._self_test_score_pct,
                growth_trend=self._growth_trend,
                mission=self._mission,
                activity=self._activity,
                session=self._session,
                continuity=self._continuity,
                diagnostics=self._diagnostics,
            )

    def can_begin_command(self) -> bool:
        with self._lock:
            return self._state is not DesktopWidgetState.PROCESSING

    def begin_command(self) -> int | None:
        with self._lock:
            if self._state is DesktopWidgetState.PROCESSING:
                return None
            self._cancel_event.clear()
            self._command_generation += 1
            generation = self._command_generation
            self._state = DesktopWidgetState.PROCESSING
        if self._on_state_change is not None:
            self._on_state_change(DesktopWidgetState.PROCESSING)
        return generation

    def complete_command(self, generation: int) -> bool:
        with self._lock:
            if self._command_generation != generation:
                return False
            should_reset = self._state is DesktopWidgetState.PROCESSING
            if should_reset:
                self._state = DesktopWidgetState.IDLE
        if should_reset and self._on_state_change is not None:
            self._on_state_change(DesktopWidgetState.IDLE)
        return True

    def cancel_command(self) -> None:
        self._cancel_event.set()
        self.set_state(DesktopWidgetState.IDLE)

    def processing_timed_out(self) -> bool:
        with self._lock:
            timed_out = self._state is DesktopWidgetState.PROCESSING
            if timed_out:
                self._state = DesktopWidgetState.IDLE
        if timed_out and self._on_state_change is not None:
            self._on_state_change(DesktopWidgetState.IDLE)
        return timed_out

    def begin_dictation(self) -> bool:
        with self._lock:
            if self._state in (
                DesktopWidgetState.LISTENING,
                DesktopWidgetState.PROCESSING,
            ):
                return False
            self._state = DesktopWidgetState.LISTENING
        if self._on_state_change is not None:
            self._on_state_change(DesktopWidgetState.LISTENING)
        return True

    def end_dictation(self) -> None:
        with self._lock:
            should_reset = self._state is DesktopWidgetState.LISTENING
            if should_reset:
                self._state = DesktopWidgetState.IDLE
        if should_reset and self._on_state_change is not None:
            self._on_state_change(DesktopWidgetState.IDLE)

    def try_start_hotword_loop(self) -> bool:
        with self._lock:
            if self._hotword_active.is_set():
                return False
            self._hotword_active.set()
            return True

    def finish_hotword_loop(self) -> None:
        self._hotword_active.clear()

    def apply_health_snapshot(
        self,
        *,
        online: bool,
        intel_data: dict[str, Any] | None = None,
        growth_data: dict[str, Any] | None = None,
        recent_events: list[dict[str, Any]] | None = None,
        now_working_on: dict[str, Any] | None = None,
        clear_missing: bool = False,
    ) -> list[dict[str, Any]]:
        """Update controller-owned desktop status from health/widget payloads."""
        with self._lock:
            self._online = online
            if intel_data is not None or clear_missing:
                self._apply_intelligence_locked(intel_data)
            if growth_data is not None or now_working_on is not None or clear_missing:
                self._apply_growth_locked(growth_data, now_working_on)
            if recent_events is not None:
                return self._ingest_activity_events_locked(recent_events)
            return []

    def apply_session_snapshot(
        self,
        *,
        route_label: str,
        route_accent: str,
        route_family: str,
        control_armed: bool,
        auto_approve: bool,
        wakeword_enabled: bool,
        speech_enabled: bool,
    ) -> None:
        """Update controller-owned desktop posture from local widget settings."""
        with self._lock:
            self._session = DesktopSessionSnapshot(
                route_label=str(route_label).strip() or "Auto Router",
                route_accent=str(route_accent).strip() or "#12c9b1",
                route_family=str(route_family).strip() or "auto",
                control_mode="Desktop control armed" if control_armed else "Advisory only",
                control_armed=bool(control_armed),
                approval_mode="Auto-approve armed" if auto_approve else "Approval required",
                auto_approve=bool(auto_approve),
                voice_mode="Wake word live" if wakeword_enabled else "Push to talk",
                wakeword_enabled=bool(wakeword_enabled),
                speech_mode="Voice replies on" if speech_enabled else "Silent replies",
                speech_enabled=bool(speech_enabled),
            )

    def apply_continuity_snapshot(
        self,
        *,
        rolling_summary: str,
        anchor_entities: list[str] | tuple[str, ...],
        unresolved_goals: list[str] | tuple[str, ...],
        prior_decisions: list[str] | tuple[str, ...],
        timeline_count: int = 0,
    ) -> None:
        """Update controller-owned continuity state from conversation_state."""
        with self._lock:
            self._continuity = DesktopContinuitySnapshot(
                rolling_summary=str(rolling_summary).strip(),
                anchor_entities=tuple(str(item).strip() for item in anchor_entities if str(item).strip()),
                unresolved_goals=tuple(str(item).strip() for item in unresolved_goals if str(item).strip()),
                prior_decisions=tuple(str(item).strip() for item in prior_decisions if str(item).strip()),
                timeline_count=max(int(timeline_count), 0),
            )

    def apply_diagnostics_snapshot(
        self,
        *,
        score: int | None,
        healthy: bool,
        issues: list[dict[str, Any]] | None = None,
        error: str = "",
    ) -> None:
        """Update controller-owned diagnostic health state."""
        issue_items = issues or []
        top_issue = ""
        if issue_items:
            first = issue_items[0]
            top_issue = str(first.get("description", "")).strip()
        elif error.strip():
            top_issue = error.strip()
        with self._lock:
            self._diagnostics = DesktopDiagnosticsSnapshot(
                score=score if isinstance(score, int) else None,
                healthy=bool(healthy),
                issue_count=len(issue_items),
                top_issue=top_issue[:160],
            )

    def _apply_intelligence_locked(self, intel_data: dict[str, Any] | None) -> None:
        if not isinstance(intel_data, dict):
            self._intelligence_score_pct = None
            self._intelligence_regression = False
            return
        try:
            score = float(intel_data.get("score", 0.0))
        except (TypeError, ValueError):
            self._intelligence_score_pct = None
            self._intelligence_regression = False
            return
        self._intelligence_score_pct = round(score * 100)
        self._intelligence_regression = bool(intel_data.get("regression", False))

    def _apply_growth_locked(
        self,
        growth_data: dict[str, Any] | None,
        now_working_on: dict[str, Any] | None,
    ) -> None:
        metrics = growth_data.get("metrics", growth_data) if isinstance(growth_data, dict) else None
        if not isinstance(metrics, dict):
            self._facts_total = 0
            self._facts_last_7d = 0
            self._kg_nodes = 0
            self._kg_edges = 0
            self._memory_records = 0
            self._self_test_score_pct = None
            self._growth_trend = "stable"
            self._mission = self._build_mission_snapshot_locked(None, now_working_on)
            return
        self._facts_total = self._safe_int(metrics.get("facts_total", 0))
        self._facts_last_7d = self._safe_int(metrics.get("facts_last_7d", 0))
        self._kg_nodes = self._safe_int(metrics.get("kg_nodes", 0))
        self._kg_edges = self._safe_int(metrics.get("kg_edges", 0))
        self._memory_records = self._safe_int(metrics.get("memory_records", 0))
        try:
            self._self_test_score_pct = round(float(metrics.get("last_self_test_score", 0.0)) * 100)
        except (TypeError, ValueError):
            self._self_test_score_pct = None
        self._growth_trend = str(metrics.get("growth_trend", "stable")) or "stable"
        self._mission = self._build_mission_snapshot_locked(metrics, now_working_on)

    def _build_mission_snapshot_locked(
        self,
        metrics: dict[str, Any] | None,
        now_working_on: dict[str, Any] | None,
    ) -> DesktopMissionSnapshot:
        active_topics: tuple[str, ...] = ()
        mission_count = 0
        if isinstance(metrics, dict):
            missions = metrics.get("active_missions", [])
            if isinstance(missions, list):
                filtered = [
                    m for m in missions
                    if isinstance(m, dict)
                    and str(m.get("status", "")).lower()
                    not in {"completed", "failed", "cancelled", "exhausted"}
                ]
                active_topics = tuple(str(m.get("topic", "")).strip()[:24] for m in filtered if str(m.get("topic", "")).strip())
                mission_count = self._safe_int(metrics.get("mission_count", len(filtered)))
            else:
                mission_count = self._safe_int(metrics.get("mission_count", 0))
        topic = ""
        step = ""
        progress_pct = 0
        artifacts = 0
        if isinstance(now_working_on, dict):
            topic = str(now_working_on.get("mission_topic", "")).strip()
            step = str(now_working_on.get("current_step", "")).strip()
            progress_pct = self._safe_int(now_working_on.get("progress_pct", 0))
            artifacts = self._safe_int(now_working_on.get("artifacts_so_far", 0))
        return DesktopMissionSnapshot(
            count=mission_count,
            topics=active_topics[:3],
            current_topic=topic[:32],
            current_step=step[:80],
            progress_pct=progress_pct,
            artifacts_so_far=artifacts,
        )

    def _ingest_activity_events_locked(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        new_events: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id", "")).strip()
            if event_id:
                if event_id in self._seen_activity_event_ids:
                    continue
                self._seen_activity_event_ids[event_id] = None
            new_events.append(event)
        if len(self._seen_activity_event_ids) > 500:
            keys = list(self._seen_activity_event_ids.keys())
            self._seen_activity_event_ids = dict.fromkeys(keys[-400:])
        latest_event = new_events[-1] if new_events else None
        if isinstance(latest_event, dict):
            timestamp_raw = str(latest_event.get("timestamp", "")).strip()
            timestamp_short = timestamp_raw[11:19] if len(timestamp_raw) >= 19 else timestamp_raw
            self._activity = DesktopActivitySnapshot(
                summary=str(latest_event.get("summary", "")).strip()[:140],
                category=str(latest_event.get("category", "")).strip(),
                timestamp=timestamp_short,
            )
        return new_events

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def set_state(self, state: "DesktopWidgetState | str") -> DesktopWidgetState:
        next_state = DesktopWidgetState.coerce(state)
        with self._lock:
            self._state = next_state
        if self._on_state_change is not None:
            self._on_state_change(next_state)
        return next_state
