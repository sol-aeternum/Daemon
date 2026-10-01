"""Tests for three-tier routing: classification, model resolution, and /local parsing."""

from __future__ import annotations

from orchestrator.model_router import (
    _instruction_text,  # pyright: ignore[reportPrivateUsage]
    classify_message,
    select_model_tier,
)
from orchestrator.router import route_message


class TestClassifyMessage:
    """Unit tests for message classification (trivial/standard/complex)."""

    def test_hi_is_trivial(self) -> None:
        """'hi' is a canonical trivial phrase."""
        assert classify_message("hi") == "trivial"

    def test_hello_is_trivial(self) -> None:
        """'hello' is a canonical trivial phrase."""
        assert classify_message("hello") == "trivial"

    def test_thanks_is_trivial(self) -> None:
        """'thanks' is a canonical trivial phrase."""
        assert classify_message("thanks") == "trivial"

    def test_okay_is_trivial(self) -> None:
        """'okay' is a canonical trivial phrase."""
        assert classify_message("okay") == "trivial"

    def test_what_time_is_it_trivial(self) -> None:
        """'what time is it' matches TRIVIAL_SIMPLE_SIGNALS → trivial."""
        assert classify_message("what time is it") == "trivial"

    def test_whats_the_weather_standard(self) -> None:
        """Weather without a complexity signal is a routine workload."""
        assert classify_message("what's the weather") == "standard"

    def test_weather_alone_standard(self) -> None:
        """Weather alone is standard."""
        assert classify_message("weather") == "standard"

    def test_generate_image_standard(self) -> None:
        """Image text is classified as standard (modality gate still denies it)."""
        assert classify_message("generate an image") == "standard"

    def test_refactor_complex(self) -> None:
        """'help me refactor this auth module' contains 'refactor' → complex."""
        assert classify_message("help me refactor this auth module") == "complex"

    def test_debug_complex(self) -> None:
        """Messages with 'debug' signal are classified as complex."""
        assert classify_message("debug this function for me") == "complex"

    def test_write_python_script_complex(self) -> None:
        """'write a Python script' matches COMPLEXITY_SIGNALS → complex."""
        assert classify_message("write a Python script that downloads files") == "complex"

    def test_analyze_complex(self) -> None:
        """'analyze' signal → complex."""
        assert classify_message("analyze the pros and cons") == "complex"

    def test_long_document_critique_uses_reasoning_for_the_task(self) -> None:
        document = "Document evidence. " * 1500
        assert select_model_tier(f"Summarise and critique this: {document}").profile == "reasoning"
        assert select_model_tier("Critique this short argument.").profile == "reasoning"
        assert select_model_tier(f"Summarise this: {document}").profile == "routine"

    def test_british_analysis_and_critique_word_boundaries(self) -> None:
        assert classify_message("Analyse the evidence") == "complex"
        assert classify_message("The filename is autocritique.txt") == "standard"

    def test_code_block_complex(self) -> None:
        """Messages with code blocks are classified as complex regardless of content."""
        assert classify_message("explain this: ```def foo(): pass```") == "complex"

    def test_long_message_is_not_a_quality_requirement(self) -> None:
        """Length is accounted for by context/output bounds, not model quality."""
        long_text = "a" * 501
        assert classify_message(long_text) == "standard"

    def test_many_tokens_is_not_a_quality_requirement(self) -> None:
        """A long prompt alone does not upgrade to reasoning."""
        many_tokens = "word " * 81
        assert classify_message(many_tokens) == "standard"

    def test_deep_conversation_is_not_a_quality_requirement(self) -> None:
        """Turn count does not upgrade a greeting to reasoning."""
        assert classify_message("hello", turn_count=11) == "trivial"

    def test_simple_prefix_does_not_hide_complex_work(self) -> None:
        assert classify_message("search for and compare these architectures") == "complex"
        assert classify_message("remember my strategy and evaluate the options") == "complex"

    def test_signals_match_word_boundaries(self) -> None:
        assert classify_message("the username is comparisondebugger") == "standard"

    def test_empty_message_trivial(self) -> None:
        """Empty message is trivially trivial."""
        assert classify_message("") == "trivial"
        assert classify_message("   ") == "trivial"


