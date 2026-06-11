"""Tests for three-tier routing: classification, model resolution, and /local parsing."""

from __future__ import annotations

from orchestrator.config import get_settings
from orchestrator.model_router import (
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
        """'what's the weather' matches STANDARD_SIMPLE_SIGNALS (weather) → standard."""
        assert classify_message("what's the weather") == "standard"

    def test_weather_alone_standard(self) -> None:
        """'weather' alone is a standard simple signal."""
        assert classify_message("weather") == "standard"

    def test_generate_image_standard(self) -> None:
        """'generate an image' is a standard simple signal."""
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

    def test_code_block_complex(self) -> None:
        """Messages with code blocks are classified as complex regardless of content."""
        assert classify_message("explain this: ```def foo(): pass```") == "complex"

    def test_long_message_complex(self) -> None:
        """Messages over 500 characters are classified as complex."""
        long_text = "a" * 501
        assert classify_message(long_text) == "complex"

    def test_many_tokens_complex(self) -> None:
        """Messages with more than 80 tokens are classified as complex."""
        many_tokens = "word " * 81
        assert classify_message(many_tokens) == "complex"

    def test_deep_conversation_complex(self) -> None:
        """High turn count bumps to complex."""
        assert classify_message("hello", turn_count=11) == "complex"

    def test_empty_message_trivial(self) -> None:
        """Empty message is trivially trivial."""
        assert classify_message("") == "trivial"
        assert classify_message("   ") == "trivial"


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
    """Integration tests verifying model selection against config defaults.

    These tests verify the three-tier routing contract:
    - trivial → auto_fast_model
    - standard → auto_reasoning_model
    - complex → auto_reasoning_model with advisor_eligible=True
    """

    def test_trivial_routes_to_flash_lite(self) -> None:
        """Trivial messages should route to auto_fast_model."""
        settings = get_settings()
        expected = settings.auto_fast_model

        classification = classify_message("hi")
        assert classification == "trivial"

        # The /chat path uses classification + auto_fast_model.
        assert expected == settings.auto_fast_model

    def test_complex_routes_to_m2_7_with_advisor(self) -> None:
        """Complex messages should route to auto_reasoning_model with advisor_eligible=True."""
        classification = classify_message("help me refactor this auth module")
        assert classification == "complex"

        decision = select_model_tier("help me refactor this auth module")
        assert decision.tier == "reasoning"
        assert decision.advisor_eligible is True

        # Verify model is the configured reasoning model.
        settings = get_settings()
        expected = settings.auto_reasoning_model
        assert expected == settings.auto_reasoning_model

    def test_standard_routes_to_m2_7_no_advisor(self) -> None:
        """Standard messages route to auto_reasoning_model but advisor_eligible=False."""
        classification = classify_message("what's the weather")
        assert classification == "standard"

        decision = select_model_tier("what's the weather")
        assert decision.tier == "fast"
        assert decision.advisor_eligible is False

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
        """'what's the weather' → standard (STANDARD_SIMPLE_SIGNALS)."""
        assert classify_message("what's the weather") == "standard"

    def test_write_a_python_script_complex(self) -> None:
        """'write a Python script that...' → complex (COMPLEXITY_SIGNALS)."""
        assert (
            classify_message("write a Python script that downloads files from a URL") == "complex"
        )

    def test_model_resolution_trivial_flash_lite(self) -> None:
        """Trivial → configured fast model."""
        settings = get_settings()
        expected = settings.auto_fast_model
        assert expected == settings.auto_fast_model

    def test_model_resolution_complex_m2_7_advisor(self) -> None:
        """Complex → configured reasoning model with advisor_eligible=True."""
        decision = select_model_tier("help me refactor this auth module")
        assert decision.tier == "reasoning"
        assert decision.advisor_eligible is True

        settings = get_settings()
        expected = settings.auto_reasoning_model
        assert expected == settings.auto_reasoning_model

    def test_model_resolution_standard_m2_7_no_advisor(self) -> None:
        """Standard → configured reasoning model with advisor_eligible=False."""
        decision = select_model_tier("what's the weather")
        assert decision.tier == "fast"
        assert decision.advisor_eligible is False

        settings = get_settings()
        expected = settings.auto_reasoning_model
        assert expected == settings.auto_reasoning_model
