"""NUnit test scaffolding generator for Unity game scripts.

Generates paired NUnit test files for every Unity C# game script, following
the convention: Assets/JarvisGenerated/Scripts/{Name}.cs ->
Assets/JarvisGenerated/Tests/{Name}Tests.cs.

When a ModelGateway is provided, uses LLM for context-aware test generation.
Without a gateway, produces a structural scaffold with [Test] and [UnityTest]
methods.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.gateway.models import ModelGateway


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Match `class ClassName` with optional access/partial modifiers
_RE_CLASS_NAME = re.compile(
    r'^(?:public\s+|internal\s+)?(?:partial\s+)?class\s+(\w+)', re.MULTILINE
)

# Match markdown code fences: ```csharp ... ``` or ``` ... ```
_RE_CODE_FENCE = re.compile(r"^```(?:csharp|cs)?\s*\n?([\s\S]*?)\n?```\s*$", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    """Remove markdown code fence wrappers from *text*."""
    stripped = text.strip()
    match = _RE_CODE_FENCE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _extract_class_name(script_content: str, fallback: str) -> str:
    """Extract the primary class name from C# source.

    Returns the first class name found, or *fallback* if none found.
    """
    match = _RE_CLASS_NAME.search(script_content)
    if match:
        return match.group(1)
    return fallback


def _compute_test_path(script_path: str, class_name: str) -> str:
    """Compute the canonical test file path from a script path.

    Convention:
    - Replace ``Scripts/`` with ``Tests/`` in the path.
    - Replace ``{ClassName}.cs`` with ``{ClassName}Tests.cs``.
    - If ``Scripts/`` is not present, place under ``Assets/JarvisGenerated/Tests/``.
    """
    if "Scripts/" in script_path:
        test_path = script_path.replace("Scripts/", "Tests/", 1)
        # Replace the filename portion: {Name}.cs -> {Name}Tests.cs
        test_path = re.sub(r"(\w+)\.cs$", lambda m: f"{m.group(1)}Tests.cs", test_path)
        return test_path

    # No Scripts/ directory: place directly under Assets/JarvisGenerated/Tests/
    return f"Assets/JarvisGenerated/Tests/{class_name}Tests.cs"


def _build_scaffold(class_name: str) -> str:
    """Build a structural NUnit test scaffold for *class_name*."""
    return f"""\
using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

[TestFixture]
public class {class_name}Tests
{{
    [Test]
    public void {class_name}_Exists()
    {{
        var go = new GameObject();
        var component = go.AddComponent<{class_name}>();
        Assert.IsNotNull(component);
        Object.DestroyImmediate(go);
    }}

    [UnityTest]
    public IEnumerator {class_name}_StartsCorrectly()
    {{
        var go = new GameObject();
        var component = go.AddComponent<{class_name}>();
        yield return null;
        Assert.IsTrue(component.enabled);
        Object.DestroyImmediate(go);
    }}
}}
"""


def _build_llm_prompt(script_path: str, class_name: str, script_content: str) -> str:
    """Build the LLM prompt for context-aware NUnit test generation."""
    return (
        f"Generate a complete NUnit test file for the following Unity 6.3 C# script.\n\n"
        f"Script path: {script_path}\n"
        f"Class name: {class_name}\n\n"
        f"Requirements:\n"
        f"- Include: using NUnit.Framework; using UnityEngine; using UnityEngine.TestTools;\n"
        f"- Use [TestFixture] on the test class, named {class_name}Tests\n"
        f"- Include at least one [Test] method that instantiates the component via AddComponent\n"
        f"- Include at least one [UnityTest] IEnumerator that yields one frame and checks component.enabled\n"
        f"- Follow Unity 6.3 NUnit best practices\n"
        f"- Call Object.DestroyImmediate(go) after each test to clean up\n\n"
        f"Script source:\n```csharp\n{script_content}\n```\n\n"
        f"Return ONLY the complete C# test file content, no explanation."
    )


# ---------------------------------------------------------------------------
# NUnitGenerator
# ---------------------------------------------------------------------------

class NUnitGenerator:
    """Generates paired NUnit test files for Unity game scripts.

    Args:
        gateway: Optional ModelGateway for LLM-backed test generation.
            When None, generates a structural scaffold instead.
    """

    def __init__(self, gateway: "ModelGateway | None" = None) -> None:
        self._gateway = gateway

    def generate(self, script_path: str, script_content: str) -> tuple[str, str]:
        """Generate a paired NUnit test file for a Unity script.

        Args:
            script_path: Relative path to the script, e.g.
                ``Assets/JarvisGenerated/Scripts/Player.cs``.
            script_content: Full C# source code of the script.

        Returns:
            A (test_path, test_content) tuple where test_path is the canonical
            test file location and test_content is the C# test source.

        Raises:
            ValueError: If *script_content* is empty or whitespace-only.
        """
        if not script_content or not script_content.strip():
            raise ValueError(
                "script_content must not be empty -- cannot generate tests for an empty script."
            )

        # Extract filename stem for fallback class name
        filename_stem = re.sub(r"\.cs$", "", script_path.rsplit("/", 1)[-1])

        class_name = _extract_class_name(script_content, fallback=filename_stem)
        test_path = _compute_test_path(script_path, class_name)

        if self._gateway is not None:
            test_content = self._generate_with_llm(script_path, class_name, script_content)
        else:
            test_content = _build_scaffold(class_name)

        return test_path, test_content

    def _generate_with_llm(
        self, script_path: str, class_name: str, script_content: str
    ) -> str:
        """Call the gateway to generate context-aware NUnit tests.

        Falls back to scaffold if the LLM response is empty.
        """
        system_prompt = (
            "You are a Unity 6.3 C# test generator. Generate only compilable NUnit test code."
            " Include all required using statements. Do not include explanations."
        )
        user_prompt = _build_llm_prompt(script_path, class_name, script_content)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._gateway.complete(messages, route_reason="nunit_test_generation")
        raw_text = response.text.strip() if response and response.text else ""

        if not raw_text:
            # LLM returned empty -- fall back to scaffold
            return _build_scaffold(class_name)

        return _strip_code_fences(raw_text)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def generate_nunit_test(
    script_path: str,
    script_content: str,
    gateway: "ModelGateway | None" = None,
) -> tuple[str, str]:
    """Generate a paired NUnit test file for a Unity script.

    Convenience wrapper around NUnitGenerator.

    Args:
        script_path: Relative path to the script, e.g.
            ``Assets/JarvisGenerated/Scripts/Player.cs``.
        script_content: Full C# source code of the script.
        gateway: Optional ModelGateway for LLM-backed test generation.

    Returns:
        A (test_path, test_content) tuple.

    Raises:
        ValueError: If *script_content* is empty or whitespace-only.
    """
    return NUnitGenerator(gateway=gateway).generate(script_path, script_content)