class TestInstructionText:
    """The routing view of a message: the user's own text, not pasted or quoted data."""

    def test_closed_backtick_and_tilde_fences_are_removed(self) -> None:
        assert _instruction_text("fix this\n```py\ncompare()\n```") == "fix this"
        assert _instruction_text("fix this\n~~~\ncompare()\n~~~") == "fix this"

    def test_closing_fence_may_be_longer_but_not_a_different_marker(self) -> None:
        assert _instruction_text("a\n```\nx\n`````\nb") == "a\nb"
        assert _instruction_text("a\n```\nx\n~~~\nb") == "a\n```\nx\n~~~\nb"

    def test_unclosed_fence_is_kept_as_text(self) -> None:
        assert _instruction_text("```\ncompare A and B") == "```\ncompare A and B"

    def test_inline_triple_backtick_span_is_not_a_fence(self) -> None:
        assert _instruction_text("```foo``` compare\nnext") == "```foo``` compare\nnext"

    def test_fence_indented_four_spaces_is_not_a_fence(self) -> None:
        message = "    ```\n    compare\n    ```"
        assert _instruction_text(message) == message.strip()

    def test_blockquotes_are_removed_when_own_text_remains(self) -> None:
        assert _instruction_text("> compare these\n>> nested\nreply briefly") == "reply briefly"

    def test_quote_only_message_keeps_the_quoted_instruction(self) -> None:
        assert _instruction_text("> compare A and B") == "> compare A and B"

    def test_fence_only_message_has_no_instruction(self) -> None:
        assert _instruction_text("```\n# refactor later\n```") == ""


class TestSelectModelTier:
    """Unit tests for legacy tier selection and advisor eligibility."""

    def test_trivial_fast_tier(self) -> None:
        """Trivial messages map to fast tier."""
        decision = select_model_tier("hi")
        assert decision.tier == "fast"
        assert decision.advisor_eligible is False

    def test_standard_fast_tier(self) -> None:
        """Standard messages map to fast tier."""
        decision = select_model_tier("what's the weather")
        assert decision.tier == "fast"
        assert decision.advisor_eligible is False

    def test_complex_reasoning_tier(self) -> None:
        """Complex messages map to reasoning tier with advisor eligible."""
        decision = select_model_tier("help me refactor this auth module")
        assert decision.tier == "reasoning"
        assert decision.advisor_eligible is True
        assert decision.profile == "reasoning"

    def test_research_workload_profile(self) -> None:
        decision = select_model_tier("search for recent weather reports")
        assert decision.profile == "research"
        assert decision.model == ""

    def test_manual_model_is_exact(self) -> None:
        model = "openrouter/z-ai/glm-5.3"
        decision = select_model_tier("debug this parser", user_override=model)
        assert decision.model == model
        assert decision.tier == "explicit"

    def test_code_block_reasoning_advisor_eligible(self) -> None:
        """Code blocks route to reasoning with advisor eligible."""
        decision = select_model_tier("explain this: ```def foo(): pass```")
        assert decision.tier == "reasoning"
        assert decision.advisor_eligible is True

    def test_user_override_explicit_tier(self) -> None:
        """User override bypasses classification and sets explicit tier."""
        decision = select_model_tier("hi", user_override="openrouter/anthropic/claude-3.5-sonnet")
        assert decision.tier == "explicit"
        assert decision.model == "openrouter/anthropic/claude-3.5-sonnet"
        assert decision.advisor_eligible is False


