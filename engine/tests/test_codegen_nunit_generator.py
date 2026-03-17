"""Tests for NUnitGenerator -- paired NUnit test scaffolding for Unity scripts."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PLAYER_SCRIPT = """\
using UnityEngine;

public class Player : MonoBehaviour
{
    [field: SerializeField] public float Speed { get; private set; }

    void Start()
    {
        Speed = 5f;
    }

    void Update()
    {
        transform.Translate(Vector3.forward * Speed * Time.deltaTime);
    }
}
"""

_ENEMY_SCRIPT = """\
using UnityEngine;

public class Enemy : MonoBehaviour
{
    private float _health = 100f;

    void Start()
    {
        _health = 100f;
    }
}
"""

_EMPTY_SCRIPT = ""
_WHITESPACE_SCRIPT = "   \n\t  "


def _make_gateway(response_text: str) -> MagicMock:
    """Return a mock ModelGateway that returns *response_text*."""
    gateway = MagicMock()
    response = MagicMock()
    response.text = response_text
    gateway.complete.return_value = response
    return gateway


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestImports:
    def test_import_nunit_generator(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator  # noqa: F401

    def test_import_generate_nunit_test(self):
        from jarvis_engine.agent.codegen.nunit_generator import generate_nunit_test  # noqa: F401

    def test_nunit_generator_instantiates_without_gateway(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        gen = NUnitGenerator()
        assert gen is not None

    def test_nunit_generator_instantiates_with_gateway(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        gw = _make_gateway("")
        gen = NUnitGenerator(gateway=gw)
        assert gen is not None


# ---------------------------------------------------------------------------
# Empty script content
# ---------------------------------------------------------------------------

class TestEmptyContent:
    def setup_method(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        self.gen = NUnitGenerator()

    def test_empty_script_raises_value_error(self):
        with pytest.raises(ValueError):
            self.gen.generate("Assets/JarvisGenerated/Scripts/Player.cs", _EMPTY_SCRIPT)

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            self.gen.generate("Assets/JarvisGenerated/Scripts/Player.cs", _WHITESPACE_SCRIPT)


# ---------------------------------------------------------------------------
# Test path convention
# ---------------------------------------------------------------------------

class TestTestPathConvention:
    def setup_method(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        self.gen = NUnitGenerator()

    def test_path_scripts_becomes_tests(self):
        test_path, _ = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "Tests/" in test_path
        assert "Scripts/" not in test_path

    def test_path_filename_appends_tests(self):
        test_path, _ = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert test_path.endswith("PlayerTests.cs")

    def test_path_nested_scripts_directory(self):
        test_path, _ = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Enemies/Enemy.cs", _ENEMY_SCRIPT
        )
        assert test_path.endswith("EnemyTests.cs")
        assert "Tests/" in test_path

    def test_path_no_scripts_dir_uses_default(self):
        """If 'Scripts/' is not in the path, place under Assets/JarvisGenerated/Tests/."""
        test_path, _ = self.gen.generate(
            "Assets/JarvisGenerated/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert test_path.startswith("Assets/JarvisGenerated/Tests/")
        assert test_path.endswith("PlayerTests.cs")

    def test_path_enemy_scripts_convention(self):
        test_path, _ = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Enemy.cs", _ENEMY_SCRIPT
        )
        assert test_path == "Assets/JarvisGenerated/Tests/EnemyTests.cs"


# ---------------------------------------------------------------------------
# Scaffold content (no gateway)
# ---------------------------------------------------------------------------

class TestScaffoldContent:
    def setup_method(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        self.gen = NUnitGenerator()

    def test_scaffold_includes_nunit_framework_using(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "using NUnit.Framework;" in content

    def test_scaffold_includes_unity_using(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "using UnityEngine;" in content

    def test_scaffold_includes_unity_test_tools_using(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "using UnityEngine.TestTools;" in content

    def test_scaffold_has_test_fixture_class(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "[TestFixture]" in content

    def test_scaffold_class_name_is_class_name_tests(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "class PlayerTests" in content

    def test_scaffold_has_test_attribute(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "[Test]" in content

    def test_scaffold_has_unity_test_attribute(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "[UnityTest]" in content

    def test_scaffold_exists_test_method(self):
        """Scaffold should have a _Exists() test that instantiates the component."""
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "Player_Exists" in content

    def test_scaffold_starts_correctly_method(self):
        """Scaffold should have a _StartsCorrectly() IEnumerator method."""
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "Player_StartsCorrectly" in content

    def test_scaffold_references_original_class(self):
        """Test content should reference the original class name."""
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "Player" in content

    def test_scaffold_enemy_class_name(self):
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Enemy.cs", _ENEMY_SCRIPT
        )
        assert "class EnemyTests" in content
        assert "Enemy_Exists" in content

    def test_scaffold_has_add_component_call(self):
        """Scaffold _Exists test should add the component to a GameObject."""
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "AddComponent" in content

    def test_scaffold_starts_correctly_is_ienumerator(self):
        """StartsCorrectly must be an IEnumerator (for [UnityTest])."""
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "IEnumerator" in content

    def test_scaffold_asserts_component_enabled(self):
        """StartsCorrectly should assert the component is enabled."""
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "enabled" in content or "Assert" in content


# ---------------------------------------------------------------------------
# LLM-backed generation (with gateway)
# ---------------------------------------------------------------------------

class TestLlmGeneration:
    def test_gateway_complete_is_called(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        # Return a plausible test content from the LLM
        llm_response = """\
