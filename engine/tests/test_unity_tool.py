"""Tests for UnityTool: path jail, static analysis guard, and WebSocket client.

TDD: tests written first, then implementation.
Async tests use asyncio.run() -- no pytest-asyncio required (matches project pattern).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis_engine.agent.tools.unity_tool import (
    BridgeState,
    UnityTool,
    _assert_in_jail,
    _assert_safe_code,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Auth success response returned by mock recv() during connect()
_AUTH_OK = json.dumps(
    {"jsonrpc": "2.0", "id": "auth", "result": {"authenticated": True}}
)


def _make_mock_ws(response_payload: str = "") -> AsyncMock:
    """Create a mock WebSocket that feeds responses through the async iterator.

    The listener loop reads via ``async for raw in self._ws``, so the mock
    must support async iteration.  Messages are queued and yielded one at a
    time.  After all messages are consumed, the iterator blocks forever
    (simulating an open connection) so the listener doesn't exit early.

    ``recv()`` returns the auth success response consumed by connect()'s
    authentication handshake.  The actual test response is fed through the
    async iterator to the listener loop.
    """
    messages: list[str] = [response_payload] if response_payload else []
    _block_forever: asyncio.Event = asyncio.Event()  # never set

    async def _aiter():
        for msg in messages:
            yield msg
        # Keep the iterator alive so the listener doesn't exit
        await _block_forever.wait()

    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    # recv() is called once during connect() for auth handshake
    mock_ws.recv = AsyncMock(return_value=_AUTH_OK)
    mock_ws.__aiter__ = lambda self=None: _aiter()
    mock_ws.close = AsyncMock()
    return mock_ws


class _AsyncContextManager:
    """Async context manager wrapping a mock WebSocket."""

    def __init__(self, ws: AsyncMock) -> None:
        self._ws = ws

    async def __aenter__(self) -> AsyncMock:
        return self._ws

    async def __aexit__(self, *args: object) -> None:
        pass


def _async_context_manager(ws: AsyncMock) -> _AsyncContextManager:
    return _AsyncContextManager(ws)


# ---------------------------------------------------------------------------
# TestPathJail
# ---------------------------------------------------------------------------


class TestPathJail:
    """Tests for _assert_in_jail path security function."""

    def test_path_jail_valid(self) -> None:
        """Valid path inside jail does not raise."""
        _assert_in_jail("Assets/JarvisGenerated/Scripts/Player.cs")

    def test_path_jail_subdirectory(self) -> None:
        """Nested subdirectory inside jail does not raise."""
        _assert_in_jail("Assets/JarvisGenerated/Tests/TestPlayer.cs")

    def test_path_jail_outside(self) -> None:
        """Path outside jail raises PermissionError."""
        with pytest.raises(PermissionError):
            _assert_in_jail("Assets/Scripts/Evil.cs")

    def test_path_jail_traversal(self) -> None:
        """Forward-slash traversal raises PermissionError."""
        with pytest.raises(PermissionError):
            _assert_in_jail("Assets/JarvisGenerated/../../Evil.cs")

    def test_path_jail_backslash_traversal(self) -> None:
        """Backslash traversal raises PermissionError."""
        with pytest.raises(PermissionError):
            _assert_in_jail("Assets\\JarvisGenerated\\..\\..\\Evil.cs")

    def test_path_jail_empty(self) -> None:
        """Empty path raises PermissionError."""
        with pytest.raises(PermissionError):
            _assert_in_jail("")

    def test_path_jail_sibling_prefix(self) -> None:
        """Path that starts with jail prefix but is actually a sibling raises."""
        with pytest.raises(PermissionError):
            _assert_in_jail("Assets/JarvisGenerated2/Scripts/Evil.cs")

    def test_path_jail_root_only(self) -> None:
        """Exact jail root without trailing file raises (no file component)."""
        with pytest.raises(PermissionError):
            _assert_in_jail("Assets/JarvisGeneratedEvil.cs")

    def test_path_jail_directory_itself(self) -> None:
        """Exact jail directory path (no trailing slash) does not raise."""
        _assert_in_jail("Assets/JarvisGenerated")


# ---------------------------------------------------------------------------
# TestStaticAnalysis
# ---------------------------------------------------------------------------


class TestStaticAnalysis:
    """Tests for _assert_safe_code static analysis guard."""

    def test_static_analysis_process_start(self) -> None:
        """Process.Start raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code('Process.Start("cmd")')

    def test_static_analysis_diagnostics_process(self) -> None:
        """System.Diagnostics.Process raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("System.Diagnostics.Process p = new()")

    def test_static_analysis_file_delete(self) -> None:
        """File.Delete raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("File.Delete(path)")

    def test_static_analysis_directory_delete(self) -> None:
        """Directory.Delete raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("Directory.Delete(path, true)")

    def test_static_analysis_assembly_loadfrom(self) -> None:
        """Assembly.LoadFrom raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("Assembly.LoadFrom(dll)")

    def test_static_analysis_assembly_load(self) -> None:
        """Assembly.Load with space before paren raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("Assembly.Load (bytes)")

    def test_static_analysis_path_traversal(self) -> None:
        """Path traversal pattern in code raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code('var p = Application.dataPath + "/../etc"')

    def test_static_analysis_safe_code(self) -> None:
        """Normal MonoBehaviour code does not raise."""
        _assert_safe_code(
            "public class Player : MonoBehaviour { void Start() { } }"
        )

    def test_static_analysis_delete_asset(self) -> None:
        """AssetDatabase.DeleteAsset raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("AssetDatabase.DeleteAsset(path)")

    def test_static_analysis_getmethod_invoke(self) -> None:
        """GetMethod...Invoke chain raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code('type.GetMethod("Run").Invoke(null, args)')

    def test_static_analysis_file_util_delete(self) -> None:
        """FileUtil.DeleteFileOrDirectory raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("FileUtil.DeleteFileOrDirectory(path)")

    def test_static_analysis_assembly_load_no_space(self) -> None:
        """Assembly.LoadFrom (no space variant) raises ValueError."""
        with pytest.raises(ValueError):
            _assert_safe_code("Assembly.LoadFrom(somePath)")


# ---------------------------------------------------------------------------
# TestBridgeStateMachine
# ---------------------------------------------------------------------------


class TestBridgeStateMachine:
    """Tests for UnityTool BridgeState machine and WebSocket client."""

    def test_bridge_state_initial(self) -> None:
        """UnityTool starts in DISCONNECTED state."""
        tool = UnityTool()
        assert tool.state == BridgeState.DISCONNECTED

    def test_connect_transitions_to_connected(self) -> None:
        """After connect(), state is CONNECTED."""

        async def _run() -> None:
            tool = UnityTool()
            mock_ws = _make_mock_ws()
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
            assert tool.state == BridgeState.CONNECTED
            await tool.disconnect()

        asyncio.run(_run())

    def test_call_sends_jsonrpc(self) -> None:
        """call() sends valid JSON-RPC 2.0 payload."""

        async def _run() -> None:
            tool = UnityTool()
            response_payload = '{"jsonrpc":"2.0","id":"1","result":"ok"}'
            mock_ws = _make_mock_ws(response_payload)
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                result = await tool.call("SomeMethod", {"arg": 1})

            sent = json.loads(mock_ws.send.call_args[0][0])
            assert sent["jsonrpc"] == "2.0"
            assert sent["method"] == "SomeMethod"
            assert sent["params"] == {"arg": 1}
            assert "id" in sent
            assert result == "ok"
            await tool.disconnect()

        asyncio.run(_run())

    def test_call_returns_parsed_response(self) -> None:
        """call() returns the result field from JSON-RPC response."""

        async def _run() -> None:
            tool = UnityTool()
            response_payload = (
                '{"jsonrpc":"2.0","id":"1","result":{"status":"done"}}'
            )
            mock_ws = _make_mock_ws(response_payload)
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                result = await tool.call("SomeMethod")

            assert result == {"status": "done"}
            await tool.disconnect()

        asyncio.run(_run())

    def test_call_raises_on_error_response(self) -> None:
        """call() raises RuntimeError when response has error field."""

        async def _run() -> None:
            tool = UnityTool()
            response_payload = (
                '{"jsonrpc":"2.0","id":"1",'
                '"error":{"code":-32600,"message":"bad"}}'
            )
            mock_ws = _make_mock_ws(response_payload)
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                with pytest.raises(RuntimeError, match="bad"):
                    await tool.call("SomeMethod")
            await tool.disconnect()

        asyncio.run(_run())

    def test_write_script_enforces_jail(self) -> None:
        """write_script with outside path raises PermissionError without WS call."""

        async def _run() -> None:
            tool = UnityTool()
            mock_ws = _make_mock_ws()
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                # Reset send call count so we only check calls after connect/auth
                mock_ws.send.reset_mock()
                with pytest.raises(PermissionError):
                    await tool.write_script("Assets/Evil.cs", "safe code")
                mock_ws.send.assert_not_called()
            await tool.disconnect()

        asyncio.run(_run())

    def test_write_script_enforces_static_analysis(self) -> None:
        """write_script with dangerous code raises ValueError without WS call."""

        async def _run() -> None:
            tool = UnityTool()
            mock_ws = _make_mock_ws()
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                # Reset send call count so we only check calls after connect/auth
                mock_ws.send.reset_mock()
                with pytest.raises(ValueError):
                    await tool.write_script(
                        "Assets/JarvisGenerated/Bad.cs",
                        'Process.Start("cmd")',
                    )
                mock_ws.send.assert_not_called()
            await tool.disconnect()

        asyncio.run(_run())

    def test_write_script_enters_waiting(self) -> None:
        """write_script with valid path and safe code transitions to WAITING_FOR_BRIDGE."""

        async def _run() -> None:
            tool = UnityTool()
            response_payload = '{"jsonrpc":"2.0","id":"1","result":"written"}'
            mock_ws = _make_mock_ws(response_payload)
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                await tool.write_script(
                    "Assets/JarvisGenerated/Scripts/Player.cs",
                    "public class Player : MonoBehaviour {}",
                )
            # After write, state should be WAITING_FOR_BRIDGE (no heartbeat received)
            assert tool.state == BridgeState.WAITING_FOR_BRIDGE
            await tool.disconnect()

        asyncio.run(_run())

    def test_call_blocked_in_waiting_state(self) -> None:
        """When state is WAITING_FOR_BRIDGE, call() times out waiting for ready_event."""

        async def _run() -> None:
            tool = UnityTool()
            mock_ws = _make_mock_ws('{"jsonrpc":"2.0","id":"1","result":"ok"}')
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                # Force into WAITING_FOR_BRIDGE state directly
                tool._state = BridgeState.WAITING_FOR_BRIDGE
                tool._ready_event.clear()
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(tool.call("SomeMethod"), timeout=0.05)
            await tool.disconnect()

        asyncio.run(_run())

    def test_heartbeat_exits_waiting(self) -> None:
        """Receiving {status: ready} sets state back to CONNECTED."""
        tool = UnityTool()
        tool._state = BridgeState.WAITING_FOR_BRIDGE
        tool._ready_event.clear()

        tool._handle_heartbeat_msg({"status": "ready"})

        assert tool.state == BridgeState.CONNECTED
        assert tool._ready_event.is_set()

    def test_disconnect_handler(self) -> None:
        """When disconnect() is called, state transitions to DISCONNECTED."""

        async def _run() -> None:
            tool = UnityTool()
            mock_ws = _make_mock_ws()
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
            await tool.disconnect()
            assert tool.state == BridgeState.DISCONNECTED

        asyncio.run(_run())

    def test_compile_returns_errors(self) -> None:
        """compile() sends CompileProject command and returns result."""

        async def _run() -> None:
            tool = UnityTool()
            response_payload = (
                '{"jsonrpc":"2.0","id":"1","result":{"errors":[],"warnings":[]}}'
            )
            mock_ws = _make_mock_ws(response_payload)
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                result = await tool.compile()

            assert result == {"errors": [], "warnings": []}
            sent = json.loads(mock_ws.send.call_args[0][0])
            assert sent["method"] == "CompileProject"
            await tool.disconnect()

        asyncio.run(_run())

    def test_create_project_sends_command(self) -> None:
        """create_project() sends CreateProject JSON-RPC command."""

        async def _run() -> None:
            tool = UnityTool()
            response_payload = '{"jsonrpc":"2.0","id":"1","result":"created"}'
            mock_ws = _make_mock_ws(response_payload)
            with patch(
                "jarvis_engine.agent.tools.unity_tool.websockets_connect",
                return_value=_async_context_manager(mock_ws),
            ):
                await tool.connect()
                result = await tool.create_project("/path/to/project")

            assert result == "created"
            sent = json.loads(mock_ws.send.call_args[0][0])
            assert sent["method"] == "CreateProject"
            assert sent["params"]["path"] == "/path/to/project"
            await tool.disconnect()

        asyncio.run(_run())

    def test_get_tool_spec_returns_valid_spec(self) -> None:
        """get_tool_spec() returns a ToolSpec with correct schema."""
        from jarvis_engine.agent.tool_registry import ToolSpec

        tool = UnityTool()
        spec = tool.get_tool_spec()

        assert isinstance(spec, ToolSpec)
        assert spec.name == "unity"
        assert "method" in spec.parameters.get("properties", {})
        assert callable(spec.execute)

    def test_call_raises_when_disconnected(self) -> None:
        """call() raises ConnectionError when in DISCONNECTED state."""

        async def _run() -> None:
            tool = UnityTool()
            with pytest.raises(ConnectionError):
                await tool.call("SomeMethod")

        asyncio.run(_run())
