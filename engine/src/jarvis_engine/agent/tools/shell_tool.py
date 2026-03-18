"""ShellTool -- sandboxed subprocess tool with timeout and blocklist.

Executes shell commands with a configurable timeout, rejecting commands that
match a blocklist of dangerous patterns (rm -rf /, format, etc.).
Requires human approval before execution (is_destructive=True).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jarvis_engine.agent.tool_registry import ToolSpec

_DEFAULT_BLOCKED: list[str] = [
    "rm -rf /",
    "format ",
    "del /s",
    "rmdir /s",
    ":(){",
    "mkfs",
]


class ShellTool:
    """Async shell command executor with timeout and blocklist enforcement."""

    def __init__(
        self,
        timeout: int = 30,
        blocked_patterns: list[str] | None = None,
    ) -> None:
        self._timeout = timeout
        self._blocked = blocked_patterns if blocked_patterns is not None else list(_DEFAULT_BLOCKED)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _check_blocklist(self, command: str) -> None:
        """Raise PermissionError if *command* matches any blocked pattern.

        Normalises whitespace and checks case-insensitively.  Also rejects
        command substitution via ``$( )`` and backticks.
        """
        # Normalize whitespace so "rm  -rf  /" still matches "rm -rf /"
        normalized = re.sub(r"\s+", " ", command.strip().lower())

        for pattern in self._blocked:
            if pattern.lower() in normalized:
                raise PermissionError(
                    f"Command blocked by policy: pattern {pattern!r} matched in {command!r}"
                )

        # Reject command substitution attempts that could smuggle blocked commands
        if "$(" in normalized or "`" in normalized:
            raise PermissionError(
                f"Command blocked by policy: command substitution ($() or backticks) "
                f"is not permitted in {command!r}"
            )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Execute *command* as a shell subprocess.

        Args:
            command: Shell command string to execute.
            cwd: Optional working directory for the subprocess.

        Returns:
            Dict with keys: "stdout" (str), "stderr" (str), "returncode" (int).

        Raises:
            PermissionError: If command matches the blocklist.
            asyncio.TimeoutError: If command exceeds the configured timeout.
        """
        self._check_blocklist(command)
        logger.debug("ShellTool.execute: %r (timeout=%ds)", command, self._timeout)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(self._timeout)
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        returncode = proc.returncode if proc.returncode is not None else -1

        logger.debug(
            "ShellTool.execute: returncode=%d stdout=%d chars stderr=%d chars",
            returncode, len(stdout), len(stderr),
        )
        return {"stdout": stdout, "stderr": stderr, "returncode": returncode}

    # ------------------------------------------------------------------
    # ToolSpec
    # ------------------------------------------------------------------

    def get_tool_spec(self) -> "ToolSpec":
        """Return a ToolSpec for registration in the agent ToolRegistry."""
        from jarvis_engine.agent.tool_registry import ToolSpec  # lazy import

        return ToolSpec(
            name="shell",
            description=(
                "Execute a shell command with a configurable timeout. "
                "Destructive commands (rm -rf /, format, etc.) are blocked. "
                "Always requires human approval before execution."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory for the subprocess.",
                    },
                },
                "required": ["command"],
            },
            execute=self._dispatch,
            requires_approval=True,
            is_destructive=True,
        )

    async def _dispatch(self, **kwargs: Any) -> Any:
        """Dispatch to execute() from ToolSpec call convention."""
        command = kwargs["command"]
        cwd = kwargs.get("cwd")
        return await self.execute(command, cwd=cwd)
