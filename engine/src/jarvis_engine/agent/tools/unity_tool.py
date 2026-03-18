"""UnityTool -- WebSocket JSON-RPC client for the Unity Editor Bridge.

Connects to the C# JarvisEditorBridge running on ws://localhost:8091/jarvis
and issues JSON-RPC 2.0 commands for project management, script writing, and
compilation.

Security model (PRIMARY GATE on the Python side):
- Path jail: all script writes must target Assets/JarvisGenerated/ only.
- Static analysis: generated C# is scanned for dangerous API patterns before
  transmission to the bridge.  The C# bridge applies defense-in-depth checks
  as a second layer.

Domain reload handling:
- After write_script(), Unity triggers a domain reload (recompilation).
- The tool enters WAITING_FOR_BRIDGE state and blocks further commands until
  the bridge sends a {"status":"ready"} heartbeat.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------

_JAIL_PREFIX = "Assets/JarvisGenerated"

# Each entry is (compiled_pattern, human_readable_message).
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Process execution
    (re.compile(r"Process\.Start"), "Process.Start is forbidden"),
    (re.compile(r"System\.Diagnostics\.Process"), "System.Diagnostics.Process is forbidden"),
    (re.compile(r"System\.Management\.Automation"), "PowerShell execution is forbidden"),
    # File/directory deletion
    (re.compile(r"File\.Delete"), "File.Delete is forbidden"),
    (re.compile(r"Directory\.Delete"), "Directory.Delete is forbidden"),
    (re.compile(r"FileUtil\.DeleteFileOrDirectory"), "FileUtil.DeleteFileOrDirectory is forbidden"),
    (re.compile(r"AssetDatabase\.DeleteAsset"), "AssetDatabase.DeleteAsset is forbidden"),
    # Path traversal
    (re.compile(r"\.\.\s*[/\\]"), "Path traversal (../) is forbidden"),
    # Assembly/reflection
    (re.compile(r"Assembly\.Load(?:From|File)?\s*[\(\[]"), "Assembly.Load is forbidden"),
    (re.compile(r"GetMethod\s*\(.*\)\s*\.\s*Invoke"), "Reflection Invoke chain is forbidden"),
    (re.compile(r"Activator\.CreateInstance"), "Dynamic instantiation is forbidden"),
    (re.compile(r"AppDomain\.ExecuteAssembly"), "Assembly execution is forbidden"),
    # Dynamic compilation
    (re.compile(r"CSharpCodeProvider|CodeDomProvider"), "Dynamic C# compilation is forbidden"),
    (re.compile(r"CSharpCompilation"), "Roslyn compilation is forbidden"),
    # Native interop
    (re.compile(r"\bDllImport\b"), "P/Invoke (DllImport) is forbidden"),
    (re.compile(r"\bunsafe\b"), "Unsafe code blocks are forbidden"),
    (re.compile(r"Marshal\.\w+"), "Unmanaged memory access is forbidden"),
    # Network (data exfiltration)
    (re.compile(r"WebClient|HttpClient|WebRequest"), "Network requests are forbidden in generated code"),
    # Environment/Registry
    (re.compile(r"Environment\.SetEnvironmentVariable"), "Environment modification is forbidden"),
    (re.compile(r"Microsoft\.Win32\.Registry"), "Registry access is forbidden"),
]


# ---------------------------------------------------------------------------
# Security functions
# ---------------------------------------------------------------------------


def _assert_in_jail(rel_path: str) -> None:
    """Raise PermissionError if *rel_path* is outside the Unity path jail.

    Normalises both forward and backward slashes, resolves ``..`` segments,
    then verifies the result starts with ``Assets/JarvisGenerated/`` (note the
    trailing slash — prevents siblings like ``Assets/JarvisGenerated2/`` from
    passing).
    """
    if not rel_path:
        raise PermissionError(
            f"Empty path is not permitted; must be inside {_JAIL_PREFIX}/"
        )
    # Normalize separators, then let os.path.normpath resolve traversals.
    normalised = os.path.normpath(rel_path.replace("\\", "/")).replace("\\", "/")
    # Require path to start with the jail prefix followed by a separator so
    # that sibling prefixes like "Assets/JarvisGenerated2" don't sneak through.
    # Accept the jail directory itself or any path under it
    if normalised != _JAIL_PREFIX and not normalised.startswith(_JAIL_PREFIX + "/"):
        raise PermissionError(
            f"Path {rel_path!r} (normalised: {normalised!r}) is outside the "
            f"Unity path jail ({_JAIL_PREFIX}/)."
        )


def _assert_safe_code(content: str) -> None:
    """Raise ValueError if *content* contains any forbidden C# API pattern.

    Scans the generated C# source for dangerous patterns (process spawning,
    file deletion outside jail, reflection-based code execution, path
    traversal) and raises on the first match.
    """
    for pattern, message in _DANGEROUS_PATTERNS:
        if pattern.search(content):
            raise ValueError(
                f"Static analysis violation in generated code: {message}"
            )


# ---------------------------------------------------------------------------
# Bridge state machine
# ---------------------------------------------------------------------------


class BridgeState(Enum):
    """States of the WebSocket connection to the Unity Editor Bridge."""

    DISCONNECTED = auto()
    CONNECTED = auto()
    WAITING_FOR_BRIDGE = auto()


# ---------------------------------------------------------------------------
# WebSocket import shim (allows patching in tests)
# ---------------------------------------------------------------------------

try:
    from websockets.asyncio.client import connect as websockets_connect  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — only missing in CI without websockets
    websockets_connect = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# UnityTool
# ---------------------------------------------------------------------------


class UnityTool:
    """Async WebSocket client for the C# JarvisEditorBridge.

    Usage::

        tool = UnityTool()
        await tool.connect()
        result = await tool.create_project("/path/to/MyGame")
        await tool.write_script(
            "Assets/JarvisGenerated/Scripts/Player.cs",
            cs_source,
        )
        # ... wait for domain-reload heartbeat automatically ...
        errors = await tool.compile()
        await tool.disconnect()
    """

    def __init__(self, port: int = 8091) -> None:
        self._port = port
        self._state = BridgeState.DISCONNECTED
        self._ws: Any = None
        self._ws_cm: Any = None
        self._request_id = 0
        self._ready_event: asyncio.Event = asyncio.Event()
        self._listener_task: asyncio.Task[None] | None = None
        self._pending_rpc: dict[str, asyncio.Future[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> BridgeState:
        """Current BridgeState."""
        return self._state

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket connection to the bridge and start heartbeat listener."""
        if websockets_connect is None:  # pragma: no cover
            raise RuntimeError(
                "websockets package is not installed. "
                "Run: pip install 'websockets>=14.0'"
            )
        uri = f"ws://localhost:{self._port}/jarvis"
        logger.info("UnityTool: connecting to %s", uri)
        # websockets.asyncio.client.connect is an async context manager;
        # we enter it to get the connection object.
        self._ws_cm = websockets_connect(uri)
        self._ws = await self._ws_cm.__aenter__()

        # ── Authenticate BEFORE starting the listener ──────────────────
        # Uses direct send/recv to avoid a race condition where the
        # listener task could consume the auth response before the RPC
        # future is registered.
        secret = os.environ.get("JARVIS_BRIDGE_SECRET", "jarvis-dev-secret")
        auth_request = json.dumps({
            "jsonrpc": "2.0",
            "id": "auth",
            "method": "authenticate",
            "params": {"token": secret},
        })
        await self._ws.send(auth_request)
        raw_auth = await self._ws.recv()
        try:
            auth_msg: dict[str, Any] = json.loads(raw_auth)
        except (json.JSONDecodeError, TypeError):
            auth_msg = {}
        auth_result = auth_msg.get("result")
        if not isinstance(auth_result, dict) or not auth_result.get("authenticated"):
            await self._ws.close()
            self._ws = None
            self._ws_cm = None
            raise ConnectionError("Unity bridge authentication failed")

        self._state = BridgeState.CONNECTED
        self._ready_event.set()
        self._listener_task = asyncio.create_task(
            self._listen_loop(), name="unity-bridge-listener"
        )
        logger.info("UnityTool: connected and authenticated, state=%s", self._state)

    async def disconnect(self) -> None:
        """Close the WebSocket and cancel the heartbeat listener."""
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None
        if self._ws_cm is not None:
            try:
                await self._ws_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._ws_cm = None
            self._ws = None
        elif self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        self._state = BridgeState.DISCONNECTED
        logger.info("UnityTool: disconnected")

    # ------------------------------------------------------------------
    # Core RPC
    # ------------------------------------------------------------------

    async def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send a JSON-RPC 2.0 request and return the parsed ``result``.

        Raises:
            ConnectionError: When state is DISCONNECTED.
            asyncio.TimeoutError: When WAITING_FOR_BRIDGE and ready signal
                does not arrive within 30 seconds.
            RuntimeError: When the bridge returns a JSON-RPC ``error`` object.
        """
        if self._state == BridgeState.DISCONNECTED:
            raise ConnectionError("UnityTool is not connected to the bridge.")
        if self._state == BridgeState.WAITING_FOR_BRIDGE:
            logger.debug("UnityTool: waiting for bridge ready signal…")
            await asyncio.wait_for(self._ready_event.wait(), timeout=30.0)

        return await self._send_rpc(method, params)

    async def _send_rpc(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Low-level JSON-RPC send — no state checks (caller is responsible).

        Increments request counter, serialises the request, sends over WS,
        and parses the response.  Raises RuntimeError on JSON-RPC error.
        """
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": str(self._request_id),
            "method": method,
            "params": params or {},
        }
        raw_request = json.dumps(request)
        logger.debug("UnityTool -> %s", raw_request)
        await self._ws.send(raw_request)
        # Wait for the response via the pending-response future.
        # The single reader loop (_listen_loop) dispatches messages to either
        # _pending_rpc or the heartbeat handler based on the 'id' field.
        req_id = str(self._request_id)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_rpc[req_id] = future
        try:
            response = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            raise RuntimeError(f"RPC timeout waiting for response to {method}") from None
        finally:
            self._pending_rpc.pop(req_id, None)
        logger.debug("UnityTool <- %s", response)
        if "error" in response:
            raise RuntimeError(response["error"]["message"])
        return response.get("result")

    # ------------------------------------------------------------------
    # High-level commands
    # ------------------------------------------------------------------

    async def write_script(self, rel_path: str, content: str) -> Any:
        """Write a C# script inside the path jail.

        Enforces path jail and static analysis BEFORE sending to bridge.
        Transitions to WAITING_FOR_BRIDGE because Unity will trigger a domain
        reload after the script is written.

        Raises:
            PermissionError: Path outside Assets/JarvisGenerated/.
            ValueError: Code contains forbidden C# API patterns.
        """
        _assert_in_jail(rel_path)
        _assert_safe_code(content)
        # Send the command first (must be CONNECTED or it will raise).
        # Then transition to WAITING_FOR_BRIDGE so subsequent calls block until
        # the domain-reload heartbeat arrives.  We use _send_rpc directly to
        # bypass the WAITING_FOR_BRIDGE guard in call().
        if self._state == BridgeState.DISCONNECTED:
            raise ConnectionError("UnityTool is not connected to the bridge.")
        # Pre-set WAITING state and clear event BEFORE sending to prevent race
        # where heartbeat arrives between send and state transition.
        self._state = BridgeState.WAITING_FOR_BRIDGE
        self._ready_event.clear()
        logger.info("UnityTool: entering WAITING_FOR_BRIDGE for %r", rel_path)
        result = await self._send_rpc(
            "WriteScript", {"path": rel_path, "content": content}
        )
        return result

    async def compile(self) -> Any:
        """Trigger Unity compilation and return the result (errors + warnings)."""
        return await self.call("CompileProject", {})

    async def create_project(self, project_path: str) -> Any:
        """Create a new Unity project at *project_path*."""
        return await self.call("CreateProject", {"path": project_path})

    # ------------------------------------------------------------------
    # Heartbeat listener (background task)
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Single reader loop: dispatches messages to RPC futures or heartbeat handler.

        JSON-RPC responses (have 'id') go to pending RPC futures.
        Status messages (have 'status') go to the heartbeat handler.
        This prevents concurrent WebSocket reads between _send_rpc and the listener.
        """
        try:
            async for raw in self._ws:
                try:
                    msg: dict[str, Any] = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Dispatch: RPC response, stale RPC, or heartbeat
                msg_id = msg.get("id")
                if msg_id and msg_id in self._pending_rpc:
                    future = self._pending_rpc.pop(msg_id)
                    if not future.done():
                        future.set_result(msg)
                elif msg_id:
                    logger.debug("Stale RPC response id=%s (no pending handler)", msg_id)
                else:
                    await self._handle_heartbeat_message(raw)
        except Exception:  # noqa: BLE001
            logger.debug("UnityTool: listener loop closed")
            self._state = BridgeState.DISCONNECTED
            # Fail any pending RPCs
            for future in self._pending_rpc.values():
                if not future.done():
                    future.set_exception(ConnectionError("Bridge disconnected"))
            self._pending_rpc.clear()

    async def _handle_heartbeat_message(self, raw: str) -> None:
        """Process a single raw heartbeat message."""
        try:
            msg: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("status") == "ready":
            logger.info("UnityTool: bridge ready — transitioning to CONNECTED")
            self._state = BridgeState.CONNECTED
            self._ready_event.set()

    # ------------------------------------------------------------------
    # ToolSpec registration
    # ------------------------------------------------------------------

    async def _safe_dispatch(self, **kwargs: Any) -> Any:
        """Dispatch tool calls with full security enforcement.

        Routes WriteScript through write_script() (which enforces path jail and
        static analysis).  Other methods pass through call() directly.
        """
        method = kwargs.pop("method", "")
        params = kwargs.pop("params", kwargs)
        if method == "WriteScript":
            path = params.get("path", "")
            code = params.get("code", "")
            return await self.write_script(path, code)
        elif method == "CreateProject":
            return await self.create_project(**params)
        elif method == "Compile":
            return await self.compile()
        else:
            return await self.call(method, params)

    def get_tool_spec(self) -> "ToolSpec":  # noqa: F821
        """Return a ToolSpec for registration in the agent ToolRegistry."""
        from jarvis_engine.agent.tool_registry import ToolSpec  # lazy import

        return ToolSpec(
            name="unity",
            description=(
                "Execute Unity Editor commands via the WebSocket bridge. "
                "Supports creating projects, writing scripts, and triggering "
                "compilation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": (
                            "JSON-RPC method name: WriteScript, CompileProject, "
                            "CreateProject."
                        ),
                    },
                    "params": {
                        "type": "object",
                        "description": "Method-specific parameters.",
                    },
                },
                "required": ["method"],
            },
            execute=self._safe_dispatch,
            requires_approval=False,
            is_destructive=False,
        )
