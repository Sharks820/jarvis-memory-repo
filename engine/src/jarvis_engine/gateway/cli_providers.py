"""CLI-based LLM providers: invoke Claude Code, Codex, Gemini, and Kimi CLIs.

Uses subprocess to call CLI tools in non-interactive mode.  This lets Jarvis
leverage subscription-based CLI plans (Claude Code 20x Max, Codex Pro, etc.)
without needing separate API keys.

Provider detection is lazy: ``detect_cli_providers()`` is called during
gateway initialization so CLI availability reflects runtime PATH/auth state.

**Windows note:** npm-installed CLIs create ``.CMD`` batch wrappers that
``subprocess.run`` cannot execute by bare name (CreateProcess doesn't search
PATHEXT).  We resolve the full executable path via ``shutil.which`` at
detection time and use that path for all invocations.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token usage parsing from CLI stderr/stdout
# ---------------------------------------------------------------------------

# Patterns for extracting token counts from CLI output.
# Claude CLI JSON has usage.input_tokens / usage.output_tokens in event stream.
# Other CLIs may print "tokens: X input, Y output" or similar to stderr.
_TOKEN_PATTERNS = [
    # "X input tokens, Y output tokens"
    re.compile(r"(\d[\d,]*)\s*input\s*tokens?\b.*?(\d[\d,]*)\s*output\s*tokens?", re.IGNORECASE),
    # "tokens: X input, Y output"
    re.compile(r"tokens?:\s*(\d[\d,]*)\s*input\b.*?(\d[\d,]*)\s*output\b", re.IGNORECASE),
    # "input: X tokens, output: Y tokens"
    re.compile(r"input:\s*(\d[\d,]*)\s*tokens?\b.*?output:\s*(\d[\d,]*)\s*tokens?", re.IGNORECASE),
    # "prompt_tokens: X" / "completion_tokens: Y"
    re.compile(r"prompt_tokens?:\s*(\d[\d,]*).*?completion_tokens?:\s*(\d[\d,]*)", re.IGNORECASE | re.DOTALL),
]


def _parse_token_usage(text: str) -> tuple[int, int]:
    """Try to extract (input_tokens, output_tokens) from CLI output text.

    Scans *text* (typically stderr or JSON stdout) for common token usage
    patterns.  Returns ``(0, 0)`` if no pattern matches.
    """
    if not text:
        return (0, 0)

    # Try JSON parsing first (Claude CLI event-stream format)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            usage = parsed.get("usage", {})
            if isinstance(usage, dict):
                inp = int(usage.get("input_tokens", 0) or 0)
                out = int(usage.get("output_tokens", 0) or 0)
                if inp or out:
                    return (inp, out)
        elif isinstance(parsed, list):
            # Event stream — look for result event with usage
            for event in parsed:
                if not isinstance(event, dict):
                    continue
                usage = event.get("usage", {})
                if isinstance(usage, dict):
                    inp = int(usage.get("input_tokens", 0) or 0)
                    out = int(usage.get("output_tokens", 0) or 0)
                    if inp or out:
                        return (inp, out)
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.debug("Token usage JSON parsing failed, falling back to regex")
        pass

    # Try regex patterns on raw text
    for pattern in _TOKEN_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                inp = int(match.group(1).replace(",", ""))
                out = int(match.group(2).replace(",", ""))
                return (inp, out)
            except (ValueError, IndexError):
                logger.debug("Failed to parse token counts from regex match")
                continue

    return (0, 0)

def _default_cli_timeout() -> int:
    """Return CLI timeout seconds from env with safe bounds."""
    raw = os.environ.get("JARVIS_CLI_TIMEOUT_S", "240").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 240
    return max(60, min(value, 900))


# Timeout for CLI calls (seconds).  LLM completions can take a while.
_DEFAULT_TIMEOUT = _default_cli_timeout()
_MAX_PROMPT_CHARS_DEFAULT = 24_000
_MAX_MESSAGE_CHARS = 2_000
_CHECKPOINT_LINES = 10


def _max_prompt_chars() -> int:
    raw = os.environ.get("JARVIS_CLI_PROMPT_MAX_CHARS", str(_MAX_PROMPT_CHARS_DEFAULT)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _MAX_PROMPT_CHARS_DEFAULT
    return max(6_000, min(value, 120_000))


def _claude_cli_max_budget_usd() -> str | None:
    """Return optional Claude CLI max budget from env, or None to omit the flag."""
    raw = os.environ.get("JARVIS_CLAUDE_CLI_MAX_BUDGET_USD", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.debug("Invalid JARVIS_CLAUDE_CLI_MAX_BUDGET_USD value: %r", raw)
        return None
    if value <= 0:
        return None
    return f"{value:.2f}"


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
# Common result / subprocess helpers
# ---------------------------------------------------------------------------

class CLIProviderResult(TypedDict):
    """Standardised result from a CLI-based LLM provider call."""

    text: str
    model: str
    provider: str
    success: bool
    error: str
    cost_usd: float
    input_tokens: int
    output_tokens: int


def _cli_result(
    provider: str,
    model: str,
    *,
    text: str = "",
    success: bool = False,
    error: str = "",
    cost_usd: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> CLIProviderResult:
    """Construct a standardised CLI provider result dict."""
    return {
        "text": text,
        "model": model,
        "provider": provider,
        "success": success,
        "error": error,
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _run_cli_subprocess(
    cmd: list[str],
    provider: str,
    model: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    cli_display_name: str = "",
    env: dict[str, str] | None = None,
    parse_output: Callable[[str], tuple[str, float]] | None = None,
) -> CLIProviderResult:
    """Run a CLI subprocess and return a standardised result dict.

    Handles the common subprocess.run + error-catching pattern shared by all
    CLI providers.  Provider-specific stdout parsing is delegated to the
    optional *parse_output* callback.

    Args:
        cmd: The subprocess command list.
        provider: Provider key (e.g. ``"claude-cli"``).
        model: Model key for the result dict.
        timeout: Subprocess timeout in seconds.
        cli_display_name: Human-readable CLI name for error messages
            (e.g. ``"claude"``).  Falls back to *provider* if empty.
        env: Optional environment dict for ``subprocess.run``.
        parse_output: Optional ``(stdout) -> (text, cost_usd)`` callback.
            When *None*, the default behaviour is ``(stdout.strip(), 0.0)``.

    Returns:
        Standardised result dict with keys: text, model, provider, success,
        error, cost_usd.
    """
    display = cli_display_name or provider

    try:
        from jarvis_engine._shared import win_hidden_subprocess_kwargs

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=tempfile.gettempdir(),
            **win_hidden_subprocess_kwargs(),
        )
        if proc.returncode != 0:
            return _cli_result(
                provider, model,
                error=f"exit {proc.returncode}: {proc.stderr[:500]}",
            )

        if parse_output is not None:
            text, cost = parse_output(proc.stdout)
        else:
            text = proc.stdout.strip()
            cost = 0.0

        # Try to extract token usage from stdout or stderr
        input_tokens, output_tokens = _parse_token_usage(proc.stdout)
        if input_tokens == 0 and output_tokens == 0 and proc.stderr:
            input_tokens, output_tokens = _parse_token_usage(proc.stderr)

        success = bool(text.strip())
        return _cli_result(
            provider, model,
            text=text,
            success=success,
            error="" if success else "empty response",
            cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except subprocess.TimeoutExpired:
        return _cli_result(
            provider, model,
            error=f"timeout after {timeout}s",
        )
    except FileNotFoundError:
        return _cli_result(
            provider, model,
            error=f"{display} CLI not found on PATH",
        )
    except OSError as exc:
        return _cli_result(
            provider, model,
            error=f"OS error (prompt too long?): {exc}",
        )


# ---------------------------------------------------------------------------
# Invocation helpers
# ---------------------------------------------------------------------------

def _build_messages_text(messages: list[dict[str, str]]) -> str:
    """Convert chat messages to a single text prompt for CLI tools.

    System messages become a preamble, then user/assistant turns follow.
    Multi-turn history is formatted with User:/Assistant: prefixes so CLIs
    can distinguish conversation turns even though they receive flat text.
    """
    messages = _compact_messages_for_cli(messages)
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
        prompt += (
            "IMPORTANT: This is an ongoing multi-turn conversation. "
            "You are continuing a conversation that is already in progress. "
            "The previous exchanges are shown below. Respond ONLY to the most "
            "recent User message, using the conversation context naturally. "
            "Do not restart or re-introduce yourself.\n\n"
        )
    prompt += "\n\n".join(parts)
    return prompt


def _squash_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _build_checkpoint(dropped_messages: list[dict[str, str]]) -> str:
    if not dropped_messages:
        return ""
    lines = [
        "Conversation checkpoint from earlier turns (compressed):",
        "Keep continuity with this context and do not restart from scratch.",
    ]
    for item in dropped_messages[-_CHECKPOINT_LINES:]:
        role = str(item.get("role", "user")).strip().lower()
        role_label = "User" if role == "user" else "Assistant"
        content = _squash_whitespace(str(item.get("content", "")))
        if not content:
            continue
        lines.append(f"- {role_label}: {content[:220]}")
    return "\n".join(lines)


def _compact_messages_for_cli(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep continuity while bounding prompt size for CLI transport stability."""
    if not messages:
        return messages

    max_chars = _max_prompt_chars()
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        if len(content) > _MAX_MESSAGE_CHARS:
            content = content[:_MAX_MESSAGE_CHARS]
        normalized.append({"role": role, "content": content})

    estimated = sum(len(m.get("content", "")) + 16 for m in normalized)
    if estimated <= max_chars:
        return normalized

    system_messages = [m for m in normalized if m.get("role") == "system"]
    convo_messages = [m for m in normalized if m.get("role") != "system"]

    sys_budget = max(2_000, int(max_chars * 0.35))
    kept_system: list[dict[str, str]] = []
    used = 0
    for msg in system_messages:
        content = msg.get("content", "")
        remaining = sys_budget - used
        if remaining <= 0:
            break
        clipped = content[:remaining]
        kept_system.append({"role": "system", "content": clipped})
        used += len(clipped)

    convo_budget = max(2_500, max_chars - used - 1_500)
    kept_convo_rev: list[dict[str, str]] = []
    dropped_rev: list[dict[str, str]] = []
    convo_used = 0
    for msg in reversed(convo_messages):
        content = msg.get("content", "")
        clipped = content[:_MAX_MESSAGE_CHARS]
        cost = len(clipped) + 16
        # Always keep at least the last 3 conversation turns.
        if convo_used + cost <= convo_budget or len(kept_convo_rev) < 3:
            kept_convo_rev.append({"role": msg.get("role", "user"), "content": clipped})
            convo_used += cost
        else:
            dropped_rev.append(msg)

    kept_convo = list(reversed(kept_convo_rev))
    dropped = list(reversed(dropped_rev))
    checkpoint = _build_checkpoint(dropped)
    if checkpoint:
        kept_system.append({"role": "system", "content": checkpoint})

    # Notify conversation state manager about compaction so it can
    # preserve entities/goals from dropped messages.  Deduplicate by
    # content hash to avoid double-checkpointing when both
    # _build_messages_text and _build_claude_cli_prompt call us.
    if dropped:
        content_key = hash(tuple(m.get("content", "")[:100] for m in dropped))
        if content_key != getattr(_compact_messages_for_cli, "_last_ckpt", None):
            _compact_messages_for_cli._last_ckpt = content_key  # type: ignore[attr-defined]
            try:
                from jarvis_engine.conversation_state import get_conversation_state

                csm = get_conversation_state()
                csm.create_checkpoint(dropped)
            except (ImportError, OSError, ValueError) as exc:
                logger.debug("Conversation state checkpoint failed: %s", exc)

    compacted = [*kept_system, *kept_convo]
    return compacted if compacted else normalized[-3:]


