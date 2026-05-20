"""Test-wide setup. Runs before any test module is imported."""

import os

# pydantic-ai's Anthropic provider validates the key at construction time.
# Tests mock the agent so no real call goes out — but the env var must exist
# at import time. A dummy value is fine.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
