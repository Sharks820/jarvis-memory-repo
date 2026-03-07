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
        credential_env_var: str,
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

        self._credential_env_var = credential_env_var
        self._api_key = os.environ.get(credential_env_var, "")
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

            self._client = OpenAI(api_key=self._api_key, base_url=self.base_url, timeout=60.0)
        return self._client

    def query(
        self,
        topic: str,
        system_prompt: str,
        max_tokens: int = 2048,
        *,
        extra_body: dict | None = None,
        override_cost_usd: float | None = None,
    ) -> HarvestResult:
        """Query the provider for knowledge about a topic.

        Raises RuntimeError if provider is not configured (no API key).

        Args:
            extra_body: Optional extra parameters forwarded to the API call.
            override_cost_usd: If set, use this value instead of computing cost
                from token counts (useful for free-tier providers).
        """
        if not self._available:
            raise RuntimeError(f"Provider {self.name} not configured")

        client = self._get_client()
        create_kwargs: dict = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": topic},
            ],
            max_tokens=max_tokens,
        )
        if extra_body is not None:
            create_kwargs["extra_body"] = extra_body

        response = client.chat.completions.create(**create_kwargs)

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

        if override_cost_usd is not None:
            cost = override_cost_usd
        else:
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
            credential_env_var="MINIMAX_API_KEY",
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
            credential_env_var="KIMI_API_KEY",
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
            credential_env_var="NVIDIA_API_KEY",
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
        **kwargs,
    ) -> HarvestResult:
        """Query with thinking disabled for instant mode (free tier)."""
        return super().query(
            topic,
            system_prompt,
            max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
            override_cost_usd=0.0,
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

            self._client = genai.Client(
                api_key=self._api_key,
                http_options={"timeout": 60_000},  # 60s in milliseconds
            )
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
        try:
            from google.genai import types as genai_types
            gen_config = genai_types.GenerateContentConfig(
                max_output_tokens=max_tokens,
            )
        except (ImportError, AttributeError):
            gen_config = None
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            **({"config": gen_config} if gen_config else {}),
        )

        # response.text can raise ValueError if response has no text parts
        try:
            text = response.text or ""
        except (ValueError, AttributeError):
            text = ""

        # Try to read usage metadata; fall back to character estimation
        input_tokens = 0
        output_tokens = 0
        try:
            usage = response.usage_metadata
            if usage:
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        except (AttributeError, TypeError) as exc:
            logger.debug("Failed to extract Gemini usage metadata: %s", exc)

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