def _build_claude_cli_prompt(messages: list[dict[str, str]]) -> str:
    """Build a prompt for Claude Code CLI, stripping persona instructions.

    Claude Code CLI has its own system prompt and confuses persona roleplay
    instructions (e.g. "You are Jarvis...") with the actual task. It ends up
    introducing itself as Jarvis instead of answering the user's question.

    This function keeps factual context (KG facts, memories, preferences)
    but strips the character/persona preamble.
    """
    messages = _compact_messages_for_cli(messages)
    context_parts: list[str] = []
    conversation_parts: list[str] = []
    user_count = sum(1 for m in messages if m.get("role", "user") == "user")
    multi_turn = user_count > 1

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            # Keep factual context sections, skip persona roleplay lines.
            # System messages are newline-separated sections. Lines starting
            # with "You are Jarvis" or containing persona instructions are
            # the roleplay preamble; the rest is factual context.
            for section in content.split("\n\n"):
                section_stripped = section.strip()
                if not section_stripped:
                    continue
                lower = section_stripped.lower()
                # Skip persona/roleplay instructions
                if lower.startswith("you are jarvis"):
                    continue
                if "speak like" in lower and "butler" in lower:
                    continue
                if "keep responses concise and natural" in lower:
                    continue
                # Keep everything else (facts, memories, preferences, etc.)
                context_parts.append(section_stripped)
        elif role == "assistant":
            conversation_parts.append(f"Assistant: {content}")
        else:
            if multi_turn:
                conversation_parts.append(f"User: {content}")
            else:
                conversation_parts.append(content)

    prompt = ""
    if context_parts:
        prompt = "Context:\n" + "\n\n".join(context_parts) + "\n\n"
    if multi_turn and conversation_parts:
        prompt += (
            "IMPORTANT: This is an ongoing multi-turn conversation. "
            "Respond ONLY to the most recent User message.\n\n"
        )
    prompt += "\n\n".join(conversation_parts)
    return prompt


