"""WebTool -- web research tool wrapping the existing SSRF-safe fetch pipeline.

Delegates to jarvis_engine.web.fetch.fetch_page_text for SSRF protection,
bot-bypass tiers, and HTML-to-text extraction.  Returns content truncated to
10 000 characters to keep LLM context manageable.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jarvis_engine.agent.tool_registry import ToolSpec

_MAX_CHARS = 10_000


def fetch_page_text(url: str) -> str:  # pragma: no cover -- patched in tests
    """Lazy-loaded shim so tests can patch at the module level."""
    from jarvis_engine.web.fetch import fetch_page_text as _impl  # lazy import

    return _impl(url)


class WebTool:
    """Async URL content fetcher for agent web research."""

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, url: str, prompt: str = "") -> str:
        """Fetch *url* and return cleaned plain text (truncated to 10 000 chars).

        Delegates to the existing SSRF-safe fetch pipeline.  Returns an empty
        string if the fetch fails or the URL is deemed unsafe.

        Args:
            url: HTTP/HTTPS URL to fetch.
            prompt: Optional research context (unused by fetch; preserved for
                future search-guided fetching).

        Returns:
            Page text as a string, at most 10 000 characters.
        """
        logger.debug("WebTool.execute: fetching %s", url)
        loop = asyncio.get_running_loop()
        # Run the blocking fetch in a thread-pool so the event loop stays free.
        text: str = await loop.run_in_executor(None, fetch_page_text, url)
        truncated = text[:_MAX_CHARS]
        logger.debug(
            "WebTool.execute: %d chars fetched, %d after truncation",
            len(text), len(truncated),
        )
        return truncated

    # ------------------------------------------------------------------
    # ToolSpec
    # ------------------------------------------------------------------

    def get_tool_spec(self) -> "ToolSpec":
        """Return a ToolSpec for registration in the agent ToolRegistry."""
        from jarvis_engine.agent.tool_registry import ToolSpec  # lazy import

        return ToolSpec(
            name="web",
            description=(
                "Fetch a URL and return cleaned plain text for web research. "
                "SSRF-safe: private/local IPs are blocked. Content truncated to 10 000 chars."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP/HTTPS URL to fetch.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Optional research context or search query.",
                    },
                },
                "required": ["url"],
            },
            execute=self._dispatch,
            requires_approval=False,
            is_destructive=False,
        )

    async def _dispatch(self, **kwargs: Any) -> Any:
        """Dispatch to execute() from ToolSpec call convention."""
        url = kwargs["url"]
        prompt = kwargs.get("prompt", "")
        return await self.execute(url, prompt=prompt)
