"""Tests for reasoning persistence - reasoning_text and reasoning_duration_secs."""

from __future__ import annotations

import inspect
import pytest


class TestStoreLayerReasoningFields:
    """Test that store layer accepts reasoning fields."""

    def test_insert_message_accepts_reasoning_text(self):
        """Verify insert_message accepts reasoning_text parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.insert_message)
        params = list(sig.parameters.keys())

        assert "reasoning_text" in params, (
            f"insert_message should accept reasoning_text. Found params: {params}"
        )

    def test_insert_message_accepts_reasoning_duration(self):
        """Verify insert_message accepts reasoning_duration_secs parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.insert_message)
        params = list(sig.parameters.keys())

        assert "reasoning_duration_secs" in params, (
            f"insert_message should accept reasoning_duration_secs. Found params: {params}"
        )

    def test_update_message_accepts_reasoning_text(self):
        """Verify update_message accepts reasoning_text parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.update_message)
        params = list(sig.parameters.keys())

        assert "reasoning_text" in params, (
            f"update_message should accept reasoning_text. Found params: {params}"
        )

    def test_update_message_accepts_reasoning_duration(self):
        """Verify update_message accepts reasoning_duration_secs parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.update_message)
        params = list(sig.parameters.keys())

        assert "reasoning_duration_secs" in params, (
            f"update_message should accept reasoning_duration_secs. Found params: {params}"
        )


class TestReasoningFieldsInMessageDTO:
    """Test that message DTOs include reasoning fields."""

    def test_message_dto_has_reasoning_fields(self):
        """Verify MessageOut schema includes reasoning fields."""
        from orchestrator.routes.conversations import MessageOut

        # Check if the schema has reasoning fields
        # MessageOut should have reasoning_text and reasoning_duration_secs
        fields = MessageOut.model_fields

        assert "reasoning_text" in fields or "reasoning" in fields, (
            f"MessageOut should have reasoning field. Found: {list(fields.keys())}"
        )

    def test_reasoning_fields_are_optional(self):
        """Verify reasoning fields are optional (nullable)."""
        from orchestrator.routes.conversations import MessageOut

        fields = MessageOut.model_fields

        # reasoning_text should be optional (can be None for user messages)
        if "reasoning_text" in fields:
            field = fields["reasoning_text"]
            # Optional fields have None in their default or are marked optional
            assert not field.is_required(), "reasoning_text should be optional"


class TestReasoningModelField:
    """Test reasoning_model field."""

    def test_insert_message_accepts_reasoning_model(self):
        """Verify insert_message accepts reasoning_model parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.insert_message)
        params = list(sig.parameters.keys())

        assert "reasoning_model" in params, (
            f"insert_message should accept reasoning_model. Found params: {params}"
        )

    def test_update_message_accepts_reasoning_model(self):
        """Verify update_message accepts reasoning_model parameter."""
        from orchestrator.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.update_message)
        params = list(sig.parameters.keys())

        assert "reasoning_model" in params, (
            f"update_message should accept reasoning_model. Found params: {params}"
        )

    def test_history_api_returns_reasoning_model(self):
        """Verify history API schema includes reasoning_model."""
        from orchestrator.routes.conversations import MessageOut

        fields = MessageOut.model_fields

        assert "reasoning_model" in fields, (
            f"MessageOut should include reasoning_model. Found fields: {list(fields.keys())}"
        )
