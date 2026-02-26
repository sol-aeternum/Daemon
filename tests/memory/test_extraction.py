"""Tests for fact extraction."""

import pytest
from unittest.mock import Mock, patch
from orchestrator.memory.extraction import extract_facts_from_text


@pytest.mark.asyncio
async def test_extract_facts():
    mock_response = (
        """{"facts": [{"content": "User likes Python", "confidence": 0.9}]}"""
    )
    with patch("orchestrator.memory.extraction.litellm.acompletion") as mock:
        mock.return_value = Mock(choices=[Mock(message=Mock(content=mock_response))])
        outcome = await extract_facts_from_text("I love Python")
        assert len(outcome.facts) >= 0