```csharp
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using System.Collections;

[TestFixture]
public class PlayerTests
{
    [Test]
    public void Player_Exists()
    {
        var go = new GameObject();
        var player = go.AddComponent<Player>();
        Assert.IsNotNull(player);
        Object.DestroyImmediate(go);
    }

    [UnityTest]
    public IEnumerator Player_StartsCorrectly()
    {
        var go = new GameObject();
        var player = go.AddComponent<Player>();
        yield return null;
        Assert.IsTrue(player.enabled);
        Object.DestroyImmediate(go);
    }
}
```"""
        gw = _make_gateway(llm_response)
        gen = NUnitGenerator(gateway=gw)
        test_path, test_content = gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        gw.complete.assert_called_once()
        assert test_path.endswith("PlayerTests.cs")

    def test_gateway_response_stripped_of_fences(self):
        """LLM response with markdown fences should have fences stripped."""
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        llm_response = "```csharp\nusing NUnit.Framework;\n\npublic class PlayerTests {}\n```"
        gw = _make_gateway(llm_response)
        gen = NUnitGenerator(gateway=gw)
        _, content = gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert content.startswith("using NUnit.Framework;")
        assert "```" not in content

    def test_gateway_empty_response_falls_back_to_scaffold(self):
        """If LLM returns empty string, fall back to scaffold."""
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        gw = _make_gateway("")
        gen = NUnitGenerator(gateway=gw)
        _, content = gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        # Should not be empty -- scaffold fallback
        assert len(content) > 50
        assert "NUnit.Framework" in content

    def test_gateway_response_without_fences(self):
        """LLM response without code fences should be returned as-is."""
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        llm_response = "using NUnit.Framework;\n\npublic class PlayerTests {}"
        gw = _make_gateway(llm_response)
        gen = NUnitGenerator(gateway=gw)
        _, content = gen.generate(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert "NUnit.Framework" in content


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

class TestConvenienceFunction:
    def test_generate_nunit_test_no_gateway(self):
        from jarvis_engine.agent.codegen.nunit_generator import generate_nunit_test
        test_path, test_content = generate_nunit_test(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT
        )
        assert test_path.endswith("PlayerTests.cs")
        assert "NUnit.Framework" in test_content

    def test_generate_nunit_test_with_gateway(self):
        from jarvis_engine.agent.codegen.nunit_generator import generate_nunit_test
        gw = _make_gateway("using NUnit.Framework;\npublic class PlayerTests {}")
        test_path, test_content = generate_nunit_test(
            "Assets/JarvisGenerated/Scripts/Player.cs", _SIMPLE_PLAYER_SCRIPT,
            gateway=gw,
        )
        assert test_path.endswith("PlayerTests.cs")
        gw.complete.assert_called_once()

    def test_generate_nunit_test_returns_tuple(self):
        from jarvis_engine.agent.codegen.nunit_generator import generate_nunit_test
        result = generate_nunit_test(
            "Assets/JarvisGenerated/Scripts/Enemy.cs", _ENEMY_SCRIPT
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_generate_nunit_test_empty_raises(self):
        from jarvis_engine.agent.codegen.nunit_generator import generate_nunit_test
        with pytest.raises(ValueError):
            generate_nunit_test("Assets/JarvisGenerated/Scripts/Player.cs", "")


# ---------------------------------------------------------------------------
# Class name extraction edge cases
# ---------------------------------------------------------------------------

class TestClassNameExtraction:
    def setup_method(self):
        from jarvis_engine.agent.codegen.nunit_generator import NUnitGenerator
        self.gen = NUnitGenerator()

    def test_class_name_from_colon_inheritance(self):
        """Extract class name even when it inherits from MonoBehaviour."""
        script = "public class PlayerController : MonoBehaviour { }"
        test_path, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/PlayerController.cs", script
        )
        assert test_path.endswith("PlayerControllerTests.cs")
        assert "class PlayerControllerTests" in content

    def test_class_name_fallback_to_filename(self):
        """If regex can't find class, use filename stem."""
        script = "// just a comment\n// no class"
        # Should not raise, falls back to filename stem
        test_path, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Foo.cs", script
        )
        assert test_path.endswith("FooTests.cs")
        assert "FooTests" in content

    def test_class_name_first_class_used(self):
        """When multiple classes in file, first is used."""
        script = "public class Alpha : MonoBehaviour {} public class Beta {}"
        _, content = self.gen.generate(
            "Assets/JarvisGenerated/Scripts/Alpha.cs", script
        )
        assert "class AlphaTests" in content
