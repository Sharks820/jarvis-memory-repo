"""TripoTool -- tripo3d SDK wrapper with approval gate.

Generates 3D models from text prompts or images using the tripo3d API.
Every API call costs credits, so requires_approval=True and estimate_cost > 0
ensures the ApprovalGate blocks before any network call is made.

The tripo3d SDK is imported lazily inside execute() to avoid import errors when
the SDK is not installed in environments that don't use this tool.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jarvis_engine.agent.tool_registry import ToolSpec

# Expose TripoClient at module level for patching in tests.
# This lazy import is wrapped in a try/except so the module loads cleanly
# even when tripo3d is not installed.
try:
    from tripo3d import TripoClient  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    TripoClient = None  # type: ignore[assignment,misc]


class TripoTool:
    """Async 3D model generation tool powered by the tripo3d API."""

    def __init__(
        self,
        output_dir: Path,
        api_key: str | None = None,
    ) -> None:
        """Initialise TripoTool.

        Args:
            output_dir: Directory where downloaded model files will be saved.
            api_key: tripo3d API key.  Falls back to os.environ["TRIPO_API_KEY"]
                     at execute() time when *api_key* is None.
        """
        self._output_dir = output_dir
        self._api_key = api_key

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        *,
        prompt: str,
        format: str = "fbx",
        image_path: str | None = None,
    ) -> dict[str, str]:
        """Generate a 3D model via the tripo3d API.

        Args:
            prompt: Text description of the model to generate.
            format: Output format -- "fbx" (default), "glb", or "obj".
            image_path: Optional path to a reference image; if provided, calls
                        image_to_model() instead of text_to_model().

        Returns:
            Dict with keys:
            - "model_path": path to the downloaded model file.
            - "format": the format that was used.
            - "task_id": tripo3d task identifier.

        Raises:
            ValueError: If TRIPO_API_KEY is missing and no api_key was given.
            RuntimeError: If the tripo3d SDK raises any error during generation.
        """
        api_key = self._api_key or os.environ.get("TRIPO_API_KEY", "")
        if not api_key:
            raise ValueError(
                "TRIPO_API_KEY environment variable is not set and no api_key was "
                "provided to TripoTool."
            )

        client_cls = TripoClient
        if client_cls is None:  # pragma: no cover
            raise RuntimeError(
                "tripo3d package is not installed. Run: pip install tripo3d"
            )

        try:
            async with client_cls(api_key=api_key) as client:
                if image_path:
                    task = await client.image_to_model(image_path)
                else:
                    task = await client.text_to_model(prompt)

                result = await client.wait_for_task(task.task_id)

                self._output_dir.mkdir(parents=True, exist_ok=True)
                downloaded = await client.download_task_models(
                    result, output_dir=str(self._output_dir)
                )

        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"tripo3d SDK error: {exc}"
            ) from exc

        model_path = downloaded[0] if downloaded else str(self._output_dir / f"model.{format}")

        return {
            "model_path": str(model_path),
            "format": format,
            "task_id": result.task_id,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, **kwargs: Any) -> bool:
        """Return True only when 'prompt' is a non-empty string."""
        prompt = kwargs.get("prompt", "")
        return bool(prompt and isinstance(prompt, str))

    # ------------------------------------------------------------------
    # ToolSpec
    # ------------------------------------------------------------------

    def get_tool_spec(self) -> "ToolSpec":
        """Return a ToolSpec for registration in the agent ToolRegistry."""
        from jarvis_engine.agent.tool_registry import ToolSpec  # lazy import

        return ToolSpec(
            name="tripo",
            description=(
                "Generate a 3D model from a text description or reference image using "
                "the tripo3d AI API.  Output formats: fbx (default), glb, obj. "
                "IMPORTANT: Each call consumes tripo3d credits -- approval required."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the 3D model to generate.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["fbx", "glb", "obj"],
                        "description": "Output file format (default: fbx).",
                    },
                    "image_path": {
                        "type": "string",
                        "description": "Optional path to a reference image for image-to-model.",
                    },
                },
                "required": ["prompt"],
            },
            execute=self._dispatch,
            validate=self.validate,
            estimate_cost=lambda **_kw: 1.0,  # nonzero → ApprovalGate triggers
            requires_approval=True,
            is_destructive=False,
        )

    async def _dispatch(self, **kwargs: Any) -> Any:
        """Dispatch to execute() from ToolSpec call convention."""
        prompt = kwargs["prompt"]
        fmt = kwargs.get("format", "fbx")
        image_path = kwargs.get("image_path")
        return await self.execute(prompt=prompt, format=fmt, image_path=image_path)
