"""AgentRoutesMixin -- HTTP and SSE routes for the agent subsystem.

Provides four endpoints:
  POST  /agent/run      -- Submit a new agent task (delegates to AgentRunCommand)
  GET   /agent/status   -- Query task status (delegates to AgentStatusCommand)
  POST  /agent/approve  -- Approve or reject a pending task
  GET   /agent/stream   -- SSE stream of ProgressEventBus events

The SSE handler subscribes to the module-level ProgressEventBus singleton and
streams events as ``text/event-stream``, sending keep-alive pings every 30 s.
"""

from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPStatus
from typing import Any, Protocol

from jarvis_engine.mobile_routes._helpers import (
    MobileRouteHandlerProtocol,
    MobileRouteServerProtocol,
)

logger = logging.getLogger(__name__)

_AGENT_ROUTE_ERRORS = (ImportError, RuntimeError, OSError, ValueError, TypeError, KeyError)

_SSE_KEEPALIVE_SECONDS = 30


class _AgentRouteServerProtocol(MobileRouteServerProtocol, Protocol):
    bus: Any  # ProgressEventBus | None


class _AgentRoutesHandlerProtocol(MobileRouteHandlerProtocol, Protocol):
    server: _AgentRouteServerProtocol

    def _read_json_body(self, *, max_content_length: int = 10_000) -> tuple[dict[str, Any] | None, bytes]:
        ...

    def _write_text(self, status: int, content_type: str, text: str) -> None:
        ...

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        ...

    def _parse_query_params(self) -> dict[str, str]:
        ...