def _extract_claude_text_and_cost(stdout: str) -> tuple[str, float]:
    """Parse Claude CLI output across legacy and event-stream JSON formats."""
    raw = (stdout or "").strip()
    if not raw:
        return ("", 0.0)

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return (raw, 0.0)

    if isinstance(parsed, dict):
        text = str(parsed.get("result") or parsed.get("text") or "").strip()
        if not text:
            message = parsed.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, list):
                    blocks: list[str] = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            val = str(item.get("text", "")).strip()
                            if val:
                                blocks.append(val)
                    text = "\n\n".join(blocks).strip()
        try:
            cost = float(parsed.get("total_cost_usd", parsed.get("cost_usd", 0.0)) or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        return (text, cost)

    if isinstance(parsed, list):
        assistant_text_chunks: list[str] = []
        result_text = ""
        total_cost = 0.0
        for event in parsed:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type", "")).strip().lower()
            if event_type == "assistant":
                message = event.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                val = str(block.get("text", "")).strip()
                                if val:
                                    assistant_text_chunks.append(val)
            elif event_type == "result":
                candidate = str(event.get("result", "")).strip()
                if candidate:
                    result_text = candidate
                try:
                    total_cost = float(event.get("total_cost_usd", event.get("cost_usd", total_cost)) or total_cost)
                except (TypeError, ValueError):
                    logger.debug("Could not parse cost from CLI event: %s", event.get("total_cost_usd"))

        text = result_text or "\n\n".join(assistant_text_chunks).strip()
        return (text, total_cost)

    return ("", 0.0)


def call_claude_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
    model: str = "opus",
) -> CLIProviderResult:
    """Call Claude Code CLI in non-interactive mode.

    Uses Opus 4.6 by default (the user's 20x Max plan).
    Pass model="sonnet" for faster, cheaper responses.

    Returns dict with keys: text, model, provider, success, error.
    """
    prompt = _build_claude_cli_prompt(messages)

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
    ]
    budget = _claude_cli_max_budget_usd()
    if budget is not None:
        cmd.extend(["--max-budget-usd", budget])

    return _run_cli_subprocess(
        cmd, "claude-cli", "claude-cli",
        timeout=timeout,
        cli_display_name="claude",
        env=env,
        parse_output=_extract_claude_text_and_cost,
    )


