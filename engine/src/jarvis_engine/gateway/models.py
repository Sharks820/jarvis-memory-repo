"""ModelGateway: unified LLM completion interface.

Wraps Anthropic SDK, OpenAI-compatible cloud APIs (Groq, Mistral, Z.ai),
CLI-based LLMs (Claude Code, Codex, Gemini CLI, Kimi CLI), and Ollama
Python client with:
- Automatic provider resolution based on model name
- CLI providers use authenticated CLI subscriptions (no API keys needed)
- Fallback chain: cloud failure -> CLI -> local Ollama -> graceful error
- Per-query cost tracking via CostTracker
- Local-only mode when no API key is configured
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import httpx

from jarvis_engine._constants import DEFAULT_CLOUD_MODEL
from jarvis_engine._shared import (
    get_fast_local_model,
    get_local_model,
)

try:
    from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False
    Anthropic = None  # type: ignore[assignment,misc]

    class APIConnectionError(Exception):  # type: ignore[no-redef]
        pass

    class APIStatusError(Exception):  # type: ignore[no-redef]
        pass

    class RateLimitError(Exception):  # type: ignore[no-redef]
        pass


_log_activity: Callable[[str, str, dict[Any, Any] | None], str] | None
try:
    from jarvis_engine.memory.activity_feed import log_activity as _activity_log
except ImportError:
    _log_activity = None
else:
    _log_activity = _activity_log

from jarvis_engine.gateway.audit import GatewayAudit
from jarvis_engine.gateway.cli_providers import (
    call_cli_provider,
    detect_cli_providers,
    CLIProviderInfo,
)
from jarvis_engine.gateway.pricing import calculate_cost

if TYPE_CHECKING:
    from jarvis_engine.gateway.budget import BudgetEnforcer
    from jarvis_engine.gateway.circuit_breaker import ProviderHealthTracker
    from jarvis_engine.gateway.costs import CostTracker
    from jarvis_engine.learning.feedback import ResponseFeedbackTracker

logger = logging.getLogger(__name__)

# Timeout constants (seconds)
_OLLAMA_IMPORT_TIMEOUT_S = 3.0
_OLLAMA_CLIENT_TIMEOUT_S = 90.0
_OLLAMA_RETRY_COUNT = 2  # retry transient connection errors (e.g. RemoteDisconnected)
_OLLAMA_RETRY_BASE_S = 1.0  # exponential backoff base: 1s, 2s
_ANTHROPIC_CLIENT_TIMEOUT_S = 60.0
_HTTP_CLIENT_TIMEOUT_S = 60.0  # shared httpx pool for cloud calls

# Rate-limit retry
_RATE_LIMIT_MAX_RETRY_AFTER_S = 5.0

# Token estimation: approximate 4 chars per token
_CHARS_PER_TOKEN_ESTIMATE = 4

# Default temperature for LLM completions
_DEFAULT_TEMPERATURE = 0.7

# Default max tokens for LLM completions
_DEFAULT_MAX_TOKENS = 1024

# CLI provider refresh bounds (seconds)
_CLI_REFRESH_MIN_S = 1.0
_CLI_REFRESH_MAX_S = 300.0
_CLI_REFRESH_DEFAULT_S = 30.0

# Defer ollama import to avoid blocking when Ollama server isn't running.
# Some ollama versions attempt a connection check during import/init.
# Mutable container for ollama lazy-import state (avoids ``global`` keyword).
_ollama_state: dict[str, Any] = {
    "has_ollama": False,
    "client_cls": None,
    "response_error_cls": None,
}

# Module-level names kept for backward compatibility with test patches
# (e.g. ``@patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)``).
_HAS_OLLAMA = False
OllamaClient = None  # type: ignore[assignment,misc]


class ResponseError(Exception):  # type: ignore[no-redef]
    """Placeholder until real ollama.ResponseError is loaded."""

    pass


import sys as _sys


def _ensure_ollama() -> bool:
    """Lazy-import ollama client with timeout guard.

    The ollama package may try to connect to the Ollama server during import
    or Client() construction.  If the server is down, this blocks indefinitely.
    We use a daemon thread with a 3-second timeout to prevent the hang.

    Set JARVIS_SKIP_OLLAMA=1 to bypass entirely (used in test environments).
    """
    _mod = _sys.modules[__name__]
    if getattr(_mod, "_HAS_OLLAMA", False):
        return True
    if os.environ.get("JARVIS_SKIP_OLLAMA"):
        return False

    import threading as _th

    result: dict = {}

    def _try_import():
        try:
            from ollama import Client as _OC, ResponseError as _RE

            result["client"] = _OC
            result["error"] = _RE
        except (ImportError, OSError) as exc:
            result["exc"] = exc

    t = _th.Thread(target=_try_import, daemon=True, name="ollama-import")
    t.start()
    t.join(timeout=_OLLAMA_IMPORT_TIMEOUT_S)

    if t.is_alive():
        logger.debug("Ollama import timed out (server likely down) — skipping")
        return False

    if "client" in result:
        _ollama_state["client_cls"] = result["client"]
        _ollama_state["response_error_cls"] = result["error"]
        _ollama_state["has_ollama"] = True
        # Update module-level attributes for backward compatibility with test patches
        _mod.OllamaClient = result["client"]  # type: ignore[attr-defined]
        _mod.ResponseError = result["error"]  # type: ignore[attr-defined]
        _mod._HAS_OLLAMA = True  # type: ignore[attr-defined]
        return True

    logger.debug("Ollama import failed: %s", result.get("exc", "unknown"))
    return False


# OpenAI-compatible cloud provider configurations
# Each maps: env_var_for_key -> (base_url, provider_name)
OPENAI_COMPAT_PROVIDERS: dict[str, dict] = {
    "groq": {
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "provider_name": "groq",
    },
    "mistral": {
        "env_key": "MISTRAL_API_KEY",
        "base_url": "https://api.mistral.ai/v1",
        "provider_name": "mistral",
    },
    "zai": {
        "env_key": "ZAI_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "provider_name": "zai",
    },
}

# Model name -> (provider_key, full_model_id)
CLOUD_MODEL_MAP: dict[str, tuple[str, str]] = {
    # Groq models
    "kimi-k2": ("groq", "moonshotai/kimi-k2-instruct"),
    "llama-3.3-70b": ("groq", "llama-3.3-70b-versatile"),
    # Mistral models
    "devstral-2": ("mistral", "devstral-2512"),
    "devstral-small-2": ("mistral", "devstral-small-2512"),
    # Z.ai models
    "glm-4.7": ("zai", "glm-4.7"),
    "glm-4.7-flash": ("zai", "glm-4.7-flash"),
}

# Short alias -> full Anthropic API model identifier
ANTHROPIC_MODEL_ALIASES: dict[str, str] = {
    "claude-opus": "claude-opus-4-0-20250514",
    "claude-sonnet": "claude-sonnet-4-5-20250929",
    "claude-haiku": "claude-haiku-4-5-20251001",
}

# CLI-based model names (these route to subprocess invocations, no API key needed)
CLI_MODEL_MAP: dict[str, str] = {
    "claude-cli": "claude-cli",
    "codex-cli": "codex-cli",
    "gemini-cli": "gemini-cli",
    "kimi-cli": "kimi-cli",
}

# Reverse index: provider_key -> first (preferred) model alias for fallback use.
# Built once at import time to avoid O(n) scan per provider in _fallback_chain().
_PROVIDER_DEFAULT_MODEL: dict[str, str] = {}
for _alias, (_pk, _) in CLOUD_MODEL_MAP.items():
    _PROVIDER_DEFAULT_MODEL.setdefault(_pk, _alias)


def _cli_refresh_interval_seconds() -> float:
    """Return CLI provider refresh cadence in seconds."""
    raw = os.environ.get("JARVIS_CLI_PROVIDER_REFRESH_S", str(int(_CLI_REFRESH_DEFAULT_S))).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _CLI_REFRESH_DEFAULT_S
    return max(_CLI_REFRESH_MIN_S, min(value, _CLI_REFRESH_MAX_S))


@dataclass
class GatewayResponse:
    """Response from a ModelGateway completion call."""

    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    fallback_used: bool = False
    fallback_reason: str = ""
    # CTX-02/CTX-06: Context continuity metadata for fallback chains.
    # Populated when a fallback occurs so callers can verify that the
    # original task context was preserved across the provider switch.
    _continuity_context: dict[str, object] = field(default_factory=dict)


# Model context window limits (tokens).
# Used by _apply_context_guard to prevent oversized prompts.
_MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # Local models (Ollama)
    "qwen3.5:latest": 32_768,
    "qwen3.5:4b": 32_768,
    "qwen3:14b": 32_768,
    "qwen3:4b": 32_768,
    "gemma3:4b": 8_192,
    "gemma3:12b": 8_192,
    "llama3.2:3b": 8_192,
    "phi-4": 16_384,
    # Cloud models (Groq / Mistral / Z.ai)
    "kimi-k2": 131_072,
    "llama-3.3-70b": 128_000,
    "devstral-2": 128_000,
    "devstral-small-2": 128_000,
    "glm-4.7": 128_000,
    "glm-4.7-flash": 128_000,
    # Anthropic
    "claude-opus": 200_000,
    "claude-sonnet": 200_000,
    "claude-haiku": 200_000,
    # CLI-based (generous limits since they manage their own context)
    "claude-cli": 200_000,
    "codex-cli": 128_000,
    "gemini-cli": 1_000_000,
    "kimi-cli": 131_072,
}

# Threshold fraction: warn/truncate when prompt exceeds this fraction of context.
_CONTEXT_GUARD_THRESHOLD = 0.9


# Route -> temperature mapping for _derive_temperature.
# Covers all known intent routes; unknown routes default to 0.7.
_ROUTE_TEMPERATURE: dict[str, float] = {
    "math_logic": 0.2,
    "complex": 0.3,
    "routine": 0.5,
    "creative": 0.85,
    "web_research": 0.5,
    "simple_private": 0.7,
}


class ModelGateway:
    """Unified LLM completion interface with fallback chains.

    Dispatches to Anthropic, OpenAI-compatible cloud APIs (Groq, Mistral, Z.ai),
    or Ollama based on model name. Handles API failures gracefully with automatic
    fallback to local models. Logs per-query costs when a CostTracker is provided.
    """

    def __init__(
        self,
        anthropic_api_key: str | None = None,
        ollama_host: str = "http://127.0.0.1:11434",
        cost_tracker: "CostTracker | None" = None,
        groq_api_key: str | None = None,
        mistral_api_key: str | None = None,
        zai_api_key: str | None = None,
        audit_path: Path | None = None,
        budget_enforcer: "BudgetEnforcer | None" = None,
        health_tracker: "ProviderHealthTracker | None" = None,
        feedback_tracker: "ResponseFeedbackTracker | None" = None,
    ) -> None:
        self._closed = False
        self._budget = budget_enforcer
        self._health = health_tracker
        self._feedback_tracker = feedback_tracker
        self._audit: GatewayAudit | None = (
            GatewayAudit(audit_path) if audit_path is not None else None
        )
        if anthropic_api_key is not None:
            if _HAS_ANTHROPIC:
                self._anthropic: Anthropic | None = Anthropic(
                    api_key=anthropic_api_key, timeout=_ANTHROPIC_CLIENT_TIMEOUT_S
                )
            else:
                self._anthropic = None
                logger.warning(
                    "Anthropic API key provided but anthropic package is not installed"
                )
        else:
            self._anthropic = None

        if _ensure_ollama():
            self._ollama = OllamaClient(host=ollama_host, timeout=_OLLAMA_CLIENT_TIMEOUT_S)  # type: ignore[misc]
        else:
            self._ollama = None
        self._cost_tracker = cost_tracker

        # Build cloud provider registry from explicit keys or env vars
        self._cloud_keys: dict[str, str] = {}
        for provider_key, cfg in OPENAI_COMPAT_PROVIDERS.items():
            key = {
                "groq": groq_api_key,
                "mistral": mistral_api_key,
                "zai": zai_api_key,
            }.get(provider_key) or os.environ.get(cfg["env_key"], "")
            if key:
                self._cloud_keys[provider_key] = key

        # httpx client for cloud calls (shared, connection pooling)
        self._http = httpx.Client(timeout=_HTTP_CLIENT_TIMEOUT_S)

        # Detect CLI-based LLM providers (Claude Code, Codex, Gemini, Kimi)
        self._cli_refresh_interval_s = _cli_refresh_interval_seconds()
        self._last_cli_refresh_monotonic = 0.0
        self._cli_providers: dict[str, CLIProviderInfo] = {}
        self._refresh_cli_providers(force=True)

        # Log available providers
        available = []
        if self._anthropic is not None:
            available.append("anthropic")
        available.extend(self._cloud_keys.keys())
        for key in self._cli_providers:
            available.append(f"{key}")
        if self._ollama is not None:
            available.append("ollama")
        if not available:
            logger.warning("No LLM providers configured -- all calls will fail")
        else:
            logger.info("LLM providers available: %s", ", ".join(available))

    def close(self) -> None:
        """Release httpx connection pool and other resources.

        Safe to call multiple times -- uses ``_closed`` flag to avoid
        double-close errors on httpx and Anthropic clients.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            if hasattr(self, "_http"):
                self._http.close()
        except OSError as exc:
            logger.debug("Failed to close httpx client: %s", exc)
        if hasattr(self, "_anthropic") and self._anthropic is not None:
            try:
                self._anthropic.close()
            except OSError as exc:
                logger.debug("Failed to close Anthropic client: %s", exc)

    def __del__(self) -> None:
        try:
            self.close()
        except (OSError, RuntimeError, TypeError) as exc:
            logger.debug("__del__ cleanup failed: %s", exc)

    def __enter__(self) -> "ModelGateway":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _refresh_cli_providers(self, *, force: bool = False) -> None:
        """Refresh installed CLI provider availability without restarting."""
        now = time.monotonic()
        if (
            not force
            and (now - self._last_cli_refresh_monotonic) < self._cli_refresh_interval_s
        ):
            return
        self._last_cli_refresh_monotonic = now
        try:
            detected = detect_cli_providers()
        except (OSError, ValueError) as exc:
            logger.debug("CLI provider refresh failed: %s", exc)
            return

        refreshed: dict[str, CLIProviderInfo] = {}
        for key, info in detected.items():
            if info.available:
                refreshed[key] = info

        if refreshed == self._cli_providers:
            return

        previous_keys = set(self._cli_providers.keys())
        refreshed_keys = set(refreshed.keys())
        added = sorted(refreshed_keys - previous_keys)
        removed = sorted(previous_keys - refreshed_keys)
        if added or removed:
            logger.info(
                "CLI provider availability updated: added=%s removed=%s",
                added,
                removed,
            )
        self._cli_providers = refreshed

    def _resolve_provider(self, model: str) -> str:
        """Determine which provider to use for a given model."""
        self._refresh_cli_providers()
        # CLI models take precedence (exact match) — before startswith checks
        if model in CLI_MODEL_MAP:
            cli_key = CLI_MODEL_MAP[model]
            if cli_key in self._cli_providers:
                return f"cli:{cli_key}"
            # CLI model requested but not installed — fall to Ollama, not Anthropic
            return "ollama"

        if model.startswith("claude-") and self._anthropic is not None:
            return "anthropic"

        # Check if model maps to an OpenAI-compatible cloud provider
        if model in CLOUD_MODEL_MAP:
            provider_key, _ = CLOUD_MODEL_MAP[model]
            if provider_key in self._cloud_keys:
                return f"cloud:{provider_key}"

        return "ollama"

    def _best_cloud_model(self) -> str | None:
        """Return the best available cloud model based on configured API keys.

        Priority: Groq Kimi K2 (fastest) > Mistral Devstral 2 > Z.ai GLM-4.7
        """
        if "groq" in self._cloud_keys:
            return DEFAULT_CLOUD_MODEL
        if "mistral" in self._cloud_keys:
            return "devstral-2"
        if "zai" in self._cloud_keys:
            return "glm-4.7-flash"
        return None

    @staticmethod
    def _derive_temperature(
        model: str,
        route_reason: str,
        temperature: float | None,
    ) -> float:
        """Derive sampling temperature from route/model when not explicitly set.

        Uses the ``_ROUTE_TEMPERATURE`` dict to map intent routes to
        appropriate temperatures. Model-specific overrides (codex -> 0.2,
        gemini -> 0.85) are applied on top when no route matches.
        """
        if temperature is not None:
            return temperature

        # Check route-based temperature first (covers all known intent routes)
        reason_lower = route_reason.lower()
        for route, temp in _ROUTE_TEMPERATURE.items():
            if route and route == reason_lower:
                return temp

        # Model-specific overrides for cases where route_reason is empty/generic
        model_lower = model.lower()
        if "codex" in model_lower:
            return 0.2
        if "gemini" in model_lower:
            return 0.85

        return _ROUTE_TEMPERATURE.get("", _DEFAULT_TEMPERATURE)

    def _remap_model_if_needed(self, model: str) -> str:
        """Remap Claude API model to best cloud model if Anthropic is unavailable."""
        if (
            model.startswith("claude-")
            and model not in CLI_MODEL_MAP
            and self._anthropic is None
        ):
            best = self._best_cloud_model()
            if best:
                logger.info("Anthropic unavailable, routing %s -> %s", model, best)
                return best
        return model

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, str]]) -> int:
        """Rough token estimate: total chars / _CHARS_PER_TOKEN_ESTIMATE."""
        return sum(len(m.get("content", "")) for m in messages) // _CHARS_PER_TOKEN_ESTIMATE

    def _apply_context_guard(
        self,
        messages: list[dict[str, str]],
        model: str,
    ) -> tuple[list[dict[str, str]], str]:
        """Guard against exceeding a model's context window.

        If the estimated token count exceeds 90% of the model's context limit,
        try to find a model with a larger context window.  If none is available
        or the model is unknown, truncate the system prompt to fit.

        Returns ``(possibly_modified_messages, possibly_remapped_model)``.
        """
        limit = _MODEL_CONTEXT_LIMITS.get(model)
        if limit is None:
            return messages, model

        estimated = self._estimate_tokens(messages)
        threshold = int(limit * _CONTEXT_GUARD_THRESHOLD)

        if estimated <= threshold:
            return messages, model

        logger.warning(
            "Prompt ~%d tokens exceeds %d%% of %s context (%d). "
            "Attempting context guard.",
            estimated,
            int(_CONTEXT_GUARD_THRESHOLD * 100),
            model,
            limit,
        )

        # Try to switch to a model with a larger context window.
        # Prefer candidates from the same provider family as the original model.
        original_provider = self._resolve_provider(model)
        original_family = original_provider.split(":")[0] if original_provider else ""

        candidates = [
            (c, cl)
            for c, cl in sorted(
                _MODEL_CONTEXT_LIMITS.items(), key=lambda x: x[1], reverse=True
            )
            if c != model and cl > limit
        ]

        def _try_candidate(candidate: str, candidate_limit: int) -> str | None:
            candidate_threshold = int(candidate_limit * _CONTEXT_GUARD_THRESHOLD)
            if estimated > candidate_threshold:
                return None
            provider = self._resolve_provider(candidate)
            if provider != "ollama" or candidate in (
                get_local_model(),
                get_fast_local_model(),
            ):
                logger.info(
                    "Context guard: switching %s -> %s (limit %d -> %d)",
                    model,
                    candidate,
                    limit,
                    candidate_limit,
                )
                return candidate
            return None

        # First pass: same provider family only
        for candidate, candidate_limit in candidates:
            cand_provider = self._resolve_provider(candidate)
            cand_family = cand_provider.split(":")[0] if cand_provider else ""
            if cand_family != original_family:
                continue
            result = _try_candidate(candidate, candidate_limit)
            if result is not None:
                return messages, result

        # Second pass: any provider family
        for candidate, candidate_limit in candidates:
            result = _try_candidate(candidate, candidate_limit)
            if result is not None:
                return messages, result

        # No larger model available — truncate user/assistant messages
        # (oldest first) to fit.  System prompts define model behavior and
        # must NEVER be truncated.
        system_tokens = sum(
            len(m.get("content", "")) for m in messages if m.get("role") == "system"
        ) // _CHARS_PER_TOKEN_ESTIMATE
        if system_tokens >= threshold:
            logger.warning(
                "Context guard: system prompt alone (~%d tokens) exceeds "
                "%d%% of %s context (%d). Cannot truncate system prompt.",
                system_tokens,
                int(_CONTEXT_GUARD_THRESHOLD * 100),
                model,
                limit,
            )
            return messages, model

        excess_tokens = estimated - threshold
        excess_chars = excess_tokens * _CHARS_PER_TOKEN_ESTIMATE  # reverse the estimate

        # Separate system messages (preserved) from conversation messages
        system_msgs = [m for m in messages if m.get("role") == "system"]
        conversation_msgs = [m for m in messages if m.get("role") != "system"]

        # Trim oldest conversation messages first
        chars_trimmed = 0
        trimmed_conversation: list[dict[str, str]] = []
        for msg in conversation_msgs:
            if chars_trimmed < excess_chars:
                content = msg.get("content", "")
                trim_amount = min(len(content), excess_chars - chars_trimmed)
                new_content = content[trim_amount:]  # trim from the start (oldest text)
                chars_trimmed += trim_amount
                if new_content:
                    trimmed_conversation.append({"role": msg["role"], "content": new_content})
                # else: drop fully trimmed message
            else:
                trimmed_conversation.append(msg)

        logger.info(
            "Context guard: trimmed conversation messages by ~%d chars for %s",
            chars_trimmed,
            model,
        )
        return system_msgs + trimmed_conversation, model

    def _check_feedback_quality(self, route_reason: str) -> None:
        """Log a warning if feedback satisfaction is low for the current route.

        This is a soft signal — it does NOT change routing, just emits a warning
        so operators can investigate quality issues.
        """
        if self._feedback_tracker is None or not route_reason:
            return
        try:
            quality = self._feedback_tracker.get_route_quality(route_reason)
            if quality["total"] >= 5 and quality["satisfaction_rate"] < 0.4:
                logger.warning(
                    "Low satisfaction (%.0f%%) for route '%s' over last %d feedback entries. "
                    "Consider investigating model quality.",
                    quality["satisfaction_rate"] * 100,
                    route_reason,
                    quality["total"],
                )
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("Feedback quality check failed: %s", exc)

    def _route_to_provider(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        route_reason: str,
        privacy_routed: bool,
    ) -> tuple[GatewayResponse, str, float]:
        """Route request to the resolved provider with fallback on failure.

        Returns (response, audit_reason, t0) where t0 is the perf_counter
        value to use for latency calculation.
        """
        provider = self._resolve_provider(model)
        t0 = time.perf_counter()

        if provider == "anthropic":
            return self._route_anthropic(
                messages,
                model,
                max_tokens,
                temperature,
                route_reason,
                privacy_routed,
                t0,
            )
        if provider.startswith("cloud:"):
            provider_key = provider.split(":", 1)[1]
            return self._route_cloud(
                messages,
                model,
                max_tokens,
                temperature,
                route_reason,
                privacy_routed,
                t0,
                provider_key,
            )
        if provider.startswith("cli:"):
            cli_key = provider.split(":", 1)[1]
            return self._route_cli(
                messages,
                model,
                max_tokens,
                temperature,
                route_reason,
                privacy_routed,
                t0,
                cli_key,
            )
        return self._route_ollama(
            messages,
            model,
            max_tokens,
            temperature,
            route_reason,
            privacy_routed,
            t0,
        )

    def _route_anthropic(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        route_reason: str,
        privacy_routed: bool,
        t0: float,
    ) -> tuple[GatewayResponse, str, float]:
        """Attempt Anthropic provider, falling back on API errors."""
        try:
            response = self._call_anthropic(messages, model, max_tokens, temperature)
            return response, route_reason or "primary:anthropic", t0
        except (APIConnectionError, APIStatusError, RateLimitError) as exc:
            reason = f"{type(exc).__name__}"
            logger.warning("Anthropic API error, falling back: %s", exc)
            t0 = self._audit_failed_attempt(
                "anthropic", model, route_reason, t0, privacy_routed
            )
            response = self._fallback_chain(
                messages,
                max_tokens,
                reason,
                temperature,
                skip_provider="anthropic",
                privacy_routed=privacy_routed,
            )
            return response, f"fallback:{reason}", t0

    def _route_cloud(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        route_reason: str,
        privacy_routed: bool,
        t0: float,
        provider_key: str,
    ) -> tuple[GatewayResponse, str, float]:
        """Attempt cloud provider, falling back on errors."""
        try:
            response = self._call_openai_compat(
                messages, model, max_tokens, provider_key, temperature
            )
            return response, route_reason or f"primary:cloud:{provider_key}", t0
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            reason = f"{provider_key}: {type(exc).__name__}"
            logger.warning(
                "Cloud provider %s failed, falling back: %s", provider_key, exc
            )
            t0 = self._audit_failed_attempt(
                provider_key, model, route_reason, t0, privacy_routed
            )
            response = self._fallback_chain(
                messages,
                max_tokens,
                reason,
                temperature,
                skip_provider=provider_key,
                privacy_routed=privacy_routed,
            )
            return response, f"fallback:{reason}", t0

    def _route_cli(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        route_reason: str,
        privacy_routed: bool,
        t0: float,
        cli_key: str,
    ) -> tuple[GatewayResponse, str, float]:
        """Attempt CLI provider, falling back on errors or empty response."""
        try:
            response = self._call_cli(messages, model, max_tokens, cli_key)
            audit_reason = route_reason or f"primary:cli:{cli_key}"
            if response.provider == "none":
                t0 = self._audit_failed_attempt(
                    cli_key, model, audit_reason, t0, privacy_routed
                )
                reason = response.fallback_reason or f"{cli_key}_failed"
                response = self._fallback_chain(
                    messages,
                    max_tokens,
                    reason,
                    temperature,
                    skip_provider=cli_key,
                    privacy_routed=privacy_routed,
                )
                return response, f"fallback:{reason}", t0
            return response, audit_reason, t0
        except (OSError, RuntimeError, ValueError) as exc:
            reason = f"{cli_key}: {type(exc).__name__}"
            logger.warning("CLI provider %s failed, falling back: %s", cli_key, exc)
            audit_reason = route_reason or f"primary:cli:{cli_key}"
            t0 = self._audit_failed_attempt(
                cli_key, model, audit_reason, t0, privacy_routed
            )
            response = self._fallback_chain(
                messages,
                max_tokens,
                reason,
                temperature,
                skip_provider=cli_key,
                privacy_routed=privacy_routed,
            )
            return response, f"fallback:{reason}", t0

    def _route_ollama(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        route_reason: str,
        privacy_routed: bool,
        t0: float,
    ) -> tuple[GatewayResponse, str, float]:
        """Attempt Ollama, falling back to cloud if available and not privacy-routed."""
        response = self._call_ollama(messages, model, max_tokens, temperature)
        audit_reason = route_reason or "primary:ollama"
        if response.provider == "none" and self._cloud_keys and not privacy_routed:
            t0 = self._audit_failed_attempt(
                "ollama", model, audit_reason, t0, privacy_routed
            )
            response = self._fallback_chain(
                messages,
                max_tokens,
                response.fallback_reason or "ollama_failed",
                temperature,
                skip_ollama=True,
                privacy_routed=privacy_routed,
            )
            audit_reason = "fallback:ollama_failed"
        return response, audit_reason, t0

    def _log_completion(
        self,
        response: GatewayResponse,
        audit_reason: str,
        route_reason: str,
        latency_ms: float,
        privacy_routed: bool,
    ) -> None:
        """Log audit decision, cost tracking, and activity feed after completion."""
        self._audit_decision(
            provider=response.provider,
            model=response.model,
            reason=audit_reason,
            latency_ms=latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            success=response.provider != "none",
            fallback_from=response.fallback_reason if response.fallback_used else "",
            privacy_routed=privacy_routed,
        )

        if self._cost_tracker is not None:
            self._cost_tracker.log(
                model=response.model,
                provider=response.provider,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=response.cost_usd,
                route_reason=route_reason,
                fallback_used=response.fallback_used,
            )

        if _log_activity is not None:
            try:
                _log_activity(
                    "llm_routing",
                    f"Routed to {response.model} via {response.provider}",
                    {
                        "model": response.model,
                        "provider": response.provider,
                        "fallback": response.fallback_used,
                    },
                )
            except (OSError, ValueError, TypeError) as exc:
                logger.debug("Activity feed logging failed: %s", exc)

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str = DEFAULT_CLOUD_MODEL,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        route_reason: str = "",
        privacy_routed: bool = False,
        temperature: float | None = None,
    ) -> GatewayResponse:
        """Send a completion request to the appropriate provider.

        Automatically falls back through the provider chain on failure.
        Enforces budget caps when a BudgetEnforcer is configured — exceeding
        the daily or monthly cap causes an automatic reroute to local Ollama.
        Logs cost to CostTracker if one is configured.
        Logs routing decision to GatewayAudit if one is configured.

        Args:
            temperature: Sampling temperature override. When None, derived from
                route_reason/model: codex/math_logic -> 0.2, gemini/creative -> 0.85,
                default -> 0.7.
        """
        temperature = self._derive_temperature(model, route_reason, temperature)

        if getattr(self, "_closed", False):
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason="gateway is closed",
            )

        model = self._remap_model_if_needed(model)

        # Feedback quality check (soft signal — logs warning, does not override)
        self._check_feedback_quality(route_reason)

        # Context window guard: truncate or switch model if prompt is too large
        messages, model = self._apply_context_guard(messages, model)

        # Record chain-level start time for cumulative fallback latency
        t0_chain = time.monotonic()

        # Budget enforcement: check before making the call
        if self._budget is not None:
            # Import here to avoid circular import at module level
            from jarvis_engine.gateway.budget import BudgetExceededError

            try:
                # Estimate cost: use prompt size for input, max_tokens for output
                input_estimate = self._estimate_tokens(messages)
                estimated = self._budget.estimate_cost(
                    model, input_estimate, max_tokens
                )
                self._budget.check_budget(estimated)
            except BudgetExceededError as exc:
                logger.warning("Budget exceeded, routing to local Ollama: %s", exc)
                fallback_model = get_local_model()
                response = self._call_ollama(
                    messages, fallback_model, max_tokens, temperature
                )
                response.fallback_used = True
                response.fallback_reason = f"budget_exceeded:{exc.period}"
                t0 = time.perf_counter()
                latency_ms = 0.0
                self._log_completion(
                    response,
                    f"budget_exceeded:{exc.period}",
                    route_reason,
                    latency_ms,
                    privacy_routed,
                )
                return response

        response, audit_reason, t0 = self._route_to_provider(
            messages,
            model,
            max_tokens,
            temperature,
            route_reason,
            privacy_routed,
        )

        latency_ms = (time.perf_counter() - t0) * 1000

        # Cumulative chain latency (spans all retries/fallbacks)
        chain_latency_ms = (time.monotonic() - t0_chain) * 1000

        # Record provider health
        if self._health is not None:
            provider_name = response.provider
            if provider_name and provider_name != "none":
                self._health.record_success(provider_name, latency_ms)
            else:
                # All providers failed — record failure for the attempted model
                failed_provider = self._resolve_provider(model)
                if failed_provider:
                    # Strip routing prefix (e.g. "cli:claude-cli" → "claude-cli")
                    bare_provider = failed_provider.split(":", 1)[-1] if ":" in failed_provider else failed_provider
                    self._health.record_failure(bare_provider)

        # Record cost in budget enforcer
        if self._budget is not None and response.cost_usd > 0:
            self._budget.record_cost(
                response.cost_usd, response.model, response.provider
            )

        self._log_completion(
            response, audit_reason, route_reason, latency_ms, privacy_routed
        )

        # Log cumulative chain latency (useful when fallbacks added delay)
        if response.fallback_used:
            logger.info(
                "Chain latency: %.1fms (provider latency: %.1fms, model=%s, provider=%s)",
                chain_latency_ms,
                latency_ms,
                response.model,
                response.provider,
            )

        return response

    def _audit_decision(self, **kwargs: object) -> None:
        """Log a routing decision if audit is configured."""
        if self._audit is not None:
            self._audit.log_decision(**kwargs)  # type: ignore[arg-type]

    def _audit_failed_attempt(
        self,
        provider: str,
        model: str,
        route_reason: str,
        t0: float,
        privacy_routed: bool,
    ) -> float:
        """Log a failed provider attempt and return a reset timer value."""
        self._audit_decision(
            provider=provider,
            model=model,
            reason=route_reason or f"primary:{provider}",
            latency_ms=(time.perf_counter() - t0) * 1000,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            success=False,
            privacy_routed=privacy_routed,
        )
        # Record failure in health tracker for circuit breaker
        if self._health is not None:
            self._health.record_failure(provider)
        return time.perf_counter()

    def _call_openai_compat(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        provider_key: str,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> GatewayResponse:
        """Call an OpenAI-compatible API (Groq, Mistral, Z.ai)."""
        cfg = OPENAI_COMPAT_PROVIDERS[provider_key]
        api_key = self._cloud_keys[provider_key]

        # Resolve actual model ID for the API
        if model in CLOUD_MODEL_MAP:
            _, api_model = CLOUD_MODEL_MAP[model]
        else:
            api_model = model

        # Separate system messages (OpenAI format puts system in messages array)
        url = f"{cfg['base_url']}/chat/completions"
        payload = {
            "model": api_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Retry once on transient connection errors (stale keep-alive, reset).
        resp: httpx.Response | None = None
        _cloud_last_exc: Exception | None = None
        for _cloud_attempt in range(2):
            try:
                resp = self._http.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                break
            except httpx.HTTPError as exc:
                _cloud_last_exc = exc
                if _cloud_attempt == 0:
                    logger.info(
                        "%s connection error, retrying once: %s",
                        cfg["provider_name"], exc,
                    )
                    time.sleep(0.5)
                    continue
                raise RuntimeError(f"{cfg['provider_name']} request failed: {exc}") from exc
        else:
            raise RuntimeError(
                f"{cfg['provider_name']} request failed: {_cloud_last_exc}"
            ) from _cloud_last_exc

        assert resp is not None  # guaranteed by break/else above
        if resp.status_code == 429:
            headers = getattr(resp, "headers", None)
            retry_after_raw = (
                headers.get("Retry-After", "unknown")
                if headers is not None
                else "unknown"
            )
            # Short-wait retry: if Retry-After <= 5s, sleep and retry ONCE
            try:
                retry_after_s = float(retry_after_raw)
            except (TypeError, ValueError):
                retry_after_s = None

            if retry_after_s is not None and 0 < retry_after_s <= _RATE_LIMIT_MAX_RETRY_AFTER_S:
                logger.info(
                    "Rate limited by %s (HTTP 429). Retry-After: %.1fs — short wait, retrying once.",
                    cfg["provider_name"],
                    retry_after_s,
                )
                time.sleep(retry_after_s)
                try:
                    resp = self._http.post(
                        url,
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                except httpx.HTTPError as exc:
                    raise RuntimeError(
                        f"{cfg['provider_name']} retry request failed: {exc}"
                    ) from exc
                # If retry also fails, fall through to error handling below
                if resp.status_code == 429:
                    logger.warning(
                        "Retry after short wait still got 429 from %s",
                        cfg["provider_name"],
                    )
                    raise RuntimeError(
                        f"Rate limited by {cfg['provider_name']} (HTTP 429, "
                        f"Retry-After: {retry_after_raw}, retried once)"
                    )
                elif resp.status_code != 200:
                    error_text = resp.text[:200]
                    raise RuntimeError(f"HTTP {resp.status_code}: {error_text}")
                # else: retry succeeded, fall through to parse response
            else:
                logger.warning(
                    "Rate limited by %s (HTTP 429). Retry-After: %s — too long or unknown, failing.",
                    cfg["provider_name"],
                    retry_after_raw,
                )
                raise RuntimeError(
                    f"Rate limited by {cfg['provider_name']} (HTTP 429, "
                    f"Retry-After: {retry_after_raw})"
                )

        if resp.status_code != 200:
            error_text = resp.text[:200]
            raise RuntimeError(f"HTTP {resp.status_code}: {error_text}")

        data = resp.json()
        choices = data.get("choices", [])
        text = ""
        if choices:
            msg = choices[0].get("message", {})
            text = msg.get("content", "") or ""

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0) or 0
        output_tokens = usage.get("completion_tokens", 0) or 0
        cost = calculate_cost(model, input_tokens, output_tokens)

        return GatewayResponse(
            text=text,
            model=model,
            provider=cfg["provider_name"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    def _call_anthropic(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> GatewayResponse:
        """Call Anthropic API via the SDK."""
        if not _HAS_ANTHROPIC:
            raise RuntimeError("anthropic package is not installed")
        if self._anthropic is None:
            raise RuntimeError("Anthropic client is not initialized")
        # Resolve short aliases (e.g. "claude-opus") to full API model IDs
        api_model = ANTHROPIC_MODEL_ALIASES.get(model, model)
        # Anthropic Messages API requires system messages via `system=` param,
        # not in the messages array (only user/assistant roles allowed).
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        # Anthropic API requires at least one user/assistant message
        if not non_system:
            raise RuntimeError("No user/assistant messages to send to Anthropic")
        kwargs: dict = {
            "model": api_model,
            "max_tokens": max_tokens,
            "messages": non_system,
            "temperature": temperature,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        resp = self._anthropic.messages.create(**kwargs)
        if not resp.content:
            return GatewayResponse(
                text="",
                model=model,
                provider="anthropic",
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=calculate_cost(
                    model, resp.usage.input_tokens, resp.usage.output_tokens
                ),
            )
        # Extract text from all TextBlocks (content may contain tool_use blocks)
        text = "".join(
            block.text
            for block in resp.content
            if hasattr(block, "text") and block.text
        )
        input_tokens = resp.usage.input_tokens
        output_tokens = resp.usage.output_tokens
        cost = calculate_cost(model, input_tokens, output_tokens)

        return GatewayResponse(
            text=text,
            model=model,
            provider="anthropic",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    def _try_ollama_chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> tuple[GatewayResponse | None, str]:
        """Attempt an Ollama chat call, returning (response, error_reason).

        On success, returns ``(GatewayResponse, "")``.
        On failure, returns ``(None, reason_string)`` describing the error.
        Handles all Ollama-specific exception classes in one place.

        Retries up to ``_OLLAMA_RETRY_COUNT`` times on transient connection
        errors (e.g. ``RemoteDisconnected``, connection reset) with
        exponential backoff to survive brief Ollama restarts and stale
        HTTP keep-alive connections.
        """
        if self._ollama is None:
            return None, "ollama package is not installed"

        last_error = ""
        for attempt in range(_OLLAMA_RETRY_COUNT + 1):
            try:
                resp = self._ollama.chat(
                    model=model,
                    messages=messages,
                    options={"num_predict": max_tokens, "temperature": temperature},
                    think=False,
                )
            except (ConnectionError, ResponseError, TimeoutError, OSError) as exc:
                last_error = "Ollama error"
                if attempt < _OLLAMA_RETRY_COUNT:
                    delay = _OLLAMA_RETRY_BASE_S * (2 ** attempt)
                    logger.info(
                        "Ollama call failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, _OLLAMA_RETRY_COUNT + 1, delay, exc,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("Ollama call failed after %d attempts: %s", attempt + 1, exc)
                return None, last_error
            except (RuntimeError, ValueError, TypeError) as exc:
                last_error = f"Ollama error: {type(exc).__name__}"
                if attempt < _OLLAMA_RETRY_COUNT:
                    delay = _OLLAMA_RETRY_BASE_S * (2 ** attempt)
                    logger.info(
                        "Ollama call failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, _OLLAMA_RETRY_COUNT + 1, delay, exc,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("Ollama call failed (unexpected) after %d attempts: %s", attempt + 1, exc)
                return None, last_error

            text = (resp.message.content if resp.message else "") or ""
            input_tokens = getattr(resp, "prompt_eval_count", 0) or 0
            output_tokens = getattr(resp, "eval_count", 0) or 0

            return GatewayResponse(
                text=text,
                model=model,
                provider="ollama",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=0.0,
            ), ""

        return None, last_error or "Ollama error"

    def _call_ollama(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> GatewayResponse:
        """Call local Ollama server."""
        resp, error = self._try_ollama_chat(messages, model, max_tokens, temperature)
        if resp is not None:
            return resp
        return GatewayResponse(
            text="",
            model=model,
            provider="none",
            fallback_used=True,
            fallback_reason=error,
        )

    def _call_cli(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        cli_key: str,
    ) -> GatewayResponse:
        """Call a CLI-based LLM provider (Claude Code, Codex, Gemini, Kimi)."""
        model_override = None
        if model and model not in CLI_MODEL_MAP and model != cli_key:
            model_override = model
        result = call_cli_provider(cli_key, messages, max_tokens, model=model_override)
        if not result.get("success"):
            error = result.get("error", "unknown CLI error")
            logger.warning("CLI provider %s failed: %s", cli_key, error)
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason=f"CLI {cli_key}: {error}",
            )
        return GatewayResponse(
            text=result.get("text", ""),
            model=model,
            provider=cli_key,
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            cost_usd=result.get("cost_usd", 0.0),
        )

    def _fallback_chain(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        reason: str,
        temperature: float = _DEFAULT_TEMPERATURE,
        skip_provider: str = "",
        skip_ollama: bool = False,
        privacy_routed: bool = False,
    ) -> GatewayResponse:
        """Try remaining cloud providers, then fall back to local Ollama.

        Tries each available cloud provider in priority order (skipping
        the one that already failed) before falling back to local Ollama.

        CTX-02/CTX-06: The *same* ``messages`` list is passed through every
        fallback attempt unchanged.  The task context (system prompt,
        conversation history, user query) is never rebuilt or restarted.
        Each successful fallback response carries ``_continuity_context``
        metadata so callers can verify intent preservation.

        When skip_ollama=True, do not retry Ollama at the end of the chain
        (used when Ollama was already the primary and failed).

        When privacy_routed=True, skip ALL cloud and CLI providers and go
        directly to local Ollama to prevent private data leaking off-device.
        """
        # CTX-06: Snapshot the first message to prove context is never restarted.
        # This reference is checked by _attach_continuity_context below.
        original_first_message = messages[0] if messages else None

        if not privacy_routed:
            self._refresh_cli_providers()

            # Cost-aware provider ordering: sort cloud providers by cost
            priority = ["groq", "mistral", "zai"]
            if self._budget is not None:
                priority = self._budget.rank_providers_by_cost(priority)

            # Try other cloud providers first
            for pk in priority:
                if pk == skip_provider or pk not in self._cloud_keys:
                    continue
                # Circuit breaker: skip providers in cooldown
                if self._health is not None and self._health.should_skip(pk):
                    logger.info("Skipping %s in fallback chain (circuit open)", pk)
                    continue
                # Look up the preferred model for this provider (O(1) lookup)
                model_alias = _PROVIDER_DEFAULT_MODEL.get(pk)
                if model_alias is None:
                    continue
                try:
                    resp = self._call_openai_compat(
                        messages, model_alias, max_tokens, pk, temperature
                    )
                    resp.fallback_used = True
                    resp.fallback_reason = reason
                    self._attach_continuity_context(
                        resp, skip_provider, original_first_message
                    )
                    # NOTE: Don't record_success here — complete() records it
                    # with real latency to avoid double-counting.
                    return resp
                except (OSError, RuntimeError, ValueError, KeyError) as exc:
                    logger.warning("Fallback to %s also failed: %s", pk, exc)
                    if self._health is not None:
                        self._health.record_failure(pk)

            # Try CLI-based providers as fallback (free via subscription)
            cli_priority = ["claude-cli", "gemini-cli", "codex-cli", "kimi-cli"]
            for cli_key in cli_priority:
                if cli_key == skip_provider or cli_key not in self._cli_providers:
                    continue
                # Circuit breaker for CLI providers too
                if self._health is not None and self._health.should_skip(cli_key):
                    logger.info("Skipping %s in fallback chain (circuit open)", cli_key)
                    continue
                try:
                    resp = self._call_cli(messages, cli_key, max_tokens, cli_key)
                    if resp.provider != "none":
                        resp.fallback_used = True
                        resp.fallback_reason = reason
                        self._attach_continuity_context(
                            resp, skip_provider, original_first_message
                        )
                        return resp
                    logger.warning("CLI fallback %s returned empty", cli_key)
                    if self._health is not None:
                        self._health.record_failure(cli_key)
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("CLI fallback %s failed: %s", cli_key, exc)
                    if self._health is not None:
                        self._health.record_failure(cli_key)

            # Try Anthropic as fallback if available and not the one that failed
            if self._anthropic is not None and skip_provider != "anthropic":
                should_skip_anthropic = (
                    self._health is not None and self._health.should_skip("anthropic")
                )
                if not should_skip_anthropic:
                    try:
                        resp = self._call_anthropic(
                            messages, "claude-haiku", max_tokens, temperature
                        )
                        resp.fallback_used = True
                        resp.fallback_reason = reason
                        self._attach_continuity_context(
                            resp, skip_provider, original_first_message
                        )
                        return resp
                    except (
                        OSError,
                        RuntimeError,
                        ValueError,
                        APIConnectionError,
                        APIStatusError,
                        RateLimitError,
                    ) as exc:
                        logger.warning("Fallback to Anthropic also failed: %s", exc)
                        if self._health is not None:
                            self._health.record_failure("anthropic")

        # All cloud providers failed
        if skip_ollama:
            # Ollama already tried as primary -- don't double-retry
            full_reason = f"{reason} -> all cloud fallbacks also failed"
            logger.error("All providers failed: %s", full_reason)
            fallback_model = get_local_model()
            resp = GatewayResponse(
                text="",
                model=fallback_model,
                provider="none",
                fallback_used=True,
                fallback_reason=full_reason,
            )
            self._attach_continuity_context(
                resp, skip_provider, original_first_message
            )
            return resp
        resp = self._fallback_to_ollama(messages, max_tokens, reason, temperature)
        self._attach_continuity_context(resp, skip_provider, original_first_message)
        return resp

    def _attach_continuity_context(
        self,
        resp: GatewayResponse,
        original_provider: str,
        original_first_message: dict[str, str] | None,
    ) -> None:
        """Populate ``_continuity_context`` on a fallback response (CTX-02/CTX-06).

        Also logs the route change to the activity feed so the telemetry
        pipeline can track provider switches during a single request.
        """
        resp._continuity_context = {
            "original_model": original_provider,
            "fallback_model": resp.model,
            "intent_preserved": True,
            "first_message_preserved": (
                original_first_message is not None
            ),
        }

        # Log route change metadata to activity feed
        if _log_activity is not None:
            try:
                _log_activity(
                    "llm_routing",
                    f"Fallback route change: {original_provider} -> {resp.provider} ({resp.model})",
                    {
                        "event": "fallback_route_change",
                        "original_provider": original_provider,
                        "fallback_provider": resp.provider,
                        "fallback_model": resp.model,
                        "intent_preserved": True,
                        "reason": resp.fallback_reason,
                    },
                )
            except (OSError, ValueError, TypeError) as exc:
                logger.debug("Fallback route change activity logging failed: %s", exc)

    def _fallback_to_ollama(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        reason: str,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> GatewayResponse:
        """Fall back to local Ollama after all cloud providers fail.

        Uses JARVIS_LOCAL_MODEL env var if set, otherwise defaults to qwen3.5:latest.
        Returns a graceful error response if Ollama also fails.
        """
        fallback_model = get_local_model()
        resp, error = self._try_ollama_chat(
            messages, fallback_model, max_tokens, temperature
        )
        if resp is not None:
            resp.fallback_used = True
            resp.fallback_reason = reason
            return resp
        full_reason = f"{reason} -> Ollama also failed" + (
            f": {error}" if error != "Ollama error" else ""
        )
        logger.error("All providers failed: %s", full_reason)
        return GatewayResponse(
            text="",
            model=fallback_model,
            provider="none",
            fallback_used=True,
            fallback_reason=full_reason,
        )

    def check_ollama(self) -> bool:
        """Check if local Ollama server is reachable.

        Retries once on transient connection errors (stale keep-alive).
        """
        if self._ollama is None:
            return False
        for attempt in range(2):
            try:
                self._ollama.list()
                return True
            except (ConnectionError, TimeoutError, OSError) as exc:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                logger.debug("Ollama health check failed: %s", exc)
                return False
        return False

    def check_anthropic(self) -> bool:
        return self._anthropic is not None

    def check_cloud(self) -> dict[str, bool]:
        return {k: True for k in self._cloud_keys}

    def check_cli(self) -> dict[str, bool]:
        """Check which CLI-based LLM providers are available."""
        self._refresh_cli_providers()
        return {k: True for k in self._cli_providers}

    def available_model_names(self) -> set[str]:
        """Return set of all model names that can actually be routed.

        Useful for passing to IntentClassifier.classify(available_models=...)
        so it only routes to models that are actually available.
        """
        self._refresh_cli_providers()
        models: set[str] = set()
        # Anthropic models
        if self._anthropic is not None:
            models.update(ANTHROPIC_MODEL_ALIASES.keys())
        # Cloud API models
        for alias, (pk, _) in CLOUD_MODEL_MAP.items():
            if pk in self._cloud_keys:
                models.add(alias)
        # CLI-based models
        for cli_key in self._cli_providers:
            models.add(cli_key)
        # Ollama (local) — always add both local model names
        if self._ollama is not None:
            models.add(get_local_model())
            models.add(get_fast_local_model())
        return models

    def available_providers(self) -> list[str]:
        """Return list of all available provider names."""
        self._refresh_cli_providers()
        providers = []
        if self._anthropic is not None:
            providers.append("anthropic")
        providers.extend(self._cloud_keys.keys())
        providers.extend(self._cli_providers.keys())
        if self.check_ollama():
            providers.append("ollama")
        return providers
