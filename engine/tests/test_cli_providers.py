"""Tests for gateway.cli_providers — CLI-based LLM provider integration."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch


from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.gateway.cli_providers import (
    CLIProviderInfo,
    _build_claude_cli_prompt,
    _build_messages_text,
    call_claude_cli,
    call_codex_cli,
    call_gemini_cli,
    call_kimi_cli,
    call_cli_provider,
    detect_cli_providers,
)


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

class TestBuildMessagesText:
    def test_single_user_message(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        assert _build_messages_text(msgs) == "hello"

    def test_system_plus_user(self) -> None:
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
        text = _build_messages_text(msgs)
        assert "You are helpful." in text
        assert "hi" in text
        assert text.index("You are helpful.") < text.index("hi")

    def test_multi_turn_conversation(self) -> None:
        msgs = [
            {"role": "user", "content": "what is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "and 3+3?"},
        ]
        text = _build_messages_text(msgs)
        assert "what is 2+2?" in text
        assert "Assistant: 4" in text
        assert "and 3+3?" in text

    def test_empty_messages(self) -> None:
        assert _build_messages_text([]) == ""

    @patch.dict("os.environ", {"JARVIS_CLI_PROMPT_MAX_CHARS": "6000"}, clear=False)
    def test_large_conversation_compacts_with_checkpoint(self) -> None:
        msgs = [{"role": "system", "content": "System context " + ("x" * 4000)}]
        for i in range(40):
            msgs.append({"role": "user", "content": f"user turn {i} " + ("y" * 280)})
            msgs.append({"role": "assistant", "content": f"assistant turn {i} " + ("z" * 280)})
        msgs.append({"role": "user", "content": "FINAL_USER_TURN keep this context"})

        text = _build_messages_text(msgs)
        assert "Conversation checkpoint from earlier turns" in text
        assert "FINAL_USER_TURN keep this context" in text
        # Soft upper bound: formatter adds role labels/preamble around compacted payload.
        assert len(text) <= 9000

    @patch.dict("os.environ", {"JARVIS_CLI_PROMPT_MAX_CHARS": "6000"}, clear=False)
    def test_claude_prompt_compaction_keeps_recent_turn(self) -> None:
        msgs = [{"role": "system", "content": "You are Jarvis. " + ("persona " * 300)}]
        for i in range(30):
            msgs.append({"role": "user", "content": f"historic user {i} " + ("a" * 220)})
            msgs.append({"role": "assistant", "content": f"historic assistant {i} " + ("b" * 220)})
        msgs.append({"role": "user", "content": "LATEST_USER_REQUEST please continue"})

        text = _build_claude_cli_prompt(msgs)
        assert "LATEST_USER_REQUEST please continue" in text
        assert len(text) <= 9000


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

class TestDetectProviders:
    @patch("jarvis_engine.gateway.cli_providers._detect_cli")
    def test_detects_installed_clis(self, mock_detect: MagicMock) -> None:
        mock_detect.side_effect = lambda name: {
            "claude": "/usr/bin/claude",
            "codex": "/usr/bin/codex",
            "gemini": None,
            "kimi": None,
        }.get(name)

        result = detect_cli_providers()
        assert result["claude-cli"].available is True
        assert result["codex-cli"].available is True
        assert result["gemini-cli"].available is False
        assert result["kimi-cli"].available is False

    @patch("jarvis_engine.gateway.cli_providers._detect_cli", return_value=None)
    def test_none_available(self, mock_detect: MagicMock) -> None:
        result = detect_cli_providers()
        assert all(not info.available for info in result.values())


# ---------------------------------------------------------------------------
# Claude CLI
# ---------------------------------------------------------------------------

class TestCallClaudeCli:
    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_success_json_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout=json.dumps({"result": "Hello from Claude!", "cost_usd": 0.01}),
            stderr="",
        )
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["text"] == "Hello from Claude!"
        assert result["provider"] == "claude-cli"
        assert result["cost_usd"] == 0.01

        # Verify CLAUDECODE env var is removed
        env = mock_run.call_args.kwargs.get("env", {})
        assert "CLAUDECODE" not in env

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_non_json_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout="Plain text response",
            stderr="",
        )
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["text"] == "Plain text response"

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_event_array_output_uses_result_payload(self, mock_run: MagicMock) -> None:
        payload = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Intermediate"}],
                },
            },
            {
                "type": "result",
                "result": "Final answer from Claude",
                "total_cost_usd": 0.123,
            },
        ]
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["text"] == "Final answer from Claude"
        assert result["cost_usd"] == 0.123

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_event_array_without_result_uses_assistant_text(self, mock_run: MagicMock) -> None:
        payload = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Assistant answer"}],
                },
            },
        ]
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["text"] == "Assistant answer"

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_failure_exit_code(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=1,
            stdout="",
            stderr="auth error",
        )
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "exit 1" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("claude", 120)
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "timeout" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError()
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_os_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("Argument list too long")
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "OS error" in result["error"]

    @patch.dict("os.environ", {"JARVIS_CLAUDE_CLI_MAX_BUDGET_USD": ""}, clear=False)
    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_budget_flag_omitted_when_env_not_set(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout=json.dumps({"result": "ok", "cost_usd": 0.0}),
            stderr="",
        )
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        cmd = mock_run.call_args[0][0]
        assert "--max-budget-usd" not in cmd

    @patch.dict("os.environ", {"JARVIS_CLAUDE_CLI_MAX_BUDGET_USD": "0.25"}, clear=False)
    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_budget_flag_added_when_env_set(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout=json.dumps({"result": "ok", "cost_usd": 0.0}),
            stderr="",
        )
        result = call_claude_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        cmd = mock_run.call_args[0][0]
        assert "--max-budget-usd" in cmd
        idx = cmd.index("--max-budget-usd") + 1
        assert cmd[idx] == "0.25"


# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------

class TestCallCodexCli:
    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_success_with_output_file(self, mock_run: MagicMock) -> None:
        # Simulate codex writing to output file
        def side_effect(*args, **kwargs):
            # Find the -o argument and write to that file
            cmd = args[0]
            out_idx = cmd.index("-o") + 1
            out_path = cmd[out_idx]
            with open(out_path, "w") as f:
                f.write("Codex response here")
            return MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = call_codex_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["text"] == "Codex response here"
        assert result["provider"] == "codex-cli"

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("codex", 120)
        result = call_codex_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "timeout" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_failure_exit_code(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=1, stdout="", stderr="codex error"
        )
        result = call_codex_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "exit 1" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError()
        result = call_codex_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_os_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("Argument list too long")
        result = call_codex_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "OS error" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_success_but_empty_response(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0, stdout="", stderr=""
        )
        result = call_codex_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "empty response" in result["error"]


# ---------------------------------------------------------------------------
# Gemini CLI
# ---------------------------------------------------------------------------

class TestCallGeminiCli:
    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout="Gemini says hello!",
            stderr="",
        )
        result = call_gemini_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["text"] == "Gemini says hello!"
        assert result["provider"] == "gemini-cli"

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=1,
            stdout="",
            stderr="rate limited",
        )
        result = call_gemini_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("gemini", 120)
        result = call_gemini_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "timeout" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError()
        result = call_gemini_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_os_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("Argument list too long")
        result = call_gemini_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "OS error" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_empty_response(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0, stdout="", stderr=""
        )
        result = call_gemini_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert result["error"] == "empty response"


# ---------------------------------------------------------------------------
# Kimi CLI
# ---------------------------------------------------------------------------

class TestCallKimiCli:
    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0,
            stdout="Kimi response",
            stderr="",
        )
        result = call_kimi_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is True
        assert result["text"] == "Kimi response"
        assert result["provider"] == "kimi-cli"

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=1, stdout="", stderr="kimi error"
        )
        result = call_kimi_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "exit 1" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("kimi", 120)
        result = call_kimi_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "timeout" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError()
        result = call_kimi_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_os_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("Argument list too long")
        result = call_kimi_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "OS error" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_empty_response(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0, stdout="", stderr=""
        )
        result = call_kimi_cli([{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert result["error"] == "empty response"


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------

class TestCallCliProvider:
    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_dispatches_to_claude(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0, stdout="ok", stderr=""
        )
        result = call_cli_provider("claude-cli", [{"role": "user", "content": "hi"}])
        assert result["provider"] == "claude-cli"

    def test_unknown_provider(self) -> None:
        result = call_cli_provider("nonexistent-cli", [{"role": "user", "content": "hi"}])
        assert result["success"] is False
        assert "unknown" in result["error"]

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_model_override_forwarded_to_claude(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0, stdout="ok", stderr=""
        )
        call_cli_provider("claude-cli", [{"role": "user", "content": "hi"}], model="sonnet")
        # Verify --model sonnet was passed in the subprocess command
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        model_idx = cmd.index("--model") + 1
        assert cmd[model_idx] == "sonnet"

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_model_override_forwarded_to_codex(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0, stdout="", stderr=""
        )
        call_cli_provider("codex-cli", [{"role": "user", "content": "hi"}], model="gpt-4o")
        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        model_idx = cmd.index("-m") + 1
        assert cmd[model_idx] == "gpt-4o"

    @patch("jarvis_engine.gateway.cli_providers.subprocess.run")
    def test_model_override_ignored_for_gemini(self, mock_run: MagicMock) -> None:
        """Model override should NOT be forwarded to gemini/kimi (they don't accept it)."""
        mock_run.return_value = MagicMock(spec=subprocess.CompletedProcess,
            returncode=0, stdout="hello", stderr=""
        )
        result = call_cli_provider("gemini-cli", [{"role": "user", "content": "hi"}], model="custom-model")
        assert result["success"] is True
        # Gemini cmd should not have the model parameter
        cmd = mock_run.call_args[0][0]
        assert "custom-model" not in cmd


# ---------------------------------------------------------------------------
# ModelGateway integration
# ---------------------------------------------------------------------------

class TestGatewayCliIntegration:
    """Test that ModelGateway properly wires CLI providers."""

    @patch("jarvis_engine.gateway.cli_providers._detect_cli")
    @patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False)
    def test_gateway_detects_cli_providers(self, mock_detect: MagicMock) -> None:
        mock_detect.side_effect = lambda name: {
            "claude": "/usr/bin/claude",
            "codex": "/usr/bin/codex",
            "gemini": None,
            "kimi": None,
        }.get(name)

        from jarvis_engine.gateway.models import ModelGateway
        gw = ModelGateway(groq_api_key="test")
        try:
            providers = gw.available_providers()
            assert "claude-cli" in providers
            assert "codex-cli" in providers
            assert "gemini-cli" not in providers
        finally:
            gw.close()

    @patch("jarvis_engine.gateway.cli_providers._detect_cli")
    @patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False)
    def test_available_model_names_includes_cli(self, mock_detect: MagicMock) -> None:
        mock_detect.side_effect = lambda name: {
            "claude": "/usr/bin/claude",
            "codex": None,
            "gemini": "/usr/bin/gemini",
            "kimi": None,
        }.get(name)

        from jarvis_engine.gateway.models import ModelGateway
        gw = ModelGateway(groq_api_key="test")
        try:
            models = gw.available_model_names()
            assert "claude-cli" in models
            assert "gemini-cli" in models
            assert "codex-cli" not in models
            # API models should also be present
            assert "kimi-k2" in models
        finally:
            gw.close()

    @patch("jarvis_engine.gateway.cli_providers._detect_cli")
    @patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False)
    def test_resolve_provider_for_cli_model(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = "/usr/bin/claude"

        from jarvis_engine.gateway.models import ModelGateway
        gw = ModelGateway(groq_api_key="test")
        try:
            provider = gw._resolve_provider("claude-cli")
            assert provider == "cli:claude-cli"
        finally:
            gw.close()

    @patch("jarvis_engine.gateway.cli_providers._detect_cli")
    @patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False)
    def test_resolve_provider_claude_cli_not_hijacked_by_anthropic(self, mock_detect: MagicMock) -> None:
        """claude-cli must route to CLI, NOT to Anthropic API, even when Anthropic is configured."""
        mock_detect.return_value = "/usr/bin/claude"

        from jarvis_engine.gateway.models import ModelGateway
        gw = ModelGateway(groq_api_key="test", anthropic_api_key="sk-ant-test")
        try:
            provider = gw._resolve_provider("claude-cli")
            assert provider == "cli:claude-cli", (
                f"claude-cli should route to CLI, not {provider}"
            )
            # Regular claude models should still go to Anthropic
            provider2 = gw._resolve_provider("claude-sonnet")
            assert provider2 == "anthropic"
        finally:
            gw.close()

    @patch("jarvis_engine.gateway.cli_providers._detect_cli")
    @patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False)
    def test_complete_does_not_remap_claude_cli_to_cloud(self, mock_detect: MagicMock) -> None:
        """claude-cli must NOT be remapped to a cloud model when Anthropic is unavailable."""
        mock_detect.return_value = "/usr/bin/claude"

        from jarvis_engine.gateway.models import ModelGateway
        # No anthropic_api_key — previously this would remap claude-cli to kimi-k2
        gw = ModelGateway(groq_api_key="test")
        try:
            provider = gw._resolve_provider("claude-cli")
            assert provider == "cli:claude-cli", (
                f"claude-cli should route to CLI even without Anthropic API key, not {provider}"
            )
        finally:
            gw.close()

    @patch("jarvis_engine.gateway.models.detect_cli_providers")
    def test_gateway_refreshes_cli_providers_without_restart(self, mock_detect: MagicMock) -> None:
        codex_info = CLIProviderInfo(
            name="Codex CLI",
            executable="/usr/bin/codex",
            available=True,
            model="codex-cli",
        )
        state = {"installed": False}

        def _fake_detect() -> dict[str, CLIProviderInfo]:
            if state["installed"]:
                return {"codex-cli": codex_info}
            return {}

        mock_detect.side_effect = _fake_detect

        from jarvis_engine.gateway.models import ModelGateway
        gw = ModelGateway()
        try:
            assert "codex-cli" not in gw.check_cli()
            state["installed"] = True
            gw._refresh_cli_providers(force=True)
            assert "codex-cli" in gw.check_cli()
        finally:
            gw.close()