class AgentRoutesMixin:
    """HTTP route handlers for agent run/status/approve and SSE streaming."""

    # ------------------------------------------------------------------
    # POST /agent/run
    # ------------------------------------------------------------------

    def handle_agent_run(self: _AgentRoutesHandlerProtocol) -> None:
        """Submit a new agent task goal."""
        try:
            body, _raw = self._read_json_body(max_content_length=10_000)
            if body is None:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid JSON body"})
                return
            goal = str(body.get("goal", "")).strip()
            task_id = str(body.get("task_id", "")).strip()
            token_budget = int(body.get("token_budget", 50000))

            from jarvis_engine._bus import get_bus
            from jarvis_engine.commands.agent_commands import AgentRunCommand

            cmd = AgentRunCommand(goal=goal, task_id=task_id, token_budget=token_budget)
            result = get_bus().dispatch(cmd)
            payload: dict[str, Any] = {"ok": True, "task_id": result.task_id, "status": result.status}
            self._write_json(HTTPStatus.OK, payload)
        except _AGENT_ROUTE_ERRORS as exc:
            logger.error("handle_agent_run error: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # GET /agent/status?task_id=...
    # ------------------------------------------------------------------

    def handle_agent_status(self: _AgentRoutesHandlerProtocol) -> None:
        """Return current task status."""
        try:
            from jarvis_engine.mobile_routes._helpers import _parse_query_params

            params = _parse_query_params(self.path)  # type: ignore[attr-defined]
            task_id_list = params.get("task_id", [""])
            task_id = task_id_list[0] if task_id_list else ""

            from jarvis_engine._bus import get_bus
            from jarvis_engine.commands.agent_commands import AgentStatusCommand

            cmd = AgentStatusCommand(task_id=task_id)
            result = get_bus().dispatch(cmd)
            payload: dict[str, Any] = {
                "ok": result.return_code == 0,
                "task_id": result.task_id,
                "status": result.status,
                "step_index": result.step_index,
                "tokens_used": result.tokens_used,
                "last_error": result.last_error,
            }
            status_code = HTTPStatus.OK if result.return_code == 0 else HTTPStatus.NOT_FOUND
            self._write_json(status_code, payload)
        except _AGENT_ROUTE_ERRORS as exc:
            logger.error("handle_agent_status error: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # POST /agent/approve
    # ------------------------------------------------------------------

    def handle_agent_approve(self: _AgentRoutesHandlerProtocol) -> None:
        """Approve or reject a pending task."""
        try:
            body, _raw = self._read_json_body(max_content_length=10_000)
            if body is None:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid JSON body"})
                return
            task_id = str(body.get("task_id", "")).strip()
            approved = bool(body.get("approved", True))
            reason = str(body.get("reason", "")).strip()

            from jarvis_engine._bus import get_bus
            from jarvis_engine.commands.agent_commands import AgentApproveCommand

            cmd = AgentApproveCommand(task_id=task_id, approved=approved, reason=reason)
            result = get_bus().dispatch(cmd)
            payload: dict[str, Any] = {
                "ok": result.return_code == 0,
                "task_id": result.task_id,
                "action_taken": result.action_taken,
            }
            self._write_json(HTTPStatus.OK, payload)
        except _AGENT_ROUTE_ERRORS as exc:
            logger.error("handle_agent_approve error: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # GET /agent/stream  (Server-Sent Events)
    # ------------------------------------------------------------------

    def handle_agent_stream(self: _AgentRoutesHandlerProtocol) -> None:
        """Stream ProgressEventBus events as Server-Sent Events (SSE).

        Subscribes to the singleton ProgressEventBus.  Drains the queue in a
        background asyncio event loop.  Sends a keep-alive comment every 30 s.
        Unsubscribes on disconnect.
        """
        try:
            from jarvis_engine.agent.progress_bus import get_progress_bus

            bus = get_progress_bus()

            # Send SSE headers
            self.send_response(HTTPStatus.OK)  # type: ignore[attr-defined]
            self.send_header("Content-Type", "text/event-stream")  # type: ignore[attr-defined]
            self.send_header("Cache-Control", "no-cache")  # type: ignore[attr-defined]
            self.send_header("X-Accel-Buffering", "no")  # type: ignore[attr-defined]
            self.end_headers()  # type: ignore[attr-defined]

            # Use a separate thread-local event loop for SSE blocking
            loop: asyncio.AbstractEventLoop | None = None
            queue: asyncio.Queue | None = None
            try:
                loop = asyncio.new_event_loop()
                queue = loop.run_until_complete(_subscribe_async(bus))

                while True:
                    # Poll queue with keep-alive timeout
                    event = loop.run_until_complete(
                        _drain_with_timeout(queue, _SSE_KEEPALIVE_SECONDS)
                    )
                    if event is None:
                        # Keep-alive ping
                        _sse_write(self, ": keepalive\n\n")
                    else:
                        data = json.dumps(event)
                        _sse_write(self, f"data: {data}\n\n")

                        # Stop streaming on terminal events
                        if event.get("type") in (
                            "task_done", "task_failed", "escalation",
                            "budget_exceeded", "approval_rejected",
                        ):
                            break
            except (BrokenPipeError, ConnectionResetError, OSError):
                logger.debug("SSE client disconnected")
            finally:
                if loop is not None:
                    if queue is not None:
                        try:
                            loop.run_until_complete(_unsubscribe_async(bus, queue))
                        except Exception:  # noqa: BLE001
                            logger.debug("SSE unsubscribe failed during cleanup")
                    loop.close()

        except _AGENT_ROUTE_ERRORS as exc:
            logger.error("handle_agent_stream error: %s", exc)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _subscribe_async(bus: Any) -> asyncio.Queue:  # type: ignore[type-arg]
    return bus.subscribe()


async def _unsubscribe_async(bus: Any, queue: asyncio.Queue) -> None:  # type: ignore[type-arg]
    bus.unsubscribe(queue)


async def _drain_with_timeout(
    queue: asyncio.Queue,  # type: ignore[type-arg]
    timeout: float,
) -> dict[str, Any] | None:
    """Return next event from queue, or None on timeout."""
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def _sse_write(handler: Any, data: str) -> None:
    """Write raw SSE data to the response wfile."""
    try:
        handler.wfile.write(data.encode("utf-8"))
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        raise
