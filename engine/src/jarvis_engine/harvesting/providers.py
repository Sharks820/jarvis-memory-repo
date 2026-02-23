"""Harvesting provider abstraction and concrete implementations.

Supports MiniMax, Kimi (Moonshot + NVIDIA NIM), and Gemini providers.
Each provider wraps an LLM API for topic-based knowledge extraction.
Providers that lack API keys degrade gracefully (_available=False).
"""

from __future__ import annotations

import logging
import os

from jarvis_engine.harvesting.harvester import HarvestResult

logger = logging.getLogger(__name__)


class HarvesterProvider:
    """Base class for OpenAI-compatible harvesting providers.

    Lazy-creates the OpenAI client on first query() call to avoid
    import-time SDK dependency.
    """

    def __init__(
        self,
        name: str,
        api_key_env: str,
        base_url: str,
        model: str,
        input_cost_per_mtok: float,
        output_cost_per_mtok: float,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url
        self.input_cost_per_mtok = input_cost_per_mtok
        self.output_cost_per_mtok = output_cost_per_mtok

        self._api_key_env = api_key_env
        self._api_key = os.environ.get(api_key_env, "")
        self._available = bool(self._api_key)
        self._client = None

    @property
    def is_available(self) -> bool:
        return self._available

    def _get_client(self):
        """Lazy-create OpenAI client on first use."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    f"Provider {self.name} requires 'openai' package. "
                    "Install with: pip install openai>=1.0.0"
                )

            self._client = OpenAI(api_key=self._api_key, base_url=self.base_url)
        return self._client

    def query(
        self,
        topic: str,
        system_prompt: str,
        max_tokens: int = 2048,
    ) -> HarvestResult:
        """Query the provider for knowledge about a topic.

        Raises RuntimeError if provider is not configured (no API key).
        """
        if not self._available:
            raise RuntimeError(f"Provider {self.name} not configured")

        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": topic},
            ],
            max_tokens=max_tokens,
        )

        if not response.choices:
            return HarvestResult(
                provider=self.name,
                text="",
                model=self.model,
            )

        choice = response.choices[0]
        text = choice.message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        cost = (
            input_tokens * self.input_cost_per_mtok
            + output_tokens * self.output_cost_per_mtok
        ) / 1_000_000

        return HarvestResult(
            provider=self.name,
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )


class MiniMaxProvider(HarvesterProvider):
    """MiniMax MiniMax-M2.5 via OpenAI-compatible API."""

    def __init__(self) -> None:
        super().__init__(
            name="minimax",
            api_key_env="MINIMAX_API_KEY",
            base_url="https://api.minimax.io/v1",
            model="MiniMax-M2.5",
            input_cost_per_mtok=0.30,
            output_cost_per_mtok=1.20,
        )


class KimiProvider(HarvesterProvider):
    """Kimi k2.5 via Moonshot OpenAI-compatible API."""

    def __init__(self) -> None:
        super().__init__(
            name="kimi",
            api_key_env="KIMI_API_KEY",
            base_url="https://api.moonshot.cn/v1",
            model="kimi-k2.5",
            input_cost_per_mtok=0.60,
            output_cost_per_mtok=2.50,
        )


class KimiNvidiaProvider(HarvesterProvider):
    """Kimi k2.5 via NVIDIA NIM (free tier)."""

    def __init__(self) -> None:
        super().__init__(
            name="kimi_nvidia",
            api_key_env="NVIDIA_API_KEY",
            base_url="https://integrate.api.nvidia.com/v1",
            model="moonshotai/kimi-k2-5",
            input_cost_per_mtok=0.00,
            output_cost_per_mtok=0.00,
        )

    def query(
        self,
        topic: str,
        system_prompt: str,
        max_tokens: int = 2048,
    ) -> HarvestResult:
        """Query with thinking disabled for instant mode."""
        if not self._available:
            raise RuntimeError(f"Provider {self.name} not configured")

        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": topic},
            ],
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )

        if not response.choices:
            return HarvestResult(
                provider=self.name,
                text="",
                model=self.model,
            )

        choice = response.choices[0]
        text = choice.message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        # Free tier -- cost is always 0
        return HarvestResult(
            provider=self.name,
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
        )


class GeminiProvider:
    """Gemini 2.5 Flash via google-genai SDK.

    Does NOT inherit from HarvesterProvider because it uses a different
    SDK (google-genai) instead of the OpenAI client.
    """

    def __init__(self) -> None:
        self.name = "gemini"
        self.model = "gemini-2.5-flash"
        self._api_key = os.environ.get("GEMINI_API_KEY", "")
        self._available = bool(self._api_key)
        self._client = None

    @property
    def is_available(self) -> bool:
        return self._available

    def _get_client(self):
        """Lazy-create google-genai Client on first use."""
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise RuntimeError(
                    "GeminiProvider requires 'google-genai' package. "
                    "Install with: pip install google-genai>=1.0.0"
                )

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def query(
        self,
        topic: str,
        system_prompt: str,
        max_tokens: int = 2048,
    ) -> HarvestResult:
        """Query Gemini for knowledge about a topic.

        Raises RuntimeError if provider is not configured (no API key).
        """
        if not self._available:
            raise RuntimeError(f"Provider {self.name} not configured")

        client = self._get_client()
        prompt = f"{system_prompt}\n\n{topic}"
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
        )

        text = response.text or ""

        # Try to read usage metadata; fall back to character estimation
        input_tokens = 0
        output_tokens = 0
        try:
            usage = response.usage_metadata
            if usage:
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        except Exception:
            pass

        if output_tokens == 0 and text:
            output_tokens = len(text) // 4

        # Gemini Flash is free tier
        return HarvestResult(
            provider=self.name,
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
        )
