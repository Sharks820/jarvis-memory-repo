"""BlenderTool -- headless Blender subprocess tool with script library.

Runs parameterized Blender bpy scripts as a subprocess via:
    blender --background --python <script.py> -- <args...>

Blender path is discovered from (in priority order):
  1. blender_path constructor argument
  2. BLENDER_PATH environment variable
  3. Default Windows path: C:/Program Files/Blender Foundation/Blender 4.3/blender.exe

Blender is a free, local tool so requires_approval=False.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jarvis_engine.agent.tool_registry import ToolSpec

_DEFAULT_BLENDER_PATH = (
    "C:/Program Files/Blender Foundation/Blender 4.3/blender.exe"
)

# Scripts shipped with BlenderTool
_KNOWN_SCRIPTS: frozenset[str] = frozenset(
    {"optimize_mesh", "generate_lod", "generate_geometry"}
)

# Directory containing the bpy scripts
_SCRIPTS_DIR = Path(__file__).parent / "blender_scripts"


class BlenderTool:
    """Async headless Blender subprocess tool for mesh processing and geometry generation."""

    def __init__(
        self,
        blender_path: str | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        """Initialise BlenderTool.

        Args:
            blender_path: Explicit path to Blender executable.  Falls back to
                          BLENDER_PATH env var, then the default Windows path.
            timeout_seconds: Subprocess timeout in seconds (default 120).
        """
        if blender_path:
            self._blender_path = blender_path
        elif (env_path := os.environ.get("BLENDER_PATH")):
            self._blender_path = env_path
        else:
            self._blender_path = _DEFAULT_BLENDER_PATH

        self._timeout = timeout_seconds

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        *,
        script: str,
        input_path: str = "",
        output_path: str = "",
        **extra_args: str,
    ) -> dict[str, str]:
        """Run a headless Blender script as a subprocess.

        Args:
            script: Script name (without .py extension), e.g. "optimize_mesh".
            input_path: Path to the input model file (empty for geometry creation).
            output_path: Path for the output model file.
            **extra_args: Additional key=value arguments passed to the script.

        Returns:
            Dict with keys:
            - "output_path": the output_path that was passed in.
            - "stdout": captured stdout from Blender.
            - "exit_code": subprocess return code as string.

        Raises:
            FileNotFoundError: If the Blender executable is not found.
            RuntimeError: If subprocess returns non-zero exit code (stderr included).
            asyncio.TimeoutError: If subprocess exceeds timeout_seconds.
        """
        if script not in _KNOWN_SCRIPTS:
            return {
                "ok": False,
                "error": f"Unknown script: {script!r}. Must be one of {sorted(_KNOWN_SCRIPTS)}",
            }

        script_path = _SCRIPTS_DIR / f"{script}.py"
        extra_arg_list = [f"{k}={v}" for k, v in extra_args.items()]

        cmd_args = [
            self._blender_path,
            "--background",
            "--python",
            str(script_path),
            "--",
            input_path,
            output_path,
            *extra_arg_list,
        ]

        logger.debug(
            "BlenderTool.execute: script=%s timeout=%ds", script, self._timeout
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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

        if returncode != 0:
            raise RuntimeError(
                f"Blender script {script!r} failed (exit {returncode}): {stderr}"
            )

        return {
            "output_path": output_path,
            "stdout": stdout,
            "exit_code": str(returncode),
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, **kwargs: Any) -> bool:
        """Return True only when 'script' is a known script name."""
        script = kwargs.get("script", "")
        return bool(script and script in _KNOWN_SCRIPTS)

    # ------------------------------------------------------------------
    # ToolSpec
    # ------------------------------------------------------------------

    def get_tool_spec(self) -> "ToolSpec":
        """Return a ToolSpec for registration in the agent ToolRegistry."""
        from jarvis_engine.agent.tool_registry import ToolSpec  # lazy import

        return ToolSpec(
            name="blender",
            description=(
                "Run a headless Blender script for mesh optimization, LOD generation, "
                "or procedural geometry creation. "
                f"Available scripts: {', '.join(sorted(_KNOWN_SCRIPTS))}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "enum": sorted(_KNOWN_SCRIPTS),
                        "description": "Name of the Blender script to run.",
                    },
                    "input_path": {
                        "type": "string",
                        "description": "Path to the input model file (empty for geometry creation).",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Path for the exported output model.",
                    },
                },
                "required": ["script"],
            },
            execute=self._dispatch,
            validate=self.validate,
            requires_approval=False,
            is_destructive=False,
        )

    async def _dispatch(self, **kwargs: Any) -> Any:
        """Dispatch to execute() from ToolSpec call convention."""
        script = kwargs["script"]
        input_path = kwargs.get("input_path", "")
        output_path = kwargs.get("output_path", "")
        extra = {k: v for k, v in kwargs.items() if k not in ("script", "input_path", "output_path")}
        return await self.execute(
            script=script,
            input_path=input_path,
            output_path=output_path,
            **extra,
        )
