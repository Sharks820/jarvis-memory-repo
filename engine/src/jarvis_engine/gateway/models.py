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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from jarvis_engine._constants import (
    DEFAULT_CLOUD_MODEL,
    get_local_model as _get_local_model,
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


try:
    from jarvis_engine.activity_feed import log_activity as _log_activity
except ImportError:
    _log_activity = None

try:
    from ollama import Client as OllamaClient, ResponseError

    _HAS_OLLAMA = True
except ImportError:
    _HAS_OLLAMA = False
    OllamaClient = None  # type: ignore[assignment,misc]

    class ResponseError(Exception):  # type: ignore[no-redef]
        pass


from jarvis_engine.gateway.audit import GatewayAudit
from jarvis_engine.gateway.cli_providers import (
    call_cli_provider,
    detect_cli_providers,
    CLIProviderInfo,
)
from jarvis_engine.gateway.pricing import calculate_cost

if TYPE_CHECKING:
    from jarvis_engine.gateway.costs import CostTracker

logger = logging.getLogger(__name__)


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
    raw = os.environ.get("JARVIS_CLI_PROVIDER_REFRESH_S", "5").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 5.0
    return max(1.0, min(value, 300.0))


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
    ) -> None:
        self._closed = False
        self._audit: GatewayAudit | None = (
            GatewayAudit(audit_path) if audit_path is not None else None
        )
        if anthropic_api_key is not None:
            if _HAS_ANTHROPIC:
                self._anthropic: Anthropic | None = Anthropic(
                    api_key=anthropic_api_key, timeout=60.0
                )
            else:
                self._anthropic = None
                logger.warning(
                    "Anthropic API key provided but anthropic package is not installed"
                )
        else:
            self._anthropic = None

        if _HAS_OLLAMA:
            self._ollama = OllamaClient(host=ollama_host, timeout=120.0)
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
        # 60s timeout matches Anthropic SDK; LLM completions can be slow
        self._http = httpx.Client(timeout=60.0)

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
        for key, info in self._cli_providers.items():
            available.append(f"{key}")
        if _HAS_OLLAMA:
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
        except Exception as exc:
            # Logger may be None during interpreter shutdown; best-effort debug log
            try:
                logger.debug("ModelGateway.__del__ cleanup failed: %s", exc)
            except Exception:
                pass

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

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str = DEFAULT_CLOUD_MODEL,
        max_tokens: int = 1024,
        route_reason: str = "",
        privacy_routed: bool = False,
        temperature: float | None = None,
    ) -> GatewayResponse:
        """Send a completion request to the appropriate provider.

        Automatically falls back through the provider chain on failure.
        Logs cost to CostTracker if one is configured.
        Logs routing decision to GatewayAudit if one is configured.

        Args:
            temperature: Sampling temperature override. When None, derived from
                route_reason/model: codex/math_logic -> 0.2, gemini/creative -> 0.85,
                default -> 0.7.
        """
        # Derive temperature from route/model when not explicitly provided
        if temperature is None:
            model_lower = model.lower()
            reason_lower = route_reason.lower()
            if "codex" in model_lower or "math_logic" in reason_lower:
                temperature = 0.2
            elif "gemini" in model_lower or "creative" in reason_lower:
                temperature = 0.85
            else:
                temperature = 0.7

        if getattr(self, "_closed", False):
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason="gateway is closed",
            )

        # If Claude API requested but Anthropic unavailable, remap to best cloud model
        # Skip CLI models (claude-cli) — those use the CLI, not the Anthropic API
        if (
            model.startswith("claude-")
            and model not in CLI_MODEL_MAP
            and self._anthropic is None
        ):
            best = self._best_cloud_model()
            if best:
                logger.info("Anthropic unavailable, routing %s -> %s", model, best)
                model = best

        provider = self._resolve_provider(model)
        t0 = time.perf_counter()

        if provider == "anthropic":
            try:
                response = self._call_anthropic(
                    messages, model, max_tokens, temperature
                )
                audit_reason = route_reason or "primary:anthropic"
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
                audit_reason = f"fallback:{reason}"
        elif provider.startswith("cloud:"):
            provider_key = provider.split(":", 1)[1]
            try:
                response = self._call_openai_compat(
                    messages, model, max_tokens, provider_key, temperature
                )
                audit_reason = route_reason or f"primary:cloud:{provider_key}"
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
                audit_reason = f"fallback:{reason}"
        elif provider.startswith("cli:"):
            cli_key = provider.split(":", 1)[1]
            try:
                response = self._call_cli(messages, model, max_tokens, cli_key)
                audit_reason = route_reason or f"primary:cli:{cli_key}"
                if response.provider == "none":
                    # CLI call failed, try fallback chain
                    t0 = self._audit_failed_attempt(
                        cli_key,
                        model,
                        route_reason or f"primary:cli:{cli_key}",
                        t0,
                        privacy_routed,
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
                    audit_reason = f"fallback:{reason}"
            except (OSError, RuntimeError, ValueError) as exc:
                reason = f"{cli_key}: {type(exc).__name__}"
                logger.warning("CLI provider %s failed, falling back: %s", cli_key, exc)
                t0 = self._audit_failed_attempt(
                    cli_key,
                    model,
                    route_reason or f"primary:cli:{cli_key}",
                    t0,
                    privacy_routed,
                )
                response = self._fallback_chain(
                    messages,
                    max_tokens,
                    reason,
                    temperature,
                    skip_provider=cli_key,
                    privacy_routed=privacy_routed,
                )
                audit_reason = f"fallback:{reason}"
        else:
            response = self._call_ollama(messages, model, max_tokens, temperature)
            audit_reason = route_reason or "primary:ollama"
            # If Ollama failed and cloud providers are available, try them
            # skip_ollama=True to avoid re-trying Ollama at the end of the chain
            # NEVER fall back to cloud for privacy-routed queries
            if response.provider == "none" and self._cloud_keys and not privacy_routed:
                # Log the failed Ollama attempt (consistent with Anthropic/cloud paths)
                t0 = self._audit_failed_attempt(
                    "ollama",
                    model,
                    route_reason or "primary:ollama",
                    t0,
                    privacy_routed,
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

        latency_ms = (time.perf_counter() - t0) * 1000

        # Log the successful (or final fallback) decision
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

        # Log cost if tracker is configured
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

        # Log routing decision to activity feed
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
        return time.perf_counter()

    def _call_openai_compat(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        provider_key: str,
        temperature: float = 0.7,
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

        resp = self._http.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
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
        temperature: float = 0.7,
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

    def _call_ollama(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float = 0.7,
    ) -> GatewayResponse:
        """Call local Ollama server."""
        if not _HAS_OLLAMA:
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason="ollama package is not installed",
            )
        try:
            resp = self._ollama.chat(
                model=model,
                messages=messages,
                options={"num_predict": max_tokens, "temperature": temperature},
            )
        except (ConnectionError, ResponseError, TimeoutError, OSError) as exc:
            logger.warning("Ollama call failed: %s", exc)
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason="Ollama error",
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            # Catch httpx transport/timeout errors that don't inherit from builtins
            logger.warning("Ollama call failed (unexpected): %s", exc)
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason=f"Ollama error: {type(exc).__name__}",
            )
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
            cost_usd=result.get("cost_usd", 0.0),
        )

    def _fallback_chain(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        reason: str,
        temperature: float = 0.7,
        skip_provider: str = "",
        skip_ollama: bool = False,
        privacy_routed: bool = False,
    ) -> GatewayResponse:
        """Try remaining cloud providers, then fall back to local Ollama.

        Tries each available cloud provider in priority order (skipping
        the one that already failed) before falling back to local Ollama.

        When skip_ollama=True, do not retry Ollama at the end of the chain
        (used when Ollama was already the primary and failed).

        When privacy_routed=True, skip ALL cloud and CLI providers and go
        directly to local Ollama to prevent private data leaking off-device.
        """
        if not privacy_routed:
            self._refresh_cli_providers()
            # Try other cloud providers first
            priority = ["groq", "mistral", "zai"]
            for pk in priority:
                if pk == skip_provider or pk not in self._cloud_keys:
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
                    return resp
                except (OSError, RuntimeError, ValueError, KeyError) as exc:
                    logger.warning("Fallback to %s also failed: %s", pk, exc)

            # Try CLI-based providers as fallback (free via subscription)
            cli_priority = ["claude-cli", "gemini-cli", "codex-cli", "kimi-cli"]
            for cli_key in cli_priority:
                if cli_key == skip_provider or cli_key not in self._cli_providers:
                    continue
                try:
                    resp = self._call_cli(messages, cli_key, max_tokens, cli_key)
                    if resp.provider != "none":
                        resp.fallback_used = True
                        resp.fallback_reason = reason
                        return resp
                    logger.warning("CLI fallback %s returned empty", cli_key)
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("CLI fallback %s failed: %s", cli_key, exc)

            # Try Anthropic as fallback if available and not the one that failed
            if self._anthropic is not None and skip_provider != "anthropic":
                try:
                    resp = self._call_anthropic(
                        messages, "claude-haiku", max_tokens, temperature
                    )
                    resp.fallback_used = True
                    resp.fallback_reason = reason
                    return resp
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("Fallback to Anthropic also failed: %s", exc)

        # All cloud providers failed
        if skip_ollama:
            # Ollama already tried as primary -- don't double-retry
            full_reason = f"{reason} -> all cloud fallbacks also failed"
            logger.error("All providers failed: %s", full_reason)
            fallback_model = _get_local_model()
            return GatewayResponse(
                text="",
                model=fallback_model,
                provider="none",
                fallback_used=True,
                fallback_reason=full_reason,
            )
        return self._fallback_to_ollama(messages, max_tokens, reason, temperature)

    def _fallback_to_ollama(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        reason: str,
        temperature: float = 0.7,
    ) -> GatewayResponse:
        """Fall back to local Ollama after all cloud providers fail.

        Uses JARVIS_LOCAL_MODEL env var if set, otherwise defaults to gemma3:4b.
        Returns a graceful error response if Ollama also fails.
        """
        fallback_model = _get_local_model()

        if not _HAS_OLLAMA:
            full_reason = (
                f"{reason} -> Ollama also failed: ollama package is not installed"
            )
            logger.error("All providers failed: %s", full_reason)
            return GatewayResponse(
                text="",
                model=fallback_model,
                provider="none",
                fallback_used=True,
                fallback_reason=full_reason,
            )

        try:
            resp = self._ollama.chat(
                model=fallback_model,
                messages=messages,
                options={"num_predict": max_tokens, "temperature": temperature},
            )
            input_tokens = getattr(resp, "prompt_eval_count", 0) or 0
            output_tokens = getattr(resp, "eval_count", 0) or 0
            return GatewayResponse(
                text=(resp.message.content if resp.message else "") or "",
                model=fallback_model,
                provider="ollama",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=0.0,
                fallback_used=True,
                fallback_reason=reason,
            )
        except (ConnectionError, ResponseError, TimeoutError, OSError) as exc:
            full_reason = f"{reason} -> Ollama also failed"
            logger.error("All providers failed: %s -> %s", reason, exc)
            return GatewayResponse(
                text="",
                model=fallback_model,
                provider="none",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                fallback_used=True,
                fallback_reason=full_reason,
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            full_reason = f"{reason} -> Ollama also failed: {type(exc).__name__}"
            logger.error("All providers failed: %s -> %s", reason, exc)
            return GatewayResponse(
                text="",
                model=fallback_model,
                provider="none",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                fallback_used=True,
                fallback_reason=full_reason,
            )

    def check_ollama(self) -> bool:
        """Check if local Ollama server is reachable."""
        if not _HAS_OLLAMA or self._ollama is None:
            return False
        try:
            self._ollama.list()
            return True
        except (ConnectionError, TimeoutError, OSError) as exc:
            logger.debug("Ollama health check failed: %s", exc)
            return False

    def check_anthropic(self) -> bool:
        """Check if Anthropic client is configured (has API key)."""
        return self._anthropic is not None

    def check_cloud(self) -> dict[str, bool]:
        """Check which cloud providers have API keys configured."""
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
        # Ollama (local) — always add local model name
        if _HAS_OLLAMA:
            models.add(_get_local_model())
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
