from __future__ import annotations

import logging
import uuid
from http import HTTPStatus
from typing import Any, Protocol

from jarvis_engine._constants import OPS_SNAPSHOT_FILENAME as _OPS_SNAPSHOT_FILENAME
from jarvis_engine._shared import make_task_id as _make_task_id
from jarvis_engine.mobile_routes._helpers import (
    MobileRouteHandlerProtocol,
    MobileRouteServerProtocol,
    _get_int_param,
    _parse_bool,
    _parse_query_params,
    _thread_local,
)

logger = logging.getLogger(__name__)


class _CommandRouteServerProtocol(MobileRouteServerProtocol, Protocol):
    def ensure_memory_engine(self) -> Any | None:
        ...


class _CommandRoutesHandlerProtocol(MobileRouteHandlerProtocol, Protocol):
    server: _CommandRouteServerProtocol

    def _best_effort_learn_command_result(self, payload: dict[str, Any], result: dict[str, Any]) -> None:
        ...

    def _log_command_lifecycle_event(
        self,
        *,
        lifecycle_state: str,
        correlation_id: str,
        payload: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> None:
        ...

    def _run_voice_command(
        self,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def _normalize_command_output(
        self,
        *,
        response_text: str,
        stdout_lines: list[str],
    ) -> dict[str, Any]:
        ...

    def _run_main_cli(self, args: list[str], *, timeout_s: int = 240) -> dict[str, Any]:
        ...


class CommandRoutesMixin:
    """Command, conversation, smart-reply, self-heal, mission, digest, and meeting-prep endpoints."""

    def _best_effort_learn_command_result(
        self: _CommandRoutesHandlerProtocol,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Record failed/empty command outcomes so learning does not stall on guard paths."""
        user_text = str(payload.get("text", "")).strip()
        if not user_text:
            return
        if bool(result.get("ok")) and str(result.get("response", "")).strip():
            return
        intent = str(result.get("intent", "")).strip() or "command_failed"
        reason = str(result.get("reason", "")).strip() or str(result.get("error", "")).strip()
        if not reason:
            reason = f"Command failed with exit code {result.get('command_exit_code', 'unknown')}."
        assistant_response = f"[{intent}] {reason}"
        try:
            from jarvis_engine._bus import get_bus
            from jarvis_engine.commands.learning_commands import LearnInteractionCommand

            _thread_local.repo_root_override = self._root
            try:
                bus = get_bus()
                bus.dispatch(
                    LearnInteractionCommand(
                        user_message=user_text[:1000],
                        assistant_response=assistant_response[:1000],
                        task_id=_make_task_id(f"mobile-{intent}"),
                        route=intent,
                        topic=user_text[:100],
                    )
                )
            finally:
                _thread_local.repo_root_override = None
        except (ImportError, RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.debug("Best-effort command learning fallback failed: %s", exc)

    def _handle_post_command(self: _CommandRoutesHandlerProtocol) -> None:
        payload, _ = self._read_json_body(max_content_length=25_000)
        if payload is None:
            return
        correlation_id = uuid.uuid4().hex
        self._log_command_lifecycle_event(
            lifecycle_state="accepted",
            correlation_id=correlation_id,
            payload=payload,
        )
        self._log_command_lifecycle_event(
            lifecycle_state="running",
            correlation_id=correlation_id,
            payload=payload,
        )
        result = self._run_voice_command(payload, correlation_id=correlation_id)
        result.setdefault("correlation_id", correlation_id)
        result.setdefault("diagnostic_id", correlation_id[:12])
        result.setdefault("lifecycle_state", "completed" if result.get("ok") else "failed")
        result.setdefault("error_code", "")
        result.setdefault("category", "")
        result.setdefault("retryable", False)
        result.setdefault("user_hint", "")
        result.setdefault("response_chunks", [])
        result.setdefault("response_truncated", False)
        result.setdefault("stdout_truncated", False)
        self._best_effort_learn_command_result(payload, result)
        # Scan LLM output for security issues (credential leaks, exfiltration, etc.)
        _sec_orch = getattr(self.server, "security", None)
        if _sec_orch is not None and result.get("ok"):
            _response_parts = []
            if result.get("response"):
                _response_parts.append(str(result["response"]))
            if result.get("reason"):
                _response_parts.append(str(result["reason"]))
            for _line in result.get("stdout_tail", []):
                _response_parts.append(str(_line))
            _response_text = "\n".join(_response_parts)
            if _response_text.strip():
                _output_check = _sec_orch.scan_output(_response_text)
                if not _output_check["safe"]:
                    result["response"] = _output_check["filtered_text"]
                    result["reason"] = "Output security-filtered: " + ", ".join(_output_check.get("findings", ["policy violation"]))
                    result["stdout_tail"] = [_output_check["filtered_text"]]
                    result["security_filtered"] = True
                    normalized = self._normalize_command_output(
                        response_text=str(result.get("response", "")),
                        stdout_lines=[str(x) for x in result.get("stdout_tail", [])],
                    )
                    result["response"] = normalized["response"]
                    result["response_chunks"] = normalized["response_chunks"]
                    result["response_truncated"] = normalized["response_truncated"]
                    result["stdout_tail"] = normalized["stdout_tail"]
                    result["stdout_truncated"] = normalized["stdout_truncated"]
                    logger.warning("Output filtered: %s", _output_check["findings"][:3])
        self._log_command_lifecycle_event(
            lifecycle_state=str(result.get("lifecycle_state", "failed")),
            correlation_id=str(result.get("correlation_id", correlation_id)),
            payload=payload,
            result=result,
        )
        self._write_json(HTTPStatus.OK, result)

    def _handle_post_conversation_clear(self: _CommandRoutesHandlerProtocol) -> None:
        payload, _ = self._read_json_body(max_content_length=1_000)
        if payload is None:
            return
        try:
            import jarvis_engine.voice.pipeline as _vp_mod
            from jarvis_engine.conversation_state import get_conversation_state

            _vp_mod._state.clear_history()
            _vp_mod._state._conversation_history_loaded = True
            try:
                _vp_mod.save_conversation_history()
            except (OSError, ValueError, TypeError) as save_exc:
                logger.debug("Conversation history save-after-clear failed: %s", save_exc)
            try:
                csm = get_conversation_state()
                csm.reset()
            except (OSError, ValueError, TypeError, RuntimeError) as reset_exc:
                logger.debug("Conversation continuity reset failed during clear: %s", reset_exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "message": "Conversation history cleared."})
        except (ValueError, TypeError, OSError, ImportError, RuntimeError, AttributeError) as exc:  # narrowed from except Exception
            logger.error("Conversation history clear failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "message": "Best-effort clear completed."})

    def _handle_post_smart_reply(self: _CommandRoutesHandlerProtocol) -> None:
        """Generate a contextual auto-reply SMS for a missed call."""
        payload, _ = self._read_json_body(max_content_length=5_000)
        if payload is None:
            return
        contact_name = str(payload.get("contact_name", "")).strip()[:50]
        context = str(payload.get("context", "")).strip().lower()
        meeting_end = str(payload.get("meeting_end_time", "")).strip()
        eta_minutes = payload.get("eta_minutes")
        if not contact_name:
            contact_name = "there"
        if context == "meeting":
            reply = f"Hey {contact_name}, I'm in a meeting right now"
            if meeting_end:
                try:
                    from datetime import datetime as _dt

                    end_dt = _dt.fromisoformat(meeting_end)
                    reply += f" until {end_dt.strftime('%I:%M %p')}"
                except (ValueError, TypeError):
                    logger.debug("Invalid meeting_end format: %s", meeting_end)
            reply += ". I'll call you back as soon as I'm free."
        elif context == "driving":
            reply = f"Hey {contact_name}, I'm driving right now"
            if eta_minutes and isinstance(eta_minutes, (int, float)):
                reply += f" — about {int(eta_minutes)} min until I arrive"
            reply += ". I'll call you back when I get there."
        elif context == "sleeping":
            reply = f"Hey {contact_name}, I'm currently unavailable. I'll get back to you in the morning."
        else:
            reply = f"Hey {contact_name}, I missed your call. I'll call you back soon."
        reply += " — Sent by Jarvis"
        contact_context = ""
        try:
            server_obj = self.server
            mem_engine = server_obj.ensure_memory_engine()
            if mem_engine is not None:
                results = mem_engine.search_fts(contact_name, limit=2)
                for record_id, _score in results:
                    rec = mem_engine.get_record(record_id)
                    if rec:
                        contact_context = str(rec.get("summary", ""))[:200]
                        break
        except (ImportError, RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
            logger.debug("Contact context memory lookup failed: %s", exc)
        self._write_json(HTTPStatus.OK, {
            "ok": True,
            "reply": reply,
            "contact_context": contact_context,
        })

    def _handle_post_self_heal(self: _CommandRoutesHandlerProtocol) -> None:
        payload, _ = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return
        keep_recent_raw = payload.get("keep_recent", 1800)
        force_maintenance = _parse_bool(payload.get("force_maintenance", False))
        snapshot_note = str(payload.get("snapshot_note", "mobile-self-heal")).strip()[:160] or "mobile-self-heal"
        snapshot_note = snapshot_note.lstrip("-") or "mobile-self-heal"
        try:
            keep_recent = int(keep_recent_raw)
        except (TypeError, ValueError):
            keep_recent = 1800
        keep_recent = max(200, min(keep_recent, 50000))
        args = ["self-heal", "--keep-recent", str(keep_recent), "--snapshot-note", snapshot_note]
        if force_maintenance:
            args.append("--force-maintenance")
        result = self._run_main_cli(args, timeout_s=240)
        self._write_json(HTTPStatus.OK, result)

    def _handle_post_missions_create(self: _CommandRoutesHandlerProtocol) -> None:
        """Create a learning mission from the phone."""
        payload, _ = self._read_json_body(max_content_length=5_000)
        if payload is None:
            return
        topic = str(payload.get("topic", "")).strip()
        if not topic:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "topic is required"})
            return
        objective = str(payload.get("objective", "")).strip()[:400]
        sources = payload.get("sources")
        if sources is not None:
            if not isinstance(sources, list):
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "sources must be a list"})
                return
            sources = [str(s).strip() for s in sources if str(s).strip()][:6]
        try:
            from jarvis_engine._bus import get_bus
            from jarvis_engine.commands.ops_commands import MissionCreateCommand

            bus = get_bus()
            cmd = MissionCreateCommand(topic=topic, objective=objective, sources=sources or [], origin="phone")
            result = bus.dispatch(cmd)
            if result.return_code != 0:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Mission creation failed — invalid parameters."})
                return
            mission = result.mission if hasattr(result, "mission") else {}
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "mission_id": mission.get("mission_id", ""),
                "topic": mission.get("topic", ""),
                "status": mission.get("status", "pending"),
                "origin": mission.get("origin", "phone"),
                "sources": mission.get("sources", []),
            })
        except ValueError as exc:
            logger.warning("Mission create validation failed: %s", exc)
            sanitized = str(exc)[:200].replace("\n", " ")
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": sanitized})
        except (KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:  # narrowed from except Exception
            logger.error("Mission create failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission creation failed."})

    def _handle_get_missions_status(self: _CommandRoutesHandlerProtocol) -> None:
        """Get learning mission status."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine._bus import get_bus
            from jarvis_engine.commands.ops_commands import MissionStatusCommand

            bus = get_bus()
            qs = _parse_query_params(self.path)
            last = _get_int_param(qs, "last", 15, max_val=50)
            result = bus.dispatch(MissionStatusCommand(last=last))
            missions = result.missions if hasattr(result, "missions") else []
            total = result.total_count if hasattr(result, "total_count") else 0
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "total": total,
                "missions": [
                    {
                        "mission_id": m.get("mission_id", ""),
                        "topic": m.get("topic", ""),
                        "objective": m.get("objective", ""),
                        "status": m.get("status", ""),
                        "origin": m.get("origin", "desktop-manual"),
                        "sources": m.get("sources", []),
                        "verified_findings": m.get("verified_findings", 0),
                        "created_utc": m.get("created_utc", ""),
                        "updated_utc": m.get("updated_utc", ""),
                    }
                    for m in missions
                    if isinstance(m, dict)
                ],
            })
        except (ValueError, KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:  # narrowed from except Exception
            logger.error("Mission status failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission status unavailable."})

    def _handle_get_missions_active(self: _CommandRoutesHandlerProtocol) -> None:
        """Return all running/paused/pending missions."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.learning_missions import get_active_missions

            missions = get_active_missions(self._root)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "count": len(missions),
                "missions": [
                    {
                        "mission_id": m.get("mission_id", ""),
                        "topic": m.get("topic", ""),
                        "status": m.get("status", ""),
                        "progress_pct": int(m.get("progress_pct", 0)),
                        "progress_bar": m.get("progress_bar", ""),
                        "status_detail": m.get("status_detail", ""),
                        "updated_utc": m.get("updated_utc", ""),
                    }
                    for m in missions
                    if isinstance(m, dict)
                ],
            })
        except (ValueError, KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:
            logger.error("Mission active list failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Active missions unavailable."})

    def _handle_get_missions_steps(self: _CommandRoutesHandlerProtocol) -> None:
        """Return the step breakdown for a specific mission."""
        if not self._validate_auth(b""):
            return
        qs = _parse_query_params(self.path)
        mission_id = str(qs.get("id", [""])[0]).strip()
        if not mission_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "id query parameter is required"})
            return
        try:
            from jarvis_engine.learning_missions import get_mission_steps

            steps = get_mission_steps(self._root, mission_id)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "mission_id": mission_id,
                "steps": steps,
            })
        except (ValueError, KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:
            logger.error("Mission steps failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission steps unavailable."})

    def _handle_post_missions_pause(self: _CommandRoutesHandlerProtocol) -> None:
        """Pause a running mission."""
        payload, _ = self._read_json_body(max_content_length=1_000)
        if payload is None:
            return
        mission_id = str(payload.get("mission_id", "")).strip()
        if not mission_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "mission_id is required"})
            return
        try:
            from jarvis_engine.learning_missions import pause_mission

            mission = pause_mission(self._root, mission_id=mission_id)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "mission_id": mission.get("mission_id", ""),
                "status": mission.get("status", ""),
            })
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)[:200]})
        except (KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:
            logger.error("Mission pause failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission pause failed."})

    def _handle_post_missions_resume(self: _CommandRoutesHandlerProtocol) -> None:
        """Resume a paused mission."""
        payload, _ = self._read_json_body(max_content_length=1_000)
        if payload is None:
            return
        mission_id = str(payload.get("mission_id", "")).strip()
        if not mission_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "mission_id is required"})
            return
        try:
            from jarvis_engine.learning_missions import resume_mission

            mission = resume_mission(self._root, mission_id=mission_id)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "mission_id": mission.get("mission_id", ""),
                "status": mission.get("status", ""),
            })
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)[:200]})
        except (KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:
            logger.error("Mission resume failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission resume failed."})

    def _handle_post_missions_restart(self: _CommandRoutesHandlerProtocol) -> None:
        """Restart a failed/cancelled mission."""
        payload, _ = self._read_json_body(max_content_length=1_000)
        if payload is None:
            return
        mission_id = str(payload.get("mission_id", "")).strip()
        if not mission_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "mission_id is required"})
            return
        try:
            from jarvis_engine.learning_missions import restart_mission

            mission = restart_mission(self._root, mission_id=mission_id)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "mission_id": mission.get("mission_id", ""),
                "status": mission.get("status", ""),
            })
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)[:200]})
        except (KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:
            logger.error("Mission restart failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission restart failed."})

    def _handle_get_digest(self: _CommandRoutesHandlerProtocol) -> None:
        """Return a context-aware digest of what happened while user was busy."""
        if not self._validate_auth(b""):
            return
        qs = _parse_query_params(self.path)
        since_ts = _get_int_param(qs, "since", 0, min_val=0, max_val=2**31)
        context_label = str(qs.get("context", [""])[0]).strip()
        digest: dict[str, Any] = {
            "context": context_label,
            "since_ts": since_ts,
            "missed_calls": [],
            "notifications_summary": "",
            "calendar_upcoming": [],
            "proactive_alerts": [],
            "tasks_changed": [],
        }
        try:
            from jarvis_engine.proactive.alert_queue import peek_alerts

            digest["proactive_alerts"] = peek_alerts(self._root, limit=10)
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.debug("Peek alerts for digest failed: %s", exc)
        try:
            snapshot_path = self._root / ".planning" / _OPS_SNAPSHOT_FILENAME
            if snapshot_path.exists():
                from jarvis_engine._shared import load_json_file

                snap = load_json_file(snapshot_path, {})
                events = snap.get("calendar_events", [])
                from datetime import datetime as _dt

                now = _dt.now().astimezone()
                upcoming = []
                for ev in events:
                    start_str = ev.get("start_time", "")
                    if not start_str:
                        continue
                    try:
                        start = _dt.fromisoformat(start_str)
                        if start.tzinfo is None:
                            start = start.astimezone()
                        diff_hours = (start - now).total_seconds() / 3600.0
                        if 0 <= diff_hours <= 2:
                            upcoming.append({
                                "title": ev.get("title", ""),
                                "start_time": start_str,
                                "minutes_until": int(diff_hours * 60),
                            })
                    except (ValueError, TypeError):
                        logger.debug("Skipping calendar event with invalid date")
                        continue
                digest["calendar_upcoming"] = upcoming[:5]
        except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
            logger.debug("Calendar upcoming for digest failed: %s", exc)
        if context_label:
            try:
                from jarvis_engine._bus import get_bus
                from jarvis_engine.commands.ops_commands import OpsBriefCommand

                bus = get_bus()
                snapshot_path = self._root / ".planning" / _OPS_SNAPSHOT_FILENAME
                result = bus.dispatch(OpsBriefCommand(snapshot_path=snapshot_path))
                if hasattr(result, "brief") and result.brief:
                    digest["notifications_summary"] = result.brief[:1000]
            except (ImportError, RuntimeError, OSError, ValueError, TypeError) as exc:
                logger.debug("Ops brief for digest failed: %s", exc)
        self._write_json(HTTPStatus.OK, {"ok": True, "digest": digest})

    def _handle_get_meeting_prep(self: _CommandRoutesHandlerProtocol) -> None:
        """Return KG-powered intelligence briefing for an upcoming meeting."""
        if not self._validate_auth(b""):
            return
        qs = _parse_query_params(self.path)
        from urllib.parse import unquote

        title = unquote(str(qs.get("title", [""])[0]).strip())
        att_raw = str(qs.get("attendees", [""])[0]).strip()
        attendees: list[str] = [a.strip() for a in att_raw.split(",") if a.strip()] if att_raw else []
        if not title and not attendees:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "title or attendees required"})
            return
        briefing: dict[str, Any] = {
            "title": title,
            "attendees": attendees,
            "context_facts": [],
            "recent_memories": [],
            "suggested_topics": [],
        }
        try:
            server_obj = self.server
            mem_engine = server_obj.ensure_memory_engine()
            if mem_engine is not None:
                kg = getattr(mem_engine, "_kg", None) or getattr(mem_engine, "kg", None)
                if kg is not None:
                    for person in attendees[:5]:
                        try:
                            facts = kg.query_relevant_facts([person], limit=5)
                            for fact in facts:
                                briefing["context_facts"].append({
                                    "about": person,
                                    "fact": fact.get("label", ""),
                                    "confidence": round(float(fact.get("confidence", 0)), 2),
                                })
                        except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
                            logger.debug("KG person fact lookup failed for %s: %s", person, exc)
                    if title:
                        try:
                            topic_facts = kg.query_relevant_facts(title.split()[:4], limit=5)
                            for fact in topic_facts:
                                briefing["context_facts"].append({
                                    "about": title,
                                    "fact": fact.get("label", ""),
                                    "confidence": round(float(fact.get("confidence", 0)), 2),
                                })
                        except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
                            logger.debug("KG topic fact lookup failed: %s", exc)
                keywords = attendees + ([title] if title else [])
                # Batch: collect all record IDs first, then fetch in one query
                _keyword_record_ids: list[tuple[str, str]] = []  # (keyword, record_id)
                for keyword in keywords[:3]:
                    try:
                        results = mem_engine.search_fts(keyword, limit=3)
                        for record_id, _score in results:
                            _keyword_record_ids.append((keyword, record_id))
                    except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
                        logger.debug("Memory search for meeting keyword %s failed: %s", keyword, exc)
                if _keyword_record_ids:
                    try:
                        all_ids = [rid for _, rid in _keyword_record_ids]
                        records_batch = mem_engine.get_records_batch(all_ids)
                        rec_map = {str(r.get("record_id", "")): r for r in records_batch}
                        for keyword, record_id in _keyword_record_ids:
                            rec = rec_map.get(record_id)
                            if rec:
                                briefing["recent_memories"].append({
                                    "about": keyword,
                                    "summary": str(rec.get("summary", ""))[:200],
                                    "date": str(rec.get("ts", "")),
                                })
                    except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
                        logger.debug("Batch record fetch for meeting prep failed: %s", exc)
        except (ImportError, RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
            logger.debug("Meeting prep KG query failed: %s", exc)
        if briefing["context_facts"] or briefing["recent_memories"]:
            topics = set()
            for fact in briefing["context_facts"]:
                label = fact.get("fact", "")
                if label and len(label) > 10:
                    topics.add(label[:80])
            for mem in briefing["recent_memories"]:
                summary = mem.get("summary", "")
                if summary and len(summary) > 10:
                    topics.add(summary[:80])
            briefing["suggested_topics"] = list(topics)[:5]
        self._write_json(HTTPStatus.OK, {"ok": True, "briefing": briefing})
