"""CLI-based LLM providers: invoke Claude Code, Codex, Gemini, and Kimi CLIs.

Uses subprocess to call CLI tools in non-interactive mode.  This lets Jarvis
leverage subscription-based CLI plans (Claude Code 20x Max, Codex Pro, etc.)
without needing separate API keys.

Each provider is auto-detected at import time via ``shutil.which``.

**Windows note:** npm-installed CLIs create ``.CMD`` batch wrappers that
``subprocess.run`` cannot execute by bare name (CreateProcess doesn't search
PATHEXT).  We resolve the full executable path via ``shutil.which`` at
detection time and use that path for all invocations.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Timeout for CLI calls (seconds).  LLM completions can take a while.
_DEFAULT_TIMEOUT = 120


@dataclass
class CLIProviderInfo:
    """Metadata about a CLI-based LLM provider."""

    name: str
    executable: str
    available: bool
    model: str  # default model name for routing


def _detect_cli(name: str) -> str | None:
    """Return absolute path to CLI executable, or None."""
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_CLI_CONFIGS: dict[str, dict] = {
    "claude-cli": {
        "executable": "claude",
        "model": "claude-cli",
        "display": "Claude Code CLI (Opus 4.6)",
        "default_model": "opus",  # Claude Opus 4.6 — coding, architecture
    },
    "codex-cli": {
        "executable": "codex",
        "model": "codex-cli",
        "display": "Codex CLI (GPT-5.3)",
        "default_model": "gpt-5.3-codex",  # GPT-5.3 high reasoning — math, logic
    },
    "gemini-cli": {
        "executable": "gemini",
        "model": "gemini-cli",
        "display": "Gemini CLI",
        "default_model": None,  # Uses Gemini's default model
    },
    "kimi-cli": {
        "executable": "kimi",
        "model": "kimi-cli",
        "display": "Kimi CLI",
        "default_model": None,  # Uses Kimi's default model
    },
}


def detect_cli_providers() -> dict[str, CLIProviderInfo]:
    """Detect which CLI LLM providers are installed.

    Returns a dict mapping provider key -> CLIProviderInfo.
    Only providers whose executable is found on PATH are marked available.
    The resolved absolute path is stored so .CMD wrappers work on Windows.
    """
    result: dict[str, CLIProviderInfo] = {}
    for key, cfg in _CLI_CONFIGS.items():
        exe = cfg["executable"]
        path = _detect_cli(exe)
        result[key] = CLIProviderInfo(
            name=cfg["display"],
            executable=path or exe,
            available=path is not None,
            model=cfg["model"],
        )
        # Cache resolved path for use by invocation functions
        if path:
            _RESOLVED_PATHS[key] = path
    return result


# Resolved executable paths — populated by detect_cli_providers().
# Maps provider key -> absolute path (e.g. "claude-cli" -> "C:\\...\\claude.CMD").
_RESOLVED_PATHS: dict[str, str] = {}


def _get_executable(provider_key: str, bare_name: str) -> str:
    """Return the resolved executable path, falling back to bare name.

    On Windows, .CMD wrappers require the full path for subprocess.run().
    """
    return _RESOLVED_PATHS.get(provider_key, bare_name)


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------

def _build_messages_text(messages: list[dict[str, str]]) -> str:
    """Convert chat messages to a single text prompt for CLI tools.

    System messages become a preamble, then user/assistant turns follow.
    Multi-turn history is formatted with User:/Assistant: prefixes so CLIs
    can distinguish conversation turns even though they receive flat text.
    """
    parts: list[str] = []
    system_parts: list[str] = []
    # Count user messages to detect multi-turn conversations
    user_count = sum(1 for m in messages if m.get("role", "user") == "user")
    multi_turn = user_count > 1

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            # Add "User:" prefix in multi-turn conversations so CLIs can
            # distinguish prior turns from the current query
            if multi_turn:
                parts.append(f"User: {content}")
            else:
                parts.append(content)

    prompt = ""
    if system_parts:
        prompt = "\n\n".join(system_parts) + "\n\n"
    if multi_turn and parts:
        prompt += "The following is a multi-turn conversation. Continue naturally:\n\n"
    prompt += "\n\n".join(parts)
    return prompt


def call_claude_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
    model: str = "opus",
) -> dict:
    """Call Claude Code CLI in non-interactive mode.

    Uses Opus 4.6 by default (the user's 20x Max plan).
    Pass model="sonnet" for faster, cheaper responses.

    Returns dict with keys: text, model, provider, success, error.
    """
    prompt = _build_messages_text(messages)

    # Claude Code blocks nested sessions via CLAUDECODE env var.
    # Remove it so Jarvis daemon can call claude freely.
    # Use copy() + pop() to avoid race if another thread modifies os.environ.
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = [
        _get_executable("claude-cli", "claude"),
        "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--max-turns", "1",
        "--max-budget-usd", "0.50",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=tempfile.gettempdir(),
        )
        if proc.returncode != 0:
            return {
                "text": "",
                "model": "claude-cli",
                "provider": "claude-cli",
                "success": False,
                "error": f"exit {proc.returncode}: {proc.stderr[:500]}",
            }

        # Parse JSON output
        try:
            data = json.loads(proc.stdout)
            text = data.get("result", "") or data.get("text", "") or proc.stdout
            cost = data.get("cost_usd", 0.0) or 0.0
        except (json.JSONDecodeError, ValueError, AttributeError):
            text = proc.stdout.strip()
            cost = 0.0

        return {
            "text": text,
            "model": "claude-cli",
            "provider": "claude-cli",
            "success": True,
            "error": "",
            "cost_usd": cost,
        }
    except subprocess.TimeoutExpired:
        return {
            "text": "",
            "model": "claude-cli",
            "provider": "claude-cli",
            "success": False,
            "error": f"timeout after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "text": "",
            "model": "claude-cli",
            "provider": "claude-cli",
            "success": False,
            "error": "claude CLI not found on PATH",
        }
    except OSError as exc:
        return {
            "text": "",
            "model": "claude-cli",
            "provider": "claude-cli",
            "success": False,
            "error": f"OS error (prompt too long?): {exc}",
        }


def call_codex_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
    model: str = "gpt-5.3-codex",
) -> dict:
    """Call Codex CLI in non-interactive exec mode.

    Uses GPT-5.3 (highest reasoning) by default via the user's Codex Pro plan.
    GPT-5.3 excels at math, logic, and complex reasoning tasks.

    Returns dict with keys: text, model, provider, success, error.
    """
    prompt = _build_messages_text(messages)

    # Use temp file for output to avoid parsing JSONL stream
    out_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="codex_out_"
        ) as tmp:
            out_path = tmp.name
    except OSError as exc:
        return {
            "text": "",
            "model": "codex-cli",
            "provider": "codex-cli",
            "success": False,
            "error": f"Failed to create temp file: {exc}",
        }

    cmd = [
        _get_executable("codex-cli", "codex"),
        "exec",
        "-m", model,
        "-o", out_path,
        "--ephemeral",
        "--",  # End of options — prompt follows as positional arg
        prompt,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )

        # Read output file
        text = ""
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except FileNotFoundError:
            pass

        if proc.returncode != 0 and not text:
            return {
                "text": "",
                "model": "codex-cli",
                "provider": "codex-cli",
                "success": False,
                "error": f"exit {proc.returncode}: {proc.stderr[:500]}",
            }

        final_text = text or proc.stdout.strip()
        return {
            "text": final_text,
            "model": "codex-cli",
            "provider": "codex-cli",
            "success": bool(final_text),
            "error": "" if final_text else "empty response",
        }
    except subprocess.TimeoutExpired:
        return {
            "text": "",
            "model": "codex-cli",
            "provider": "codex-cli",
            "success": False,
            "error": f"timeout after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "text": "",
            "model": "codex-cli",
            "provider": "codex-cli",
            "success": False,
            "error": "codex CLI not found on PATH",
        }
    except OSError as exc:
        return {
            "text": "",
            "model": "codex-cli",
            "provider": "codex-cli",
            "success": False,
            "error": f"OS error (prompt too long?): {exc}",
        }
    finally:
        # Always clean up temp file, regardless of how we exit
        if out_path is not None:
            try:
                os.unlink(out_path)
            except OSError:
                pass


def call_gemini_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Call Gemini CLI in non-interactive mode.

    Returns dict with keys: text, model, provider, success, error.
    """
    prompt = _build_messages_text(messages)

    cmd = [
        _get_executable("gemini-cli", "gemini"),
        "-p", prompt,
        "-o", "text",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )
        if proc.returncode != 0:
            return {
                "text": "",
                "model": "gemini-cli",
                "provider": "gemini-cli",
                "success": False,
                "error": f"exit {proc.returncode}: {proc.stderr[:500]}",
            }

        text = proc.stdout.strip()
        return {
            "text": text,
            "model": "gemini-cli",
            "provider": "gemini-cli",
            "success": bool(text),
            "error": "" if text else "empty response",
        }
    except subprocess.TimeoutExpired:
        return {
            "text": "",
            "model": "gemini-cli",
            "provider": "gemini-cli",
            "success": False,
            "error": f"timeout after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "text": "",
            "model": "gemini-cli",
            "provider": "gemini-cli",
            "success": False,
            "error": "gemini CLI not found on PATH",
        }
    except OSError as exc:
        return {
            "text": "",
            "model": "gemini-cli",
            "provider": "gemini-cli",
            "success": False,
            "error": f"OS error (prompt too long?): {exc}",
        }


def call_kimi_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Call Kimi CLI in non-interactive (quiet) mode.

    --quiet = --print --output-format text --final-message-only

    Returns dict with keys: text, model, provider, success, error.
    """
    prompt = _build_messages_text(messages)

    cmd = [
        _get_executable("kimi-cli", "kimi"),
        "--quiet",
        "-p", prompt,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )
        if proc.returncode != 0:
            return {
                "text": "",
                "model": "kimi-cli",
                "provider": "kimi-cli",
                "success": False,
                "error": f"exit {proc.returncode}: {proc.stderr[:500]}",
            }

        text = proc.stdout.strip()
        return {
            "text": text,
            "model": "kimi-cli",
            "provider": "kimi-cli",
            "success": bool(text),
            "error": "" if text else "empty response",
        }
    except subprocess.TimeoutExpired:
        return {
            "text": "",
            "model": "kimi-cli",
            "provider": "kimi-cli",
            "success": False,
            "error": f"timeout after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "text": "",
            "model": "kimi-cli",
            "provider": "kimi-cli",
            "success": False,
            "error": "kimi CLI not found on PATH",
        }
    except OSError as exc:
        return {
            "text": "",
            "model": "kimi-cli",
            "provider": "kimi-cli",
            "success": False,
            "error": f"OS error (prompt too long?): {exc}",
        }


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------

_CLI_CALLERS: dict[str, Callable] = {
    "claude-cli": call_claude_cli,
    "codex-cli": call_codex_cli,
    "gemini-cli": call_gemini_cli,
    "kimi-cli": call_kimi_cli,
}


def call_cli_provider(
    provider_key: str,
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
    model: str | None = None,
) -> dict:
    """Call a CLI-based LLM provider by key.

    Args:
        provider_key: One of 'claude-cli', 'codex-cli', 'gemini-cli', 'kimi-cli'.
        messages: Chat messages in standard format.
        max_tokens: Max tokens for response (advisory for CLI tools).
        timeout: Subprocess timeout in seconds.
        model: Optional model override for providers that support it
            (claude-cli, codex-cli). If None, uses the provider's default.

    Returns:
        Dict with keys: text, model, provider, success, error.
    """
    caller = _CLI_CALLERS.get(provider_key)
    if caller is None:
        return {
            "text": "",
            "model": provider_key,
            "provider": provider_key,
            "success": False,
            "error": f"unknown CLI provider: {provider_key}",
        }
    # Forward model kwarg to providers that accept it (claude, codex)
    if model is not None and provider_key in ("claude-cli", "codex-cli"):
        return caller(messages, max_tokens, timeout, model=model)
    return caller(messages, max_tokens, timeout)
