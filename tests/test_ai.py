"""Tests for ai.py — the Agent is mocked, no LLM calls happen."""

from unittest.mock import MagicMock

import ai
from models import Tag


def test_extract_tags_empty_returns_empty():
    assert ai.extract_tags("") == []


def test_extract_tags_whitespace_returns_empty():
    assert ai.extract_tags("   \n\t  ") == []


def test_extract_tags_calls_agent(monkeypatch):
    expected = [Tag(technique="Rubber Guard", position="Bottom Guard")]
    fake_result = MagicMock()
    fake_result.output = expected

    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_result
    monkeypatch.setattr(ai, "_agent", fake_agent)

    out = ai.extract_tags("rubber guard from bottom")
    assert out == expected
    fake_agent.run_sync.assert_called_once_with("rubber guard from bottom")


def test_get_agent_lazy_construction_and_cache(monkeypatch):
    """_get_agent constructs the Agent on first call, caches on subsequent calls."""
    monkeypatch.setattr(ai, "_agent", None)
    first = ai._get_agent()
    second = ai._get_agent()
    assert first is second  # cached


def test_extract_tags_multiple_tags(monkeypatch):
    expected = [
        Tag(technique="Triangle Choke", position="Bottom Guard"),
        Tag(technique="Armbar", position="Bottom Guard"),
    ]
    fake_result = MagicMock()
    fake_result.output = expected
    fake_agent = MagicMock()
    fake_agent.run_sync.return_value = fake_result
    monkeypatch.setattr(ai, "_agent", fake_agent)

    out = ai.extract_tags("triangle into armbar from closed guard")
    assert len(out) == 2
