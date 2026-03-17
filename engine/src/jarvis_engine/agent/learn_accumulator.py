"""Learn-as-you-go knowledge accumulator for the Unity agent.

Stores successful code patterns and error-fix pairs in the KnowledgeGraph
after compile-fix cycles complete, and queries accumulated knowledge before
new code generation tasks to reduce repeat mistakes.

Usage::

    from jarvis_engine.agent.learn_accumulator import LearnAccumulator

    acc = LearnAccumulator(kg)
    acc.save_pattern(script_path, code, description)
    acc.save_error_fix(error_msg, fix_desc, code_before, code_after)
    patterns = acc.query_patterns(task_description, limit=5)
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph

__all__ = ["LearnAccumulator"]

logger = logging.getLogger(__name__)

# Max chars stored in KG labels to avoid bloating the fact store
_SNIPPET_MAX = 500
_ERROR_MAX = 200
_CODE_BEFORE_AFTER_MAX = 200

# Node types this accumulator produces and consumes
_PATTERN_TYPES = frozenset({"code_pattern", "error_fix"})


def _md5_hex(text: str) -> str:
    """Return a hex MD5 digest (not used for security -- only for dedup IDs)."""
    return hashlib.md5(  # noqa: S324
        text.encode("utf-8", errors="replace"),
        usedforsecurity=False,
    ).hexdigest()


class LearnAccumulator:
    """Accumulates and retrieves learned patterns from the KnowledgeGraph.

    After successful compile-fix cycles, call :meth:`save_pattern` and/or
    :meth:`save_error_fix` to persist what worked.  Before generating new code
    for a task, call :meth:`query_patterns` to retrieve relevant context.

    Args:
        kg: A :class:`~jarvis_engine.knowledge.graph.KnowledgeGraph` instance.
    """

    def __init__(self, kg: "KnowledgeGraph") -> None:
        self._kg = kg

    # ------------------------------------------------------------------
    # Write side
    # ------------------------------------------------------------------

    def save_pattern(
        self,
        script_path: str,
        code_snippet: str,
        description: str,
    ) -> bool:
        """Store a working code pattern as a KG fact.

        Args:
            script_path: Relative path of the C# script (used as source_record).
            code_snippet: The working C# code (truncated to 500 chars in label).
            description: Human-readable description of what the pattern does.

        Returns:
            True if the fact was stored successfully, False if KG blocked it
            (e.g. locked node).
        """
        node_id = f"pattern:{_md5_hex(script_path + code_snippet)[:12]}"
        truncated = code_snippet[:_SNIPPET_MAX]
        label = f"[Code Pattern] {description}\n---\n{truncated}"

        return self._kg.add_fact(
            node_id,
            label,
            0.7,
            source_record=script_path,
            node_type="code_pattern",
        )

    def save_error_fix(
        self,
        error_message: str,
        fix_description: str,
        code_before: str,
        code_after: str,
    ) -> bool:
        """Store an error-fix pair as a KG fact.

        Args:
            error_message: The compile/test error that was fixed.
            fix_description: Description of how the error was resolved.
            code_before: The broken code snippet (truncated to 200 chars).
            code_after: The fixed code snippet (truncated to 200 chars).

        Returns:
            True if the fact was stored successfully, False otherwise.
        """
        node_id = f"errfix:{_md5_hex(error_message + fix_description)[:12]}"
        err_trunc = error_message[:_ERROR_MAX]
        before_trunc = code_before[:_CODE_BEFORE_AFTER_MAX]
        after_trunc = code_after[:_CODE_BEFORE_AFTER_MAX]
        label = (
            f"[Error Fix] {err_trunc} -> {fix_description}\n"
            f"---\n"
            f"Before: {before_trunc}\n"
            f"After: {after_trunc}"
        )

        return self._kg.add_fact(
            node_id,
            label,
            0.8,
            source_record="compile_fix_loop",
            node_type="error_fix",
        )

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    def query_patterns(
        self,
        task_description: str,
        limit: int = 5,
    ) -> list[str]:
        """Query accumulated patterns relevant to a new task.

        Retrieves code_pattern and error_fix facts from the KnowledgeGraph
        using keyword search on the task description.  Returns label strings
        suitable for injecting into an LLM system prompt.

        Args:
            task_description: The current code generation task description.
            limit: Maximum number of pattern strings to return.

        Returns:
            List of label strings (may be empty if no patterns accumulated yet).
        """
        if not task_description or not task_description.strip():
            return []

        # Build keywords from task description words (length > 2)
        keywords = [w for w in task_description.split() if len(w) > 2]
        if not keywords:
            keywords = [task_description.strip()]

        try:
            # Query up to limit*2 to allow filtering by node_type
            results: list[dict] = self._kg.query_relevant_facts(
                keywords,
                min_confidence=0.5,
                limit=limit * 2,
            )
        except Exception:  # noqa: BLE001
            logger.warning("LearnAccumulator: query_relevant_facts failed", exc_info=True)
            return []

        # Filter to only pattern-type nodes
        pattern_results = [r for r in results if r.get("node_type") in _PATTERN_TYPES]

        # Return label strings up to limit
        return [r["label"] for r in pattern_results[:limit] if r.get("label")]
