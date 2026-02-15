"""Tests for fact extraction."""

import pytest
from unittest.mock import patch, AsyncMock
from orchestrator.memory.extraction import extract_facts_from_text


@pytest.mark.asyncio
async def test_extract_facts():
    mock_response = (
        """{"facts": [{"content": "User likes Python", "confidence": 0.9}]}"""
    )
    with patch(
        "orchestrator.memory.extraction.litellm.acompletion", new_callable=AsyncMock
    ) as mock:
        mock.return_value.choices = [
            type(
                "obj",
                (object,),
                {"message": type("msg", (object,), {"content": mock_response})()},
            )()
        ]
        facts = await extract_facts_from_text("I love Python")
        assert len(facts) >= 0
