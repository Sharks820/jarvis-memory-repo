"""Tests for UnityPromptBuilder -- Unity 6.3 KG-seeded system prompt construction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kg(api_facts: list[dict] | None = None, breaking_facts: list[dict] | None = None):
    """Return a mock KnowledgeGraph with query_relevant_facts wired to fake data."""
    kg = MagicMock()

    api_facts = api_facts or []
    breaking_facts = breaking_facts or []

    def _query_relevant_facts(keywords: list[str], *, min_confidence: float = 0.4, limit: int = 10):
        # Return breaking facts when keywords suggest breaking changes, else api facts
        kw_lower = [k.lower() for k in keywords]
        if any("break" in k or "deprecated" in k or "removed" in k for k in kw_lower):
            return breaking_facts[:limit]
        return api_facts[:limit]

    kg.query_relevant_facts.side_effect = _query_relevant_facts
    return kg


def _make_api_fact(label: str) -> dict:
    return {
        "node_id": f"api_{label[:10]}",
        "label": label,
        "node_type": "unity_api",
        "confidence": 0.9,
        "locked": 0,
        "updated_at": "2026-01-01",
    }


def _make_breaking_fact(label: str) -> dict:
    return {
        "node_id": f"break_{label[:10]}",
        "label": label,
        "node_type": "unity_breaking",
        "confidence": 0.95,
        "locked": 0,
        "updated_at": "2026-01-01",
    }


# ---------------------------------------------------------------------------
# Import tests (ensure module structure is correct)
# ---------------------------------------------------------------------------

class TestImports:
    def test_import_unity_prompt_builder(self):
        from jarvis_engine.agent.codegen.prompt_builder import UnityPromptBuilder  # noqa: F401

    def test_import_build_unity_system_prompt_function(self):
        from jarvis_engine.agent.codegen.prompt_builder import build_unity_system_prompt  # noqa: F401

    def test_codegen_package_init(self):
        import jarvis_engine.agent.codegen  # noqa: F401


# ---------------------------------------------------------------------------
# Baseline tests (no KG data)
# ---------------------------------------------------------------------------

class TestBaselinePrompt:
    def setup_method(self):
        from jarvis_engine.agent.codegen.prompt_builder import UnityPromptBuilder
        self.builder = UnityPromptBuilder(_make_kg())

    def test_returns_string(self):
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert isinstance(result, str)

    def test_nonempty_prompt(self):
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert len(result) > 100

    def test_role_description_present(self):
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert "Unity 6.3" in result

    def test_monobehaviour_guidance(self):
        """Prompt must include MonoBehaviour lifecycle guidance."""
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert "MonoBehaviour" in result

    def test_serialize_field_rule_present(self):
        """[field: SerializeField] syntax must be mentioned even without KG."""
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert "[field: SerializeField]" in result

    def test_no_experimental_namespace_warning(self):
        """Experimental namespace warning must always be present."""
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert "UnityEngine.Experimental" in result

    def test_urp_compatibility_mode_warning(self):
        """URP Compatibility Mode warning must be present."""
        result = self.builder.build_unity_system_prompt("rotating cube")
        lower = result.lower()
        assert "compatibility mode" in lower or "urp" in lower or "render graph" in lower

    def test_using_unity_engine_rule(self):
        """'using UnityEngine;' rule must appear."""
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert "using UnityEngine" in result

    def test_jarvis_generated_path_rule(self):
        """Assets/JarvisGenerated/ path must be mentioned."""
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert "JarvisGenerated" in result


# ---------------------------------------------------------------------------
# KG-fact injection tests
# ---------------------------------------------------------------------------

class TestKGInjection:
    def setup_method(self):
        from jarvis_engine.agent.codegen.prompt_builder import UnityPromptBuilder

        api_facts = [
            _make_api_fact("GameObject.AddComponent<T>() - Adds and returns a component of type T"),
            _make_api_fact("Transform.Rotate(Vector3) - Rotates the object by euler angles"),
            _make_api_fact("Rigidbody.AddForce(Vector3) - Applies a force to the rigidbody"),
        ]
        breaking_facts = [
            _make_breaking_fact(
                "Unity 6.3 breaking: [SerializeField] on auto-properties must use [field: SerializeField]"
            ),
        ]
        self.kg = _make_kg(api_facts=api_facts, breaking_facts=breaking_facts)
        self.builder = UnityPromptBuilder(self.kg)

    def test_includes_api_facts_in_prompt(self):
        result = self.builder.build_unity_system_prompt("rotating cube")
        # At least 3 api facts must appear
        count = sum(
            1 for label in [
                "GameObject.AddComponent",
                "Transform.Rotate",
                "Rigidbody.AddForce",
            ]
            if label in result
        )
        assert count >= 3, f"Expected >= 3 API facts in prompt, found {count}. Prompt:\n{result[:500]}"

    def test_includes_breaking_change_warning(self):
        result = self.builder.build_unity_system_prompt("rotating cube")
        assert "breaking" in result.lower() or "[field: SerializeField]" in result

    def test_kg_queried_for_api_facts(self):
        self.builder.build_unity_system_prompt("rotating cube")
        calls = self.kg.query_relevant_facts.call_args_list
        api_call_keywords = []
        for call in calls:
            kw_list = call.args[0] if call.args else call.kwargs.get("keywords", [])
            api_call_keywords.extend(kw_list)
        # At minimum "unity 6.3" or "MonoBehaviour" must appear in the API query keywords
        lower_kws = [k.lower() for k in api_call_keywords]
        assert any("unity" in k for k in lower_kws), f"KG not queried with unity keywords: {api_call_keywords}"

    def test_kg_queried_for_breaking_changes(self):
        self.builder.build_unity_system_prompt("rotating cube")
        calls = self.kg.query_relevant_facts.call_args_list
        all_keywords = []
        for call in calls:
            kw_list = call.args[0] if call.args else call.kwargs.get("keywords", [])
            all_keywords.extend(kw_list)
        lower_kws = [k.lower() for k in all_keywords]
        assert any("break" in k or "deprecated" in k or "removed" in k for k in lower_kws), (
            f"KG not queried for breaking changes: {all_keywords}"
        )


# ---------------------------------------------------------------------------
# extra_context tests
# ---------------------------------------------------------------------------

class TestExtraContext:
    def setup_method(self):
        from jarvis_engine.agent.codegen.prompt_builder import UnityPromptBuilder
        self.builder = UnityPromptBuilder(_make_kg())

    def test_extra_context_appended(self):
        result = self.builder.build_unity_system_prompt(
            "rotating cube", extra_context="Output the script to Assets/JarvisGenerated/RotateCube.cs"
        )
        assert "Assets/JarvisGenerated/RotateCube.cs" in result

    def test_empty_extra_context_no_artifact(self):
        result_with = self.builder.build_unity_system_prompt("cube", extra_context="extra instructions here")
        result_without = self.builder.build_unity_system_prompt("cube", extra_context="")
        assert "extra instructions here" in result_with
        assert "extra instructions here" not in result_without


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

class TestConvenienceFunction:
    def test_module_level_function_returns_string(self):
        from jarvis_engine.agent.codegen.prompt_builder import build_unity_system_prompt
        kg = _make_kg()
        result = build_unity_system_prompt(kg, "spawn enemy")
        assert isinstance(result, str)
        assert len(result) > 50

    def test_module_level_function_with_extra_context(self):
        from jarvis_engine.agent.codegen.prompt_builder import build_unity_system_prompt
        kg = _make_kg()
        result = build_unity_system_prompt(kg, "spawn enemy", extra_context="use coroutines")
        assert "coroutines" in result
