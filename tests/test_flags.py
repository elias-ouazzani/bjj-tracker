"""Tests for services/flags.py — the PostHog feature-flag wrapper.

The PostHog client is never really constructed here; we monkeypatch
`_client` so no network or key is needed. The real construction in
`_client` is marked pragma: no cover (needs the posthog lib + network).
"""

import services.flags as flags


class _FakeClient:
    """Stand-in for the PostHog client."""

    def __init__(self, result=False, exc=False):
        self._result = result
        self._exc = exc
        self.calls = []

    def feature_enabled(self, key, distinct_id, person_properties=None):
        if self._exc:
            raise RuntimeError("boom")
        self.calls.append((key, distinct_id, person_properties))
        return self._result


# ---------------- env override ----------------

def test_env_override_true_wins(monkeypatch):
    monkeypatch.setenv("FLAG_TRAINING_STREAK", "1")
    # Even a client that would say False loses to the override.
    monkeypatch.setattr(flags, "_client", lambda: _FakeClient(False))
    assert flags.flag_on("training_streak", "u1") is True


def test_env_override_false_wins(monkeypatch):
    monkeypatch.setenv("FLAG_TRAINING_STREAK", "off")
    monkeypatch.setattr(flags, "_client", lambda: _FakeClient(True))
    assert flags.flag_on("training_streak", "u1") is False


# ---------------- default off ----------------

def test_default_off_when_unconfigured(monkeypatch):
    monkeypatch.delenv("FLAG_TRAINING_STREAK", raising=False)
    monkeypatch.setattr(flags, "_client", lambda: None)
    assert flags.flag_on("training_streak", "u1") is False


def test_client_unconfigured_returns_none(monkeypatch):
    """Covers the POSTHOG_API_KEY-missing branch of _client()."""
    flags._client.cache_clear()
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    assert flags._client() is None
    flags._client.cache_clear()


# ---------------- PostHog evaluation ----------------

def test_enabled_via_client_passes_email(monkeypatch):
    monkeypatch.delenv("FLAG_ANALYTICS", raising=False)
    fake = _FakeClient(True)
    monkeypatch.setattr(flags, "_client", lambda: fake)
    assert flags.flag_on("analytics", "u1", email="a@b.com") is True
    assert fake.calls == [("analytics", "u1", {"email": "a@b.com"})]


def test_disabled_via_client(monkeypatch):
    monkeypatch.delenv("FLAG_ANALYTICS", raising=False)
    monkeypatch.setattr(flags, "_client", lambda: _FakeClient(False))
    assert flags.flag_on("analytics", "u1") is False


def test_no_email_sends_no_person_properties(monkeypatch):
    monkeypatch.delenv("FLAG_X", raising=False)
    fake = _FakeClient(True)
    monkeypatch.setattr(flags, "_client", lambda: fake)
    assert flags.flag_on("x", "u1") is True
    assert fake.calls == [("x", "u1", None)]


def test_client_exception_defaults_off(monkeypatch):
    monkeypatch.delenv("FLAG_X", raising=False)
    monkeypatch.setattr(flags, "_client", lambda: _FakeClient(exc=True))
    assert flags.flag_on("x", "u1") is False
