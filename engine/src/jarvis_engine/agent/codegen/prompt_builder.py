"""Unity 6.3 domain-specific system prompt builder with KG fact injection.

Queries the KnowledgeGraph for Unity 6.3 API facts and breaking change warnings,
then assembles an LLM system prompt that steers code generation away from
hallucinated legacy APIs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.agent.learn_accumulator import LearnAccumulator
    from jarvis_engine.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Baseline rules (always included, no KG required)
# ---------------------------------------------------------------------------

_BASELINE_RULES = """\
## Baseline Unity 6.3 Rules (always apply)

1. Use `[field: SerializeField]` attribute syntax on auto-properties, NOT bare `[SerializeField]`.
   Example: `[field: SerializeField] public float Speed { get; private set; }`
2. Do NOT use `UnityEngine.Experimental.*` namespaces -- they are removed in Unity 6.3.
3. Do NOT use URP Compatibility Mode render graph calls (deprecated in Unity 6.3).
4. Always include `using UnityEngine;` at the top of every script, plus any other required namespaces.
5. Always inherit from MonoBehaviour and include appropriate lifecycle methods (Awake, Start, Update) where relevant.
6. Write generated scripts to the `Assets/JarvisGenerated/` folder hierarchy.
"""

_ROLE_HEADER = (
    "You are a Unity 6.3 C# code generator. Your job is to produce correct, compilable"
    " C# scripts for Unity 6.3 projects. You must use only Unity 6.3-compatible APIs."
    " Do NOT use APIs from Unity 2019-2022 that were removed or deprecated in Unity 6.x."
)


class UnityPromptBuilder:
    """Builds LLM system prompts for Unity 6.3 C# code generation.

    Queries the KnowledgeGraph for API reference facts and breaking change
    warnings, then assembles a structured system prompt.

    Args:
        kg: A KnowledgeGraph instance used to retrieve Unity 6.3 facts.
    """

    def __init__(
        self,
        kg: "KnowledgeGraph",
        accumulator: "LearnAccumulator | None" = None,
    ) -> None:
        self._kg = kg
        self._accumulator = accumulator

    def build_unity_system_prompt(
        self,
        task_context: str,
        extra_context: str = "",
    ) -> str:
        """Assemble a Unity 6.3 system prompt with KG-sourced knowledge.

        Args:
            task_context: Short description of the generation task (e.g. "rotating cube").
                Used to fetch task-relevant API facts.
            extra_context: Optional additional instructions appended at the end.

        Returns:
            A multi-section system prompt string.
        """
        # Extract keywords from task context for targeted API lookup
        task_keywords = [w for w in task_context.split() if len(w) > 2]

        # --- Query KG for API facts ---
        api_keywords = ["unity 6.3", "MonoBehaviour"] + task_keywords
        api_facts: list[dict] = self._kg.query_relevant_facts(
            api_keywords,
            min_confidence=0.5,
            limit=15,
        )
        unity_api_facts = [f for f in api_facts if f.get("node_type") == "unity_api"]

        # --- Query KG for breaking change warnings ---
        breaking_keywords = ["unity 6.3 breaking", "deprecated", "removed"]
        breaking_facts: list[dict] = self._kg.query_relevant_facts(
            breaking_keywords,
            min_confidence=0.5,
            limit=10,
        )
        unity_breaking_facts = [f for f in breaking_facts if f.get("node_type") == "unity_breaking"]

        # --- Assemble prompt sections ---
        parts: list[str] = [_ROLE_HEADER, ""]

        # API constraints section
        if unity_api_facts:
            parts.append("## Unity 6.3 API Reference (use these correct signatures)")
            for fact in unity_api_facts:
                parts.append(f"- {fact['label']}")
            parts.append("")

        # Breaking changes section
        if unity_breaking_facts:
            parts.append("## Unity 6.3 Breaking Changes and Warnings")
            for fact in unity_breaking_facts:
                parts.append(f"- {fact['label']}")
            parts.append("")

        # Baseline rules (always present)
        parts.append(_BASELINE_RULES)

        # Learned patterns from accumulator (injected before extra context)
        if self._accumulator is not None:
            try:
                patterns = self._accumulator.query_patterns(task_context, limit=5)
                if patterns:
                    parts.append("## Learned Patterns")
                    for pattern in patterns:
                        parts.append(f"- {pattern}")
                    parts.append("")
            except Exception:  # noqa: BLE001
                logger.warning(
                    "UnityPromptBuilder: accumulator query failed (non-fatal)", exc_info=True
                )

        # Optional extra context
        if extra_context:
            parts.append("## Additional Instructions")
            parts.append(extra_context)
            parts.append("")

        return "\n".join(parts)


def build_unity_system_prompt(
    kg: "KnowledgeGraph",
    task_context: str,
    extra_context: str = "",
) -> str:
    """Module-level convenience wrapper for UnityPromptBuilder.

    Args:
        kg: KnowledgeGraph instance.
        task_context: Short description of the generation task.
        extra_context: Optional additional instructions.

    Returns:
        Assembled system prompt string.
    """
    return UnityPromptBuilder(kg).build_unity_system_prompt(task_context, extra_context)
