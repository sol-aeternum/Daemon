"""Tests for conversation summarization."""

import pytest
from unittest.mock import patch, AsyncMock
from orchestrator.memory.summarization import generate_summary, should_summarize


@pytest.mark.asyncio
async def test_generate_summary():
    mock_summary = "Test summary. Open: none"
    with patch(
        "orchestrator.memory.summarization.litellm.acompletion", new_callable=AsyncMock
    ) as mock:
        mock.return_value.choices = [
            type(
                "obj",
                (object,),
                {"message": type("msg", (object,), {"content": mock_summary})()},
            )()
        ]
        result = await generate_summary([{"role": "user", "content": "Hello"}])
        assert "Open:" in result