def call_codex_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
    model: str = "gpt-5.3-codex",
) -> CLIProviderResult:
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
        return _cli_result(
            "codex-cli", "codex-cli",
            error=f"Failed to create temp file: {exc}",
        )

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
        from jarvis_engine._shared import win_hidden_subprocess_kwargs

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
            **win_hidden_subprocess_kwargs(),
        )

        # Read output file
        text = ""
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except FileNotFoundError:
            logger.debug("CLI output file not found: %s", out_path)

        if proc.returncode != 0 and not text:
            return _cli_result(
                "codex-cli", "codex-cli",
                error=f"exit {proc.returncode}: {proc.stderr[:500]}",
            )

        final_text = text or proc.stdout.strip()

        # Try to extract token usage from stdout or stderr
        input_tokens, output_tokens = _parse_token_usage(proc.stdout)
        if input_tokens == 0 and output_tokens == 0 and proc.stderr:
            input_tokens, output_tokens = _parse_token_usage(proc.stderr)

        return _cli_result(
            "codex-cli", "codex-cli",
            text=final_text,
            success=bool(final_text),
            error="" if final_text else "empty response",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except subprocess.TimeoutExpired:
        return _cli_result(
            "codex-cli", "codex-cli",
            error=f"timeout after {timeout}s",
        )
    except FileNotFoundError:
        return _cli_result(
            "codex-cli", "codex-cli",
            error="codex CLI not found on PATH",
        )
    except OSError as exc:
        return _cli_result(
            "codex-cli", "codex-cli",
            error=f"OS error (prompt too long?): {exc}",
        )
    finally:
        # Always clean up temp file, regardless of how we exit
        if out_path is not None:
            try:
                os.unlink(out_path)
            except OSError as exc:
                logger.debug("Failed to clean up CLI temp file %s: %s", out_path, exc)


def call_gemini_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
) -> CLIProviderResult:
    """Call Gemini CLI in non-interactive mode.

    Returns dict with keys: text, model, provider, success, error.
    """
    prompt = _build_messages_text(messages)

    cmd = [
        _get_executable("gemini-cli", "gemini"),
        "-p", prompt,
        "-o", "text",
    ]

    return _run_cli_subprocess(
        cmd, "gemini-cli", "gemini-cli",
        timeout=timeout,
        cli_display_name="gemini",
    )


def call_kimi_cli(
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    timeout: int = _DEFAULT_TIMEOUT,
) -> CLIProviderResult:
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

    return _run_cli_subprocess(
        cmd, "kimi-cli", "kimi-cli",
        timeout=timeout,
        cli_display_name="kimi",
    )


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
) -> CLIProviderResult:
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
        return _cli_result(
            provider_key, provider_key,
            error=f"unknown CLI provider: {provider_key}",
        )
    # Forward model kwarg to providers that accept it (claude, codex)
    if model is not None and provider_key in ("claude-cli", "codex-cli"):
        return caller(messages, max_tokens, timeout, model=model)
    return caller(messages, max_tokens, timeout)
