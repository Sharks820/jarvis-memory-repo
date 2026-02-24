"""ModelGateway: unified LLM completion interface.

Wraps Anthropic SDK, OpenAI-compatible cloud APIs (Groq, Mistral, Z.ai),
and Ollama Python client with:
- Automatic provider resolution based on model name
- Fallback chain: cloud failure -> local Ollama -> graceful error
- Per-query cost tracking via CostTracker
- Local-only mode when no API key is configured
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

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
    from ollama import Client as OllamaClient, ResponseError
    _HAS_OLLAMA = True
except ImportError:
    _HAS_OLLAMA = False
    OllamaClient = None  # type: ignore[assignment,misc]

    class ResponseError(Exception):  # type: ignore[no-redef]
        pass

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
    ) -> None:
        if anthropic_api_key is not None:
            if _HAS_ANTHROPIC:
                self._anthropic: Anthropic | None = Anthropic(api_key=anthropic_api_key)
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
        self._http = httpx.Client(timeout=30.0)

        # Log available providers
        available = []
        if self._anthropic is not None:
            available.append("anthropic")
        available.extend(self._cloud_keys.keys())
        if _HAS_OLLAMA:
            available.append("ollama")
        if not available:
            logger.warning("No LLM providers configured -- all calls will fail")
        else:
            logger.info("LLM providers available: %s", ", ".join(available))

    def close(self) -> None:
        """Release httpx connection pool and other resources."""
        self._http.close()

    def __enter__(self) -> "ModelGateway":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _resolve_provider(self, model: str) -> str:
        """Determine which provider to use for a given model."""
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
            return "kimi-k2"
        if "mistral" in self._cloud_keys:
            return "devstral-2"
        if "zai" in self._cloud_keys:
            return "glm-4.7-flash"
        return None

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 1024,
        route_reason: str = "",
    ) -> GatewayResponse:
        """Send a completion request to the appropriate provider.

        Automatically falls back through the provider chain on failure.
        Logs cost to CostTracker if one is configured.
        """
        provider = self._resolve_provider(model)

        if provider == "anthropic":
            try:
                response = self._call_anthropic(messages, model, max_tokens)
            except (APIConnectionError, APIStatusError, RateLimitError) as exc:
                reason = f"{type(exc).__name__}"
                logger.warning("Anthropic API error, falling back: %s", exc)
                response = self._fallback_chain(messages, max_tokens, reason, skip_provider="anthropic")
        elif provider.startswith("cloud:"):
            provider_key = provider.split(":", 1)[1]
            try:
                response = self._call_openai_compat(messages, model, max_tokens, provider_key)
            except Exception as exc:
                reason = f"{provider_key}: {type(exc).__name__}"
                logger.warning("Cloud provider %s failed, falling back: %s", provider_key, exc)
                response = self._fallback_chain(messages, max_tokens, reason, skip_provider=provider_key)
        else:
            response = self._call_ollama(messages, model, max_tokens)

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

        return response

    def _call_openai_compat(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        provider_key: str,
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
            "temperature": 0.7,
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
    ) -> GatewayResponse:
        """Call Anthropic API via the SDK."""
        if not _HAS_ANTHROPIC:
            raise RuntimeError("anthropic package is not installed")
        if self._anthropic is None:
            raise RuntimeError("Anthropic client is not initialized")
        # Resolve short aliases (e.g. "claude-opus") to full API model IDs
        api_model = ANTHROPIC_MODEL_ALIASES.get(model, model)
        resp = self._anthropic.messages.create(
            model=api_model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if not resp.content:
            return GatewayResponse(
                text="",
                model=model,
                provider="anthropic",
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=calculate_cost(model, resp.usage.input_tokens, resp.usage.output_tokens),
            )
        # Extract text from first TextBlock (content may contain tool_use blocks)
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text = block.text
                break
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
            resp = self._ollama.chat(model=model, messages=messages)
        except (ConnectionError, ResponseError, TimeoutError, OSError) as exc:
            logger.warning("Ollama call failed: %s", exc)
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason="Ollama error",
            )
        except Exception as exc:
            # Catch httpx transport/timeout errors that don't inherit from builtins
            logger.warning("Ollama call failed (unexpected): %s", exc)
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason=f"Ollama error: {type(exc).__name__}",
            )
        text = resp.message.content if resp.message else ""
        input_tokens = getattr(resp, "prompt_eval_count", 0) or 0
        output_tokens = getattr(resp, "eval_count", 0) or 0

        return GatewayResponse(
            text=text or "",
            model=model,
            provider="ollama",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
        )

    def _fallback_chain(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        reason: str,
        skip_provider: str = "",
    ) -> GatewayResponse:
        """Try remaining cloud providers, then fall back to local Ollama.

        Tries each available cloud provider in priority order (skipping
        the one that already failed) before falling back to local Ollama.
        """
        # Try other cloud providers first
        priority = ["groq", "mistral", "zai"]
        for pk in priority:
            if pk == skip_provider or pk not in self._cloud_keys:
                continue
            # Find a default model for this provider
            for model_alias, (provider_key, _) in CLOUD_MODEL_MAP.items():
                if provider_key == pk:
                    try:
                        resp = self._call_openai_compat(messages, model_alias, max_tokens, pk)
                        resp.fallback_used = True
                        resp.fallback_reason = reason
                        return resp
                    except Exception as exc:
                        logger.warning("Fallback to %s also failed: %s", pk, exc)
                    break

        # All cloud providers failed, fall back to local Ollama
        return self._fallback_to_ollama(messages, max_tokens, reason)

    def _fallback_to_ollama(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        reason: str,
    ) -> GatewayResponse:
        """Fall back to local Ollama after all cloud providers fail.

        Uses JARVIS_LOCAL_MODEL env var if set, otherwise defaults to gemma3:4b.
        Returns a graceful error response if Ollama also fails.
        """
        fallback_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")

        if not _HAS_OLLAMA:
            full_reason = f"{reason} -> Ollama also failed: ollama package is not installed"
            logger.error("All providers failed: %s", full_reason)
            return GatewayResponse(
                text="",
                model=fallback_model,
                provider="none",
                fallback_used=True,
                fallback_reason=full_reason,
            )

        try:
            resp = self._ollama.chat(model=fallback_model, messages=messages)
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
        except Exception as exc:
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
        except Exception:
            return False

    def check_anthropic(self) -> bool:
        """Check if Anthropic client is configured (has API key)."""
        return self._anthropic is not None

    def check_cloud(self) -> dict[str, bool]:
        """Check which cloud providers have API keys configured."""
        return {k: True for k in self._cloud_keys}

    def available_providers(self) -> list[str]:
        """Return list of all available provider names."""
        providers = []
        if self._anthropic is not None:
            providers.append("anthropic")
        providers.extend(self._cloud_keys.keys())
        if self.check_ollama():
            providers.append("ollama")
        return providers
