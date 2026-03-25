"""Tests for council_completion_with_tools function."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from orchestrator.council.tools import council_completion_with_tools


# Helper to create mock litellm response objects
def create_mock_response(
    content=None,
    tool_calls=None,
    prompt_tokens=10,
    completion_tokens=20,
    cost_usd=0.001,
):
    """Create a mock response object that mimics litellm response structure."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_choice.message.tool_calls = tool_calls

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = prompt_tokens
    mock_usage.completion_tokens = completion_tokens
    mock_usage.total_tokens = prompt_tokens + completion_tokens

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    mock_response._hidden_params = {"response_cost": cost_usd}
    return mock_response


# Helper to create mock tool call objects
def create_mock_tool_call(id, name, arguments):
    """Create a mock tool call object."""
    mock_function = MagicMock()
    mock_function.name = name
    mock_function.arguments = arguments

    mock_tool_call = MagicMock()
    mock_tool_call.id = id
    mock_tool_call.type = "function"
    mock_tool_call.function = mock_function
    return mock_tool_call


class TestCouncilCompletionWithTools:
    @pytest.mark.asyncio
    async def test_single_tool_call_then_final_response(self):
        """Test scenario 1: Single tool call then final response."""
        # Mock responses
        tool_call_response = create_mock_response(
            content="Let me check the weather",
            tool_calls=[
                create_mock_tool_call("call_1", "get_weather", '{"city": "NYC"}')
            ],
            prompt_tokens=15,
            completion_tokens=25,
            cost_usd=0.0015,
        )
        final_response = create_mock_response(
            content="The weather in NYC is sunny.",
            tool_calls=None,
            prompt_tokens=30,  # Higher because of previous messages
            completion_tokens=10,
            cost_usd=0.0008,
        )

        mock_acompletion = AsyncMock(side_effect=[tool_call_response, final_response])

        with patch("orchestrator.council.tools.litellm.acompletion", mock_acompletion):
            # Create a mock tool executor object with an execute method
            tool_executor = MagicMock()
            tool_executor.execute = AsyncMock(return_value="Sunny, 75°F")
            messages = [{"role": "user", "content": "What's the weather in NYC?"}]
            tools = [{"type": "function", "function": {"name": "get_weather"}}]

            result, usage = await council_completion_with_tools(
                model="test-model",
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
            )

            assert result == "The weather in NYC is sunny."
            # Usage should be accumulated from both calls
            assert usage["prompt_tokens"] == 45
            assert usage["completion_tokens"] == 35
            assert usage["total_tokens"] == 80
            assert usage["cost_usd"] == 0.0023
            tool_executor.execute.assert_awaited_once_with(
                "get_weather", '{"city": "NYC"}'
            )

    @pytest.mark.asyncio
    async def test_multiple_sequential_tool_calls(self):
        """Test scenario 2: Multiple sequential tool calls (3+)."""
        # Mock responses for 3 tool calls then final response
        responses = [
            create_mock_response(
                content="First step",
                tool_calls=[create_mock_tool_call("call_1", "step_one", "{}")],
                prompt_tokens=10,
                completion_tokens=15,
                cost_usd=0.001,
            ),
            create_mock_response(
                content="Second step",
                tool_calls=[create_mock_tool_call("call_2", "step_two", "{}")],
                prompt_tokens=20,  # Higher because of previous messages
                completion_tokens=20,
                cost_usd=0.0012,
            ),
            create_mock_response(
                content="Third step",
                tool_calls=[create_mock_tool_call("call_3", "step_three", "{}")],
                prompt_tokens=30,  # Higher because of previous messages
                completion_tokens=25,
                cost_usd=0.0015,
            ),
            create_mock_response(
                content="All steps completed successfully.",
                tool_calls=None,
                prompt_tokens=40,  # Higher because of previous messages
                completion_tokens=10,
                cost_usd=0.0008,
            ),
        ]

        mock_acompletion = AsyncMock(side_effect=responses)

        with patch("orchestrator.council.tools.litellm.acompletion", mock_acompletion):
            # Create a mock tool executor object with an execute method
            tool_executor = MagicMock()
            tool_executor.execute = AsyncMock(
                side_effect=["Result 1", "Result 2", "Result 3"]
            )
            messages = [{"role": "user", "content": "Execute the plan"}]
            tools = [
                {"type": "function", "function": {"name": "step_one"}},
                {"type": "function", "function": {"name": "step_two"}},
                {"type": "function", "function": {"name": "step_three"}},
            ]

            result, usage = await council_completion_with_tools(
                model="test-model",
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
            )

            assert result == "All steps completed successfully."
            # Usage should be accumulated from all calls
            assert usage["prompt_tokens"] == 100
            assert usage["completion_tokens"] == 70
            assert usage["total_tokens"] == 170
            assert usage["cost_usd"] == 0.0045
            assert tool_executor.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_max_tool_rounds_exceeded(self):
        """Test scenario 3: Max tool rounds exceeded."""
        # Create 5 tool call responses + 1 final response that should trigger the warning
        responses = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cost = 0.0

        # Create 5 tool call responses
        for i in range(5):
            prompt_tokens = 10 + i * 5
            completion_tokens = 15 + i * 2
            cost = 0.001 + i * 0.0001

            responses.append(
                create_mock_response(
                    content=f"Step {i + 1}",
                    tool_calls=[create_mock_tool_call(f"call_{i + 1}", "step", "{}")],
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost,
                )
            )

            total_prompt_tokens += prompt_tokens
            total_completion_tokens += completion_tokens
            total_cost += cost

        # Create final response that should trigger warning
        final_prompt_tokens = 10 + 5 * 5
        final_completion_tokens = 15 + 5 * 2
        final_cost = 0.001 + 5 * 0.0001

        responses.append(
            create_mock_response(
                content="Final step",
                tool_calls=[create_mock_tool_call("call_6", "step", "{}")],
                prompt_tokens=final_prompt_tokens,
                completion_tokens=final_completion_tokens,
                cost_usd=final_cost,
            )
        )

        total_prompt_tokens += final_prompt_tokens
        total_completion_tokens += final_completion_tokens
        total_cost += final_cost

        mock_acompletion = AsyncMock(side_effect=responses)

        with patch("orchestrator.council.tools.litellm.acompletion", mock_acompletion):
            # Create a mock tool executor object with an execute method
            tool_executor = MagicMock()
            tool_executor.execute = AsyncMock(return_value="Step result")
            messages = [{"role": "user", "content": "Execute long plan"}]
            tools = [{"type": "function", "function": {"name": "step"}}]

            result, usage = await council_completion_with_tools(
                model="test-model",
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                max_tool_rounds=5,
            )

            assert (
                "Warning: council tool loop stopped after reaching max_tool_rounds=5"
                in result
            )
            assert usage["prompt_tokens"] == total_prompt_tokens
            assert usage["completion_tokens"] == total_completion_tokens
            assert (
                usage["total_tokens"] == total_prompt_tokens + total_completion_tokens
            )
            assert usage["cost_usd"] == round(total_cost, 12)
            assert tool_executor.execute.await_count == 5

    @pytest.mark.asyncio
    async def test_tool_execution_failure(self):
        """Test scenario 4: Tool execution failure."""
        tool_call_response = create_mock_response(
            content="Let me check",
            tool_calls=[create_mock_tool_call("call_1", "failing_tool", "{}")],
            prompt_tokens=12,
            completion_tokens=18,
            cost_usd=0.0012,
        )
        final_response = create_mock_response(
            content="The tool failed to execute.",
            tool_calls=None,
            prompt_tokens=25,  # Higher because of previous messages
            completion_tokens=8,
            cost_usd=0.0006,
        )

        mock_acompletion = AsyncMock(side_effect=[tool_call_response, final_response])

        with patch("orchestrator.council.tools.litellm.acompletion", mock_acompletion):
            # Create a mock tool executor object with an execute method that raises an exception
            tool_executor = MagicMock()
            tool_executor.execute = AsyncMock(side_effect=Exception("Tool error"))
            messages = [{"role": "user", "content": "Use failing tool"}]
            tools = [{"type": "function", "function": {"name": "failing_tool"}}]

            result, usage = await council_completion_with_tools(
                model="test-model",
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
            )

            assert "The tool failed to execute." in result
            assert usage["prompt_tokens"] == 37
            assert usage["completion_tokens"] == 26
            assert usage["total_tokens"] == 63
            assert usage["cost_usd"] == 0.0018

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test scenario 5: Timeout handling."""
        mock_acompletion = AsyncMock(side_effect=TimeoutError("Request timed out"))

        with patch("orchestrator.council.tools.litellm.acompletion", mock_acompletion):
            tool_executor = AsyncMock()
            messages = [{"role": "user", "content": "Slow request"}]
            tools = []

            result, usage = await council_completion_with_tools(
                model="test-model",
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                timeout=1,
            )

            assert "Error: Request timed out" in result
            assert usage["prompt_tokens"] == 0
            assert usage["completion_tokens"] == 0
            assert usage["total_tokens"] == 0
            assert usage["cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_no_tool_calls_single_shot(self):
        """Test scenario 6: No tool calls (single-shot)."""
        response = create_mock_response(
            content="Hello! How can I help you today?",
            tool_calls=None,
            prompt_tokens=8,
            completion_tokens=12,
            cost_usd=0.0005,
        )

        mock_acompletion = AsyncMock(return_value=response)

        with patch("orchestrator.council.tools.litellm.acompletion", mock_acompletion):
            tool_executor = AsyncMock()
            messages = [{"role": "user", "content": "Hi"}]
            tools = []

            result, usage = await council_completion_with_tools(
                model="test-model",
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
            )

            assert result == "Hello! How can I help you today?"
            assert usage["prompt_tokens"] == 8
            assert usage["completion_tokens"] == 12
            assert usage["total_tokens"] == 20
            assert usage["cost_usd"] == 0.0005
            tool_executor.assert_not_awaited()
