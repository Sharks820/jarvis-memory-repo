"""Tests for LearnAccumulator -- learn-as-you-go KG knowledge accumulation.

Covers save_pattern, save_error_fix, query_patterns, truncation behavior,
node_id format, and empty-result handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kg(add_fact_return: bool = True, semantic_results: list[dict] | None = None):
    """Return a mock KnowledgeGraph suitable for LearnAccumulator tests."""
    kg = MagicMock()
    kg.add_fact.return_value = add_fact_return

    results = semantic_results if semantic_results is not None else []

    def _query_relevant_facts(keywords: list[str], *, min_confidence: float = 0.4, limit: int = 10):
        return results[:limit]

    kg.query_relevant_facts.side_effect = _query_relevant_facts
    return kg


def _make_pattern_result(node_id: str, label: str, node_type: str = "code_pattern") -> dict:
    return {
        "node_id": node_id,
        "label": label,
        "node_type": node_type,
        "confidence": 0.7,
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _import_accumulator():
    from jarvis_engine.agent.learn_accumulator import LearnAccumulator  # noqa: PLC0415
    return LearnAccumulator


# ---------------------------------------------------------------------------
# save_pattern tests
# ---------------------------------------------------------------------------

class TestSavePattern:
    def test_returns_true_when_kg_add_fact_succeeds(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg(add_fact_return=True)
        acc = LearnAccumulator(kg)
        result = acc.save_pattern(
            script_path="Assets/Foo.cs",
            code_snippet="public class Foo {}",
            description="Simple Foo class",
        )
        assert result is True

    def test_returns_false_when_kg_add_fact_fails(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg(add_fact_return=False)
        acc = LearnAccumulator(kg)
        result = acc.save_pattern(
            script_path="Assets/Bar.cs",
            code_snippet="public class Bar {}",
            description="Simple Bar class",
        )
        assert result is False

    def test_calls_add_fact_with_code_pattern_node_type(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern(
            script_path="Assets/Player.cs",
            code_snippet="void Start() {}",
            description="Player start method",
        )
        kg.add_fact.assert_called_once()
        call_kwargs = kg.add_fact.call_args
        # node_type must be "code_pattern"
        assert call_kwargs.kwargs.get("node_type") == "code_pattern" or (
            len(call_kwargs.args) >= 5 and call_kwargs.args[4] == "code_pattern"
        )

    def test_node_id_starts_with_pattern_prefix(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern(
            script_path="Assets/Test.cs",
            code_snippet="int x = 1;",
            description="test",
        )
        node_id = kg.add_fact.call_args.args[0]
        assert node_id.startswith("pattern:")

    def test_node_id_hash_is_12_hex_chars(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern(
            script_path="Assets/Test.cs",
            code_snippet="int x = 1;",
            description="test",
        )
        node_id = kg.add_fact.call_args.args[0]
        hash_part = node_id.split(":", 1)[1]
        assert len(hash_part) == 12
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_label_contains_description(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern(
            script_path="Assets/Test.cs",
            code_snippet="int x = 1;",
            description="my important description",
        )
        label = kg.add_fact.call_args.args[1]
        assert "my important description" in label

    def test_label_contains_code_snippet(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern(
            script_path="Assets/Test.cs",
            code_snippet="int x = 1;",
            description="test",
        )
        label = kg.add_fact.call_args.args[1]
        assert "int x = 1;" in label

    def test_code_snippet_truncated_to_500_chars(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        long_snippet = "x" * 2000
        acc.save_pattern(
            script_path="Assets/Test.cs",
            code_snippet=long_snippet,
            description="test",
        )
        label = kg.add_fact.call_args.args[1]
        # Label should not contain more than 500 x's from the snippet
        assert "x" * 501 not in label

    def test_source_record_is_script_path(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern(
            script_path="Assets/MyScript.cs",
            code_snippet="void Foo() {}",
            description="test",
        )
        call_kwargs = kg.add_fact.call_args
        # source_record can be positional or keyword
        source_record = call_kwargs.kwargs.get("source_record") or (
            call_kwargs.args[3] if len(call_kwargs.args) > 3 else None
        )
        assert source_record == "Assets/MyScript.cs"

    def test_confidence_is_0_7(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern(
            script_path="Assets/Test.cs",
            code_snippet="void Foo() {}",
            description="test",
        )
        confidence = kg.add_fact.call_args.args[2]
        assert confidence == 0.7

    def test_same_input_produces_same_node_id(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern("Assets/Test.cs", "int x = 1;", "d1")
        id1 = kg.add_fact.call_args.args[0]
        kg.reset_mock()
        acc.save_pattern("Assets/Test.cs", "int x = 1;", "d2")
        id2 = kg.add_fact.call_args.args[0]
        # Same script_path + code_snippet => same hash
        assert id1 == id2

    def test_different_inputs_produce_different_node_ids(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_pattern("Assets/A.cs", "int x = 1;", "test")
        id1 = kg.add_fact.call_args.args[0]
        kg.reset_mock()
        acc.save_pattern("Assets/B.cs", "int y = 2;", "test")
        id2 = kg.add_fact.call_args.args[0]
        assert id1 != id2


# ---------------------------------------------------------------------------
# save_error_fix tests
# ---------------------------------------------------------------------------

class TestSaveErrorFix:
    def test_returns_true_when_kg_succeeds(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg(add_fact_return=True)
        acc = LearnAccumulator(kg)
        result = acc.save_error_fix(
            error_message="CS0117: does not exist",
            fix_description="Use NewAPI instead",
            code_before="OldAPI.Call();",
            code_after="NewAPI.Call();",
        )
        assert result is True

    def test_returns_false_when_kg_fails(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg(add_fact_return=False)
        acc = LearnAccumulator(kg)
        result = acc.save_error_fix(
            error_message="CS0117: does not exist",
            fix_description="Use NewAPI instead",
            code_before="OldAPI.Call();",
            code_after="NewAPI.Call();",
        )
        assert result is False

    def test_node_id_starts_with_errfix_prefix(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("CS0117: err", "fix desc", "before", "after")
        node_id = kg.add_fact.call_args.args[0]
        assert node_id.startswith("errfix:")

    def test_node_id_hash_is_12_hex_chars(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("CS0117: err", "fix desc", "before", "after")
        node_id = kg.add_fact.call_args.args[0]
        hash_part = node_id.split(":", 1)[1]
        assert len(hash_part) == 12

    def test_calls_add_fact_with_error_fix_node_type(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("CS0619: deprecated", "use new API", "old()", "new()")
        call_kwargs = kg.add_fact.call_args
        node_type = call_kwargs.kwargs.get("node_type") or (
            call_kwargs.args[4] if len(call_kwargs.args) > 4 else None
        )
        assert node_type == "error_fix"

    def test_label_contains_error_message(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("CS0117: SomeType does not exist", "use OtherType", "old()", "new()")
        label = kg.add_fact.call_args.args[1]
        assert "CS0117" in label

    def test_label_contains_fix_description(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("error msg", "my special fix description", "old()", "new()")
        label = kg.add_fact.call_args.args[1]
        assert "my special fix description" in label

    def test_confidence_is_0_8(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("err", "fix", "before", "after")
        confidence = kg.add_fact.call_args.args[2]
        assert confidence == 0.8

    def test_source_record_is_compile_fix_loop(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("err", "fix", "before", "after")
        call_kwargs = kg.add_fact.call_args
        source_record = call_kwargs.kwargs.get("source_record") or (
            call_kwargs.args[3] if len(call_kwargs.args) > 3 else None
        )
        assert source_record == "compile_fix_loop"

    def test_error_message_truncated_in_label(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        long_error = "E" * 500
        acc.save_error_fix(long_error, "fix", "before", "after")
        label = kg.add_fact.call_args.args[1]
        # Error message in label must not exceed 200 chars
        assert "E" * 201 not in label

    def test_code_before_after_truncated_in_label(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg()
        acc = LearnAccumulator(kg)
        acc.save_error_fix("err", "fix", "B" * 500, "A" * 500)
        label = kg.add_fact.call_args.args[1]
        assert "B" * 201 not in label
        assert "A" * 201 not in label


# ---------------------------------------------------------------------------
# query_patterns tests
# ---------------------------------------------------------------------------

class TestQueryPatterns:
    def test_returns_empty_list_when_no_results(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg(semantic_results=[])
        acc = LearnAccumulator(kg)
        result = acc.query_patterns("create a player controller")
        assert result == []

    def test_returns_labels_from_matching_facts(self):
        LearnAccumulator = _import_accumulator()
        pattern_facts = [
            _make_pattern_result("pattern:abc123456789", "[Code Pattern] Player\n---\nvoid Update() {}", "code_pattern"),
            _make_pattern_result("errfix:def456789012", "[Error Fix] CS0117 -> use NewAPI", "error_fix"),
        ]
        kg = _make_kg(semantic_results=pattern_facts)
        acc = LearnAccumulator(kg)
        result = acc.query_patterns("player movement", limit=5)
        assert len(result) == 2
        assert "[Code Pattern] Player" in result[0]
        assert "[Error Fix] CS0117" in result[1]

    def test_filters_out_non_pattern_node_types(self):
        LearnAccumulator = _import_accumulator()
        mixed_facts = [
            _make_pattern_result("pattern:aaa", "[Code Pattern] Foo", "code_pattern"),
            _make_pattern_result("unity:bbb", "Unity 6.3 API fact", "unity_api"),
            _make_pattern_result("errfix:ccc", "[Error Fix] bar -> baz", "error_fix"),
            _make_pattern_result("fact:ddd", "some other fact", "fact"),
        ]
        kg = _make_kg(semantic_results=mixed_facts)
        acc = LearnAccumulator(kg)
        result = acc.query_patterns("task description", limit=10)
        # Only code_pattern and error_fix should be included
        assert len(result) == 2
        assert any("[Code Pattern]" in r for r in result)
        assert any("[Error Fix]" in r for r in result)

    def test_respects_limit_parameter(self):
        LearnAccumulator = _import_accumulator()
        many_facts = [
            _make_pattern_result(f"pattern:{i:012d}", f"[Code Pattern] Pattern {i}", "code_pattern")
            for i in range(20)
        ]
        kg = _make_kg(semantic_results=many_facts)
        acc = LearnAccumulator(kg)
        result = acc.query_patterns("some task", limit=3)
        assert len(result) <= 3

    def test_default_limit_is_5(self):
        LearnAccumulator = _import_accumulator()
        many_facts = [
            _make_pattern_result(f"pattern:{i:012d}", f"[Code Pattern] Pattern {i}", "code_pattern")
            for i in range(20)
        ]
        kg = _make_kg(semantic_results=many_facts)
        acc = LearnAccumulator(kg)
        result = acc.query_patterns("some task")
        assert len(result) <= 5

    def test_calls_kg_query_with_task_description(self):
        LearnAccumulator = _import_accumulator()
        kg = _make_kg(semantic_results=[])
        acc = LearnAccumulator(kg)
        acc.query_patterns("create enemy AI")
        # Should have called some query method on kg
        assert kg.query_relevant_facts.called or kg.query_relevant_facts_semantic.called

    def test_returns_list_of_strings(self):
        LearnAccumulator = _import_accumulator()
        facts = [_make_pattern_result("pattern:abc123456789", "[Code Pattern] test", "code_pattern")]
        kg = _make_kg(semantic_results=facts)
        acc = LearnAccumulator(kg)
        result = acc.query_patterns("test")
        assert isinstance(result, list)
        assert all(isinstance(r, str) for r in result)


# ---------------------------------------------------------------------------
# __all__ check
# ---------------------------------------------------------------------------

class TestModuleExports:
    def test_all_exports_learn_accumulator(self):
        import jarvis_engine.agent.learn_accumulator as mod  # noqa: PLC0415
        assert "LearnAccumulator" in mod.__all__

    def test_learn_accumulator_is_importable(self):
        from jarvis_engine.agent.learn_accumulator import LearnAccumulator  # noqa: PLC0415, F401
        assert LearnAccumulator is not None
