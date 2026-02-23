"""ModelGateway: unified LLM completion interface.

Wraps Anthropic SDK and Ollama Python client with:
- Automatic provider resolution based on model name
- Fallback chain: cloud failure -> local Ollama -> graceful error
- Per-query cost tracking via CostTracker
- Local-only mode when no API key is configured
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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

    Dispatches to Anthropic or Ollama based on model name. Handles
    API failures gracefully with automatic fallback to local models.
    Logs per-query costs when a CostTracker is provided.
    """

    def __init__(
        self,
        anthropic_api_key: str | None = None,
        ollama_host: str = "http://127.0.0.1:11434",
        cost_tracker: "CostTracker | None" = None,
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
            logger.warning(
                "No Anthropic API key configured -- operating in local-only mode"
            )

        if _HAS_OLLAMA:
            self._ollama = OllamaClient(host=ollama_host)
        else:
            self._ollama = None
        self._cost_tracker = cost_tracker

    def _resolve_provider(self, model: str) -> str:
        """Determine which provider to use for a given model."""
        if model.startswith("claude-") and self._anthropic is not None:
            return "anthropic"
        return "ollama"

    def complete(
        self,
        messages: list[dict[str, str]],
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 1024,
        route_reason: str = "",
    ) -> GatewayResponse:
        """Send a completion request to the appropriate provider.

        Automatically falls back to local Ollama if Anthropic fails.
        Logs cost to CostTracker if one is configured.
        """
        provider = self._resolve_provider(model)

        if provider == "anthropic":
            try:
                response = self._call_anthropic(messages, model, max_tokens)
            except (APIConnectionError, APIStatusError, RateLimitError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                logger.warning("Anthropic API error, falling back to Ollama: %s", reason)
                response = self._fallback_to_ollama(messages, max_tokens, reason)
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
        resp = self._anthropic.messages.create(
            model=model,
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
        except (ConnectionError, ResponseError) as exc:
            logger.warning("Ollama call failed: %s", exc)
            return GatewayResponse(
                text="",
                model=model,
                provider="none",
                fallback_used=True,
                fallback_reason=f"Ollama error: {exc}",
            )
        text = resp.message.content

        return GatewayResponse(
            text=text,
            model=model,
            provider="ollama",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )

    def _fallback_to_ollama(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        reason: str,
    ) -> GatewayResponse:
        """Fall back to local Ollama after a cloud provider failure.

        Uses JARVIS_LOCAL_MODEL env var if set, otherwise defaults to qwen3:14b.
        Returns a graceful error response if Ollama also fails.
        """
        fallback_model = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3:14b")

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
            return GatewayResponse(
                text=resp.message.content,
                model=fallback_model,
                provider="ollama",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                fallback_used=True,
                fallback_reason=reason,
            )
        except (ConnectionError, ResponseError) as exc:
            full_reason = f"{reason} -> Ollama also failed: {exc}"
            logger.error("All providers failed: %s", full_reason)
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