# ---------------------------------------------------------------------------
# Classifier route-to-model resolution
# ---------------------------------------------------------------------------

class TestClassifierModelResolution:
    """Test that IntentClassifier resolves models based on availability."""

    def test_resolve_model_uses_primary_when_available(self) -> None:
        from jarvis_engine._shared import get_local_model
        from jarvis_engine.gateway.classifier import IntentClassifier
        local = get_local_model()
        available = {local, "codex-cli", "gemini-cli"}
        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed.return_value = [0.0] * 384
        mock_embed.embed_query.return_value = [0.0] * 384

        cls = IntentClassifier(mock_embed)
        model = cls._resolve_model_for_route("math_logic", available)
        assert model == local  # Local 9B is primary for math_logic

    def test_resolve_model_falls_back_when_primary_unavailable(self) -> None:
        from jarvis_engine.gateway.classifier import IntentClassifier
        # No local models available — should fall back to cloud CLIs
        available = {"codex-cli", "gemini-cli"}
        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed.return_value = [0.0] * 384
        mock_embed.embed_query.return_value = [0.0] * 384

        cls = IntentClassifier(mock_embed)
        model = cls._resolve_model_for_route("math_logic", available)
        assert model == "codex-cli"  # First cloud fallback for math_logic

    def test_resolve_model_ultimate_fallback(self) -> None:
        from jarvis_engine._shared import get_local_model
        from jarvis_engine.gateway.classifier import IntentClassifier
        available: set[str] = set()  # Nothing available
        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed.return_value = [0.0] * 384
        mock_embed.embed_query.return_value = [0.0] * 384

        cls = IntentClassifier(mock_embed)
        model = cls._resolve_model_for_route("math_logic", available)
        assert model == get_local_model()  # Ultimate fallback is local model

    def test_resolve_model_respects_available_set(self) -> None:
        from jarvis_engine.gateway.classifier import IntentClassifier
        available = {"gemini-cli"}  # Only gemini available
        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed.return_value = [0.0] * 384
        mock_embed.embed_query.return_value = [0.0] * 384

        cls = IntentClassifier(mock_embed)
        model = cls._resolve_model_for_route("math_logic", available)
        # Local not available, codex-cli not available, claude-cli not available,
        # gemini-cli IS in fallback list and available
        assert model == "gemini-cli"

    def test_resolve_provider_unavailable_cli_falls_to_ollama(self) -> None:
        """CLI model that's not installed should route to Ollama, not Anthropic."""
        from jarvis_engine.gateway.models import ModelGateway
        with patch("jarvis_engine.gateway.cli_providers._detect_cli", return_value=None):
            gw = ModelGateway(anthropic_api_key="sk-ant-test", groq_api_key="test")
            try:
                # claude-cli is in CLI_MODEL_MAP but no CLIs are installed
                provider = gw._resolve_provider("claude-cli")
                assert provider == "ollama", (
                    f"Unavailable CLI model should route to Ollama, not {provider}"
                )
            finally:
                gw.close()

    def test_resolve_model_no_available_set_uses_primary(self) -> None:
        from jarvis_engine._shared import get_local_model
        from jarvis_engine.gateway.classifier import IntentClassifier
        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed.return_value = [0.0] * 384
        mock_embed.embed_query.return_value = [0.0] * 384

        cls = IntentClassifier(mock_embed)
        model = cls._resolve_model_for_route("complex", None)
        assert model == get_local_model()  # Local 9B for complex
