"""Pre-compilation Unity 6.3 API validator.

Scans generated C# code for known-bad API patterns before submitting to the
Unity compiler.  Uses the KnowledgeGraph as the source of truth for what is
valid in Unity 6.3.

Catches:
  - UnityEngine.Experimental.* namespace usage (removed in Unity 6.3)
  - [SerializeField] applied to auto-properties (must be [field: SerializeField])
  - Soft warnings for class/method names not found in KG API facts
  - CS0117 / CS0619 compile error → KG-backed fix suggestion
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Matches any `using UnityEngine.Experimental.*` line
_RE_EXPERIMENTAL = re.compile(
    r"using\s+UnityEngine\.Experimental\b",
    re.MULTILINE,
)

# Matches [SerializeField] immediately followed (on the same or next line) by
# `public <type> <name> {` (auto-property syntax).
# This pattern intentionally does NOT match regular private fields.
_RE_SERIALIZE_FIELD_PROPERTY = re.compile(
    r"\[SerializeField\]\s*\n?\s*public\s+\w[\w<>, \[\]]*\s+\w+\s*\{",
    re.MULTILINE,
)

# Matches correct usage: [field: SerializeField]
_RE_FIELD_SERIALIZE_FIELD = re.compile(r"\[field\s*:\s*SerializeField\]")

# Matches `using <Namespace>;` directives to extract namespace references
_RE_USING = re.compile(r"^\s*using\s+([\w.]+)\s*;", re.MULTILINE)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of a C# code validation pass.

    Attributes:
        warnings: Human-readable warning strings.  Non-empty means the code
            likely has API issues that should be corrected.
        suggestions: Actionable fix suggestions keyed to warnings.
    """

    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class ApiValidator:
    """Validates generated C# code against KG-sourced Unity 6.3 API facts.

    Args:
        kg: KnowledgeGraph instance used for fact lookups.
    """

    def __init__(self, kg: "KnowledgeGraph") -> None:
        self._kg = kg

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def validate(self, code: str) -> ValidationResult:
        """Scan *code* for Unity 6.3 API violations.

        Args:
            code: C# source code string.

        Returns:
            ValidationResult with collected warnings and suggestions.
        """
        if not code or not code.strip():
            return ValidationResult()

        result = ValidationResult()
        self._check_experimental_namespaces(code, result)
        self._check_serialize_field_property(code, result)
        self._check_unknown_apis(code, result)
        return result

    def query_alternative(self, error_code: str, error_msg: str) -> str | None:
        """Suggest a KG-backed alternative for a CS0117/CS0619 compile error.

        Args:
            error_code: Compiler error code, e.g. "CS0117" or "CS0619".
            error_msg: Full compiler error message text.

        Returns:
            A suggestion string like "Use X instead of Y (per Unity 6.3 breaking changes)"
            or None if no KG match is found.
        """
        if not error_msg or error_code not in {"CS0117", "CS0619"}:
            return None

        # Extract the type/member name referenced in the error message.
        # Typical forms:
        #   CS0117: 'SomeNamespace.TypeName' does not contain a definition for 'MemberName'
        #   CS0619: 'SomeNamespace.TypeName' is obsolete
        token_match = re.search(r"'([^']+)'", error_msg)
        if not token_match:
            return None

        token = token_match.group(1)
        # Use the last segment of a fully-qualified name as a keyword
        keywords = [token, token.split(".")[-1]]

        # Check KG for matching api or breaking facts
        facts: list[dict] = self._kg.query_relevant_facts(
            keywords,
            min_confidence=0.4,
            limit=5,
        )
        breaking: list[dict] = self._kg.query_relevant_facts(
            keywords + ["breaking", "deprecated"],
            min_confidence=0.4,
            limit=5,
        )

        candidates = [f for f in facts if f.get("node_type") in {"unity_api", "unity_breaking"}]
        candidates += [f for f in breaking if f.get("node_type") in {"unity_api", "unity_breaking"}]

        if not candidates:
            return None

        # Prefer breaking-change facts (they contain the removal + alternative)
        breaking_candidates = [c for c in candidates if c.get("node_type") == "unity_breaking"]
        best = breaking_candidates[0] if breaking_candidates else candidates[0]
        label = best["label"]

        return f"Use alternative per Unity 6.3 breaking changes: {label}"

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _check_experimental_namespaces(self, code: str, result: ValidationResult) -> None:
        """Warn about UnityEngine.Experimental.* namespace usage."""
        matches = _RE_EXPERIMENTAL.findall(code)
        if not matches:
            return

        result.warnings.append(
            "WARNING: Code uses UnityEngine.Experimental.* namespace(s) which are removed in Unity 6.3."
        )

        # Try to get a KG-backed alternative
        breaking_facts: list[dict] = self._kg.query_relevant_facts(
            ["Experimental", "unity 6.3 breaking", "removed"],
            min_confidence=0.4,
            limit=5,
        )
        for fact in breaking_facts:
            if "experimental" in fact.get("label", "").lower() or "Experimental" in fact.get("label", ""):
                result.suggestions.append(f"KG suggestion: {fact['label']}")
                return

        # Generic fallback suggestion
        result.suggestions.append(
            "Replace UnityEngine.Experimental.* with the non-experimental equivalents"
            " (e.g., UnityEngine.Rendering)."
        )

    def _check_serialize_field_property(self, code: str, result: ValidationResult) -> None:
        """Warn when [SerializeField] is used on an auto-property instead of a field."""
        if not _RE_SERIALIZE_FIELD_PROPERTY.search(code):
            return

        result.warnings.append(
            "WARNING: [SerializeField] on an auto-property is incorrect in Unity 6.3."
        )
        result.suggestions.append(
            "Use [field: SerializeField] instead of [SerializeField] on auto-properties."
            " Example: `[field: SerializeField] public float Speed { get; private set; }`"
        )

    def _check_unknown_apis(self, code: str, result: ValidationResult) -> None:
        """Soft-warn about namespace imports not found in KG API facts."""
        using_namespaces = _RE_USING.findall(code)
        if not using_namespaces:
            return

        # Only check non-standard (non-UnityEngine, non-System) namespaces
        known_prefixes = {"UnityEngine", "System", "Unity", "UnityEditor"}
        unknown = [
            ns for ns in using_namespaces
            if not any(ns.startswith(p) for p in known_prefixes)
        ]
        if not unknown:
            return

        for ns in unknown:
            keywords = [ns, ns.split(".")[-1]]
            facts: list[dict] = self._kg.query_relevant_facts(keywords, min_confidence=0.4, limit=3)
            unity_facts = [f for f in facts if f.get("node_type") in {"unity_api", "unity_breaking"}]
            if not unity_facts:
                result.warnings.append(
                    f"WARNING: Namespace '{ns}' not found in KG Unity 6.3 API facts"
                    " -- possibly incorrect for Unity 6.3."
                )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def validate_csharp_against_kg(code: str, kg: "KnowledgeGraph") -> ValidationResult:
    """Convenience wrapper: validate C# code against the KG.

    Args:
        code: C# source code.
        kg: KnowledgeGraph instance.

    Returns:
        ValidationResult with warnings and suggestions.
    """
    return ApiValidator(kg).validate(code)
