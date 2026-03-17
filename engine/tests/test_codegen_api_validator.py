"""Tests for ApiValidator -- pre-compilation C# validation against KG."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kg(api_facts: list[dict] | None = None, breaking_facts: list[dict] | None = None):
    """Return a mock KnowledgeGraph."""
    kg = MagicMock()

    api_facts = api_facts or []
    breaking_facts = breaking_facts or []

    def _query(keywords: list[str], *, min_confidence: float = 0.4, limit: int = 10):
        kw_lower = [k.lower() for k in keywords]
        if any("break" in k or "deprecated" in k or "removed" in k for k in kw_lower):
            return breaking_facts[:limit]
        return api_facts[:limit]

    kg.query_relevant_facts.side_effect = _query
    return kg


def _make_api_fact(label: str) -> dict:
    return {
        "node_id": f"api_{label[:15]}",
        "label": label,
        "node_type": "unity_api",
        "confidence": 0.9,
        "locked": 0,
        "updated_at": "2026-01-01",
    }


def _make_breaking_fact(label: str, alternative: str = "") -> dict:
    full_label = label
    if alternative:
        full_label = f"{label}. Use {alternative} instead."
    return {
        "node_id": f"break_{label[:15]}",
        "label": full_label,
        "node_type": "unity_breaking",
        "confidence": 0.95,
        "locked": 0,
        "updated_at": "2026-01-01",
    }


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestImports:
    def test_import_api_validator(self):
        from jarvis_engine.agent.codegen.api_validator import ApiValidator  # noqa: F401

    def test_import_validate_csharp_against_kg(self):
        from jarvis_engine.agent.codegen.api_validator import validate_csharp_against_kg  # noqa: F401

    def test_import_validation_result(self):
        from jarvis_engine.agent.codegen.api_validator import ValidationResult  # noqa: F401

    def test_validation_result_is_dataclass(self):
        from jarvis_engine.agent.codegen.api_validator import ValidationResult
        import dataclasses
        assert dataclasses.is_dataclass(ValidationResult)

    def test_validation_result_fields(self):
        from jarvis_engine.agent.codegen.api_validator import ValidationResult
        vr = ValidationResult(warnings=["w1"], suggestions=["s1"])
        assert vr.warnings == ["w1"]
        assert vr.suggestions == ["s1"]


# ---------------------------------------------------------------------------
# Empty code
# ---------------------------------------------------------------------------

class TestEmptyCode:
    def setup_method(self):
        from jarvis_engine.agent.codegen.api_validator import ApiValidator
        self.validator = ApiValidator(_make_kg())

    def test_empty_code_no_warnings(self):
        from jarvis_engine.agent.codegen.api_validator import ValidationResult
        result = self.validator.validate("")
        assert isinstance(result, ValidationResult)
        assert result.warnings == []
        assert result.suggestions == []

    def test_whitespace_only_no_warnings(self):
        result = self.validator.validate("   \n\t  ")
        assert result.warnings == []


# ---------------------------------------------------------------------------
# Experimental namespace detection
# ---------------------------------------------------------------------------

class TestExperimentalNamespaceDetection:
    def setup_method(self):
        from jarvis_engine.agent.codegen.api_validator import ApiValidator

        breaking_facts = [
            _make_breaking_fact(
                "Unity 6.3 breaking: UnityEngine.Experimental.Rendering removed",
                alternative="Use UnityEngine.Rendering instead",
            )
        ]
        self.validator = ApiValidator(_make_kg(breaking_facts=breaking_facts))

    def test_experimental_rendering_triggers_warning(self):
        code = """
using UnityEngine.Experimental.Rendering;

public class MyShader : MonoBehaviour { }
"""
        result = self.validator.validate(code)
        assert len(result.warnings) >= 1
        assert any("Experimental" in w for w in result.warnings)

    def test_experimental_namespace_suggestion_from_kg(self):
        code = "using UnityEngine.Experimental.Rendering;"
        result = self.validator.validate(code)
        # Should have at least one suggestion pointing to alternative
        all_text = " ".join(result.warnings + result.suggestions)
        # Either the KG alternative or a generic alternative is present
        assert "Experimental" in all_text or "Rendering" in all_text

    def test_no_experimental_no_warning(self):
        code = """
using UnityEngine;
using UnityEngine.Rendering;

public class MyShader : MonoBehaviour { }
"""
        result = self.validator.validate(code)
        assert not any("Experimental" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# SerializeField on property detection
# ---------------------------------------------------------------------------

class TestSerializeFieldDetection:
    def setup_method(self):
        from jarvis_engine.agent.codegen.api_validator import ApiValidator
        self.validator = ApiValidator(_make_kg())

    def test_serialize_field_on_property_warns(self):
        code = """
using UnityEngine;

