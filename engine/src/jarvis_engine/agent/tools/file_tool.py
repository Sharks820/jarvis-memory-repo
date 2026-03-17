"""FileTool -- path-jailed file read/write tool for the agent ReAct loop.

All file operations are confined to a project_dir root. Attempts to access
paths outside the jail via traversal (../) or absolute paths raise PermissionError.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jarvis_engine.agent.tool_registry import ToolSpec


class FileTool:
    """Async file read/write tool confined to a project directory jail."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir.resolve()

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def _resolve_safe(self, path: str) -> Path:
        """Resolve *path* relative to project_dir and validate it stays within jail.

        Raises:
            PermissionError: If the resolved path is outside the project directory.
        """
        # If path is absolute, join directly; if relative, join to project_dir.
        candidate = Path(path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self._project_dir / candidate).resolve()

        # Ensure it's inside the project_dir
        try:
            resolved.relative_to(self._project_dir)
        except ValueError:
            raise PermissionError(
                f"Path {path!r} resolves to {resolved!r} which is outside the "
                f"project jail ({self._project_dir!r})."
            )
        return resolved

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def read_file(self, path: str) -> str:
        """Read a file within the project_dir jail and return its contents.

        Args:
            path: Relative or absolute path to the file. Must resolve inside project_dir.

        Returns:
            File contents as a string (UTF-8).

        Raises:
            PermissionError: If path escapes the project jail.
            FileNotFoundError: If the file does not exist.
        """
        resolved = self._resolve_safe(path)
        logger.debug("FileTool.read_file: %s", resolved)
        return resolved.read_text(encoding="utf-8")

    async def write_file(self, path: str, content: str) -> str:
        """Write *content* to a file within the project_dir jail.

        Creates parent directories as needed.

        Args:
            path: Relative or absolute path. Must resolve inside project_dir.
            content: String content to write (UTF-8).

        Returns:
            Confirmation message string.

        Raises:
            PermissionError: If path escapes the project jail.
        """
        resolved = self._resolve_safe(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.debug("FileTool.write_file: wrote %d chars to %s", len(content), resolved)
        return f"Wrote {len(content)} characters to {resolved.name}"

    # ------------------------------------------------------------------
    # ToolSpec
    # ------------------------------------------------------------------

    def get_tool_spec(self) -> "ToolSpec":
        """Return a ToolSpec for registration in the agent ToolRegistry."""
        from jarvis_engine.agent.tool_registry import ToolSpec  # lazy import

        return ToolSpec(
            name="file",
            description=(
                "Read or write files within the project directory. "
                "All paths are confined to the project root (path traversal is blocked)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write"],
                        "description": "Whether to read or write the file.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file within the project directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write (required when action=write).",
                    },
                },
                "required": ["action", "path"],
            },
            execute=self._dispatch,
            requires_approval=False,
            is_destructive=False,
        )

    async def _dispatch(self, **kwargs: Any) -> Any:
        """Dispatch to read_file or write_file based on the 'action' kwarg."""
        action = kwargs.get("action", "read")
        path = kwargs["path"]
        if action == "write":
            content = kwargs.get("content", "")
            return await self.write_file(path, content)
        return await self.read_file(path)