class TestRouteMessageLocalFlag:
    """Unit tests for /local prefix parsing and stripping."""

    def test_local_flag_strips_prefix(self) -> None:
        """/local prefix is stripped from user_message."""
        decision = route_message("/local hello world", None)
        assert decision.user_message == "hello world"
        assert decision.local_requested is True

    def test_local_flag_no_space(self) -> None:
        """/local without trailing space still strips correctly."""
        decision = route_message("/localhello world", None)
        # stripped.lstrip() removes leading whitespace after prefix
        assert decision.user_message == "hello world"
        assert decision.local_requested is True

    def test_local_flag_with_multiple_spaces(self) -> None:
        """/local   with multiple spaces strips correctly."""
        decision = route_message("/local   say hello", None)
        assert decision.user_message == "say hello"
        assert decision.local_requested is True

    def test_local_flag_empty_message(self) -> None:
        """/local with no message after it returns empty string."""
        decision = route_message("/local", None)
        assert decision.user_message == ""
        assert decision.local_requested is True

    def test_local_flag_middle_of_message(self) -> None:
        """/local in the middle does NOT trigger local routing (only prefix)."""
        decision = route_message("hello /local world", None)
        assert decision.user_message == "hello /local world"
        assert decision.local_requested is False

    def test_council_command_not_local(self) -> None:
        """/council is a separate command, not local."""
        decision = route_message("/council help me decide", None)
        assert decision.local_requested is False
        assert decision.command == "council"
        assert decision.user_message == "help me decide"

    def test_no_flag_cloud_pipeline(self) -> None:
        """Regular messages route to cloud pipeline without local flag."""
        decision = route_message("hello world", None)
        assert decision.local_requested is False
        assert decision.pipeline == "cloud"
        assert decision.user_message == "hello world"


class TestThreeTierRoutingIntegration:
    """Classification provides workload profiles; dispatch owns model selection."""

    def test_trivial_routes_to_routine(self) -> None:
        assert select_model_tier("hi").profile == "routine"

    def test_complex_routes_to_reasoning(self) -> None:
        classification = classify_message("help me refactor this auth module")
        assert classification == "complex"

        decision = select_model_tier("help me refactor this auth module")
        assert decision.tier == "reasoning"
        assert decision.advisor_eligible is True

        assert decision.profile == "reasoning"
        assert decision.model == ""

    def test_standard_routes_to_routine(self) -> None:
        classification = classify_message("what's the weather")
        assert classification == "standard"

        decision = select_model_tier("what's the weather")
        assert decision.tier == "fast"
        assert decision.advisor_eligible is False
        assert decision.profile == "routine"

    def test_local_flag_preserves_stripped_message_for_classification(self) -> None:
        """/local stripped text is used for classification downstream."""
        raw = "/local help me refactor this auth module"
        decision = route_message(raw, None)

        assert decision.local_requested is True
        stripped = decision.user_message

        # The stripped message should be classified correctly
        classification = classify_message(stripped)
        assert classification == "complex"


class TestRepresentativeCases:
    """Tests for the specific representative cases listed in the plan."""

    def test_hi_trivial(self) -> None:
        """'hi' → trivial."""
        assert classify_message("hi") == "trivial"

    def test_what_time_is_it_trivial(self) -> None:
        """'what time is it' → trivial (TRIVIAL_SIMPLE_SIGNALS)."""
        assert classify_message("what time is it") == "trivial"

    def test_help_me_refactor_auth_module_complex(self) -> None:
        """'help me refactor this auth module' → complex."""
        assert classify_message("help me refactor this auth module") == "complex"

    def test_whats_the_weather_standard(self) -> None:
        """'what's the weather' → standard."""
        assert classify_message("what's the weather") == "standard"

    def test_write_a_python_script_complex(self) -> None:
        """'write a Python script that...' → complex (COMPLEXITY_SIGNALS)."""
        assert (
            classify_message("write a Python script that downloads files from a URL") == "complex"
        )

    def test_model_resolution_trivial_routine(self) -> None:
        """Trivial → routine profile without preselecting a model."""
        decision = select_model_tier("hi")
        assert decision.profile == "routine"
        assert decision.model == ""

    def test_model_resolution_complex_reasoning(self) -> None:
        """Complex → reasoning profile with advisor compatibility metadata."""
        decision = select_model_tier("help me refactor this auth module")
        assert decision.tier == "reasoning"
        assert decision.advisor_eligible is True

        assert decision.profile == "reasoning"
        assert decision.model == ""

    def test_model_resolution_standard_routine(self) -> None:
        """Standard → routine profile without preselecting a model."""
        decision = select_model_tier("what's the weather")
        assert decision.tier == "fast"
        assert decision.advisor_eligible is False

        assert decision.profile == "routine"
        assert decision.model == ""
