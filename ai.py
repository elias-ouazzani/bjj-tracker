"""Pydantic AI tag extraction.

Turns a short free-text note about a training block into a list of
structured Tag objects. The Agent's output is bound to `list[Tag]` —
Pydantic AI handles the LLM call, JSON parsing, and validation. If
the model returns malformed data, Pydantic AI retries automatically.
"""

from __future__ import annotations

from pydantic_ai import Agent

from models import Tag

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

_agent = Agent(
    "anthropic:claude-haiku-4-5",
    output_type=list[Tag],
    system_prompt=_SYSTEM_PROMPT,
)


def extract_tags(notes_raw: str) -> list[Tag]:
    """Extract structured Tag objects from a free-text note.

    Empty/whitespace input short-circuits to [] — no API call made.
    """
    if not notes_raw.strip():
        return []
    return _agent.run_sync(notes_raw).output
