"""Pydantic AI tag extraction.

Turns a short free-text note about a training block into a list of
structured Tag objects. The Agent's output is bound to `list[Tag]` —
Pydantic AI handles the LLM call, JSON parsing, and validation. If
the model returns malformed data, Pydantic AI retries automatically.
"""

from __future__ import annotations

import logging
import time

from pydantic_ai import Agent

from models import Tag

log = logging.getLogger("strain.ai")

_SYSTEM_PROMPT = """You are an expert in Brazilian Jiu-Jitsu (BJJ) terminology.

Given a short note about what someone worked on in training, extract
structured technique tags.

For each distinct technique mentioned, output one Tag:
- technique: the normalized canonical name (e.g. "Rubber Guard",
  "Triangle Choke", "X-Guard", "Spider Guard", "Kimura")
- position: the BJJ position involved (e.g. "Bottom Guard",
  "Side Control", "Mount", "Back Control", "Open Guard", "Half Guard",
  "Standing", "Turtle")

Rules:
- Normalize naming and capitalization: "rubber gaurd" → "Rubber Guard"
- If multiple techniques are mentioned, return multiple tags
- If position isn't explicit, infer the most likely position for that technique
- Return an empty list if no clear technique is mentioned
"""

_agent: Agent | None = None


def _get_agent() -> Agent:
    """Lazily construct the Agent. Defers ANTHROPIC_API_KEY validation
    until first call, so module import works in environments where the
    key is set after import (e.g. via dotenv) or never set (e.g. unit
    tests that mock _agent directly).
    """
    global _agent
    if _agent is None:
        _agent = Agent(
            "anthropic:claude-haiku-4-5",
            output_type=list[Tag],
            system_prompt=_SYSTEM_PROMPT,
        )
    return _agent


def extract_tags(notes_raw: str) -> list[Tag]:
    """Extract structured Tag objects from a free-text note.

    Empty/whitespace input short-circuits to [] — no API call made.
    """
    if not notes_raw.strip():
        log.debug("ai.extract_tags skip: empty note")
        return []
    log.debug("ai.extract_tags start chars=%d", len(notes_raw))
    t0 = time.perf_counter()
    result = _get_agent().run_sync(notes_raw)
    log.info(
        "ai.extract_tags ok tags=%d ms=%.0f",
        len(result.output), (time.perf_counter() - t0) * 1000,
    )
    return result.output