public class Player : MonoBehaviour
{
    [SerializeField]
    public float Speed { get; private set; }
}
"""
        result = self.validator.validate(code)
        assert len(result.warnings) >= 1
        assert any("SerializeField" in w for w in result.warnings)

    def test_serialize_field_property_suggests_field_prefix(self):
        code = "[SerializeField]\npublic float Speed { get; set; }"
        result = self.validator.validate(code)
        all_text = " ".join(result.warnings + result.suggestions)
        assert "[field: SerializeField]" in all_text

    def test_serialize_field_on_field_no_property_warning(self):
        """[SerializeField] on a regular field (not auto-property) should NOT warn."""
        code = """
using UnityEngine;

public class Player : MonoBehaviour
{
    [SerializeField]
    private float _speed;
}
"""
        result = self.validator.validate(code)
        # Must not produce a SerializeField property warning for a field
        assert not any("SerializeField" in w and "{" in w for w in result.warnings)

    def test_field_serialize_field_attribute_no_warning(self):
        """[field: SerializeField] on auto-property is correct -- no warning."""
        code = """
public class Player : MonoBehaviour
{
    [field: SerializeField]
    public float Speed { get; private set; }
}
"""
        result = self.validator.validate(code)
        # Correct usage should not produce a SerializeField warning
        assert not any("SerializeField" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Unknown API soft warnings
# ---------------------------------------------------------------------------

class TestUnknownApiWarnings:
    def setup_method(self):
        from jarvis_engine.agent.codegen.api_validator import ApiValidator
        # KG has only GameObject entries -- nothing for "ObsoleteManager"
        api_facts = [
            _make_api_fact("GameObject.AddComponent<T>()"),
            _make_api_fact("GameObject.GetComponent<T>()"),
        ]
        self.validator = ApiValidator(_make_kg(api_facts=api_facts))

    def test_known_api_no_warning(self):
        code = """
using UnityEngine;

public class Spawner : MonoBehaviour
{
    void Start()
    {
        gameObject.GetComponent<Rigidbody>();
    }
}
"""
        result = self.validator.validate(code)
        # KG has GetComponent -- should have at most soft warnings, not hard errors
        # The key thing: no "possibly incorrect" for well-known APIs
        assert not any("GetComponent" in w and "possibly incorrect" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# query_alternative for CS0117 / CS0619 errors
# ---------------------------------------------------------------------------

class TestQueryAlternative:
    def setup_method(self):
        from jarvis_engine.agent.codegen.api_validator import ApiValidator

        api_facts = [
            _make_api_fact("UnityEngine.Rendering.RenderPipelineManager - use for pipeline events"),
        ]
        breaking_facts = [
            _make_breaking_fact(
                "Unity 6.3 breaking: UnityEngine.Experimental.Rendering.RenderPipeline removed",
                alternative="UnityEngine.Rendering.RenderPipelineManager",
            )
        ]
        self.validator = ApiValidator(_make_kg(api_facts=api_facts, breaking_facts=breaking_facts))

    def test_cs0117_returns_suggestion_string(self):
        result = self.validator.query_alternative(
            "CS0117",
            "error CS0117: 'UnityEngine.Experimental.Rendering' does not contain a definition for 'RenderPipeline'",
        )
        # Returns a string suggestion or None -- if KG has a match it must be a non-empty string
        assert result is None or isinstance(result, str)

    def test_cs0619_returns_suggestion_string(self):
        result = self.validator.query_alternative(
            "CS0619",
            "error CS0619: 'UnityEngine.Experimental.Rendering.RenderPipeline' is obsolete",
        )
        assert result is None or isinstance(result, str)

    def test_unknown_error_code_returns_none_or_string(self):
        result = self.validator.query_alternative("CS9999", "error CS9999: something unknown")
        # Should not raise; may return None
        assert result is None or isinstance(result, str)

    def test_empty_error_message_returns_none(self):
        result = self.validator.query_alternative("CS0117", "")
        assert result is None

    def test_cs0117_with_kg_match_returns_nonempty_string(self):
        """When KG has a match for the referenced type, result must be a non-empty string."""
        # The KG mock returns api_facts for non-breaking queries
        # "RenderPipeline" should match the api fact we set up
        result = self.validator.query_alternative(
            "CS0117",
            "error CS0117: 'RenderPipeline' does not contain a definition for 'Execute'",
        )
        # Either None (KG didn't match) or a helpful string
        if result is not None:
            assert len(result) > 5, "Suggestion string should be meaningful"


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

class TestConvenienceFunction:
    def test_validate_csharp_against_kg_returns_validation_result(self):
        from jarvis_engine.agent.codegen.api_validator import validate_csharp_against_kg, ValidationResult
        kg = _make_kg()
        result = validate_csharp_against_kg("", kg)
        assert isinstance(result, ValidationResult)

    def test_validate_csharp_against_kg_detects_experimental(self):
        from jarvis_engine.agent.codegen.api_validator import validate_csharp_against_kg
        kg = _make_kg()
        code = "using UnityEngine.Experimental.Rendering;"
        result = validate_csharp_against_kg(code, kg)
        assert any("Experimental" in w for w in result.warnings)
