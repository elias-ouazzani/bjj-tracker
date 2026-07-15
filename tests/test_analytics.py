"""Tests for analytics.py — no real PostHog client, no network calls.

Analytics is a side-channel: the guarantees worth testing are (1) it stays
off unless a key is configured, and (2) it never raises into the app.
"""

from unittest.mock import MagicMock

import pytest

import analytics


@pytest.fixture(autouse=True)
def reset_analytics(monkeypatch):
    """Start each test with fresh module state and no env config."""
    monkeypatch.setattr(analytics, "_client", None)
    monkeypatch.delenv("POSTHOG_KEY", raising=False)
    monkeypatch.delenv("POSTHOG_HOST", raising=False)


# ------------- _get_client -------------

def test_disabled_without_key():
    """No POSTHOG_KEY -> disabled."""
    assert analytics._get_client() is None


def test_disabled_with_non_phc_key(monkeypatch):
    """A key that isn't a project key (phc_) is ignored."""
    monkeypatch.setenv("POSTHOG_KEY", "phx_personal_key")
    assert analytics._get_client() is None


def test_disabled_when_posthog_not_installed(monkeypatch):
    """If the posthog package is missing, analytics stays off."""
    monkeypatch.setattr(analytics, "Posthog", None)
    monkeypatch.setenv("POSTHOG_KEY", "phc_test")
    assert analytics._get_client() is None


def test_enabled_with_valid_key(monkeypatch):
    """A phc_ key builds a client from POSTHOG_KEY/HOST."""
    fake_ctor = MagicMock(return_value="CLIENT")
    monkeypatch.setattr(analytics, "Posthog", fake_ctor)
    monkeypatch.setenv("POSTHOG_KEY", "phc_test")
    monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.com")

    assert analytics._get_client() == "CLIENT"
    fake_ctor.assert_called_once_with(
        project_api_key="phc_test", host="https://eu.i.posthog.com"
    )


def test_default_host_is_us(monkeypatch):
    """Without POSTHOG_HOST we default to the US region."""
    fake_ctor = MagicMock(return_value="CLIENT")
    monkeypatch.setattr(analytics, "Posthog", fake_ctor)
    monkeypatch.setenv("POSTHOG_KEY", "phc_test")

    analytics._get_client()
    _, kwargs = fake_ctor.call_args
    assert kwargs["host"] == "https://us.i.posthog.com"


def test_client_built_only_once(monkeypatch):
    """The client is cached after the first build."""
    fake_ctor = MagicMock(return_value="CLIENT")
    monkeypatch.setattr(analytics, "Posthog", fake_ctor)
    monkeypatch.setenv("POSTHOG_KEY", "phc_test")

    analytics._get_client()
    analytics._get_client()

    assert fake_ctor.call_count == 1


def test_disabled_state_is_cached(monkeypatch):
    """A disabled result is remembered; env changes after the fact are ignored."""
    assert analytics._get_client() is None  # no key -> caches disabled
    monkeypatch.setenv("POSTHOG_KEY", "phc_late")
    assert analytics._get_client() is None


# ------------- capture -------------

def test_capture_noop_when_disabled():
    """capture() with analytics off does nothing and doesn't raise."""
    analytics.capture("evt", "user1", {"x": 1})  # no key set -> no-op


def test_capture_requires_distinct_id(monkeypatch):
    """No distinct_id -> no-op even when enabled."""
    client = MagicMock()
    monkeypatch.setattr(analytics, "_client", client)
    analytics.capture("evt", "", {"x": 1})
    client.capture.assert_not_called()


def test_capture_forwards_to_client(monkeypatch):
    """Event, distinct_id and properties are passed straight through."""
    client = MagicMock()
    monkeypatch.setattr(analytics, "_client", client)
    analytics.capture("session_logged", "u1", {"discipline": "bjj"})
    client.capture.assert_called_once_with(
        "session_logged", distinct_id="u1", properties={"discipline": "bjj"}
    )


def test_capture_defaults_properties_to_empty(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(analytics, "_client", client)
    analytics.capture("evt", "u1")
    client.capture.assert_called_once_with("evt", distinct_id="u1", properties={})


def test_capture_swallows_client_errors(monkeypatch):
    """A failing PostHog call must never propagate into the app."""
    client = MagicMock()
    client.capture.side_effect = RuntimeError("network down")
    monkeypatch.setattr(analytics, "_client", client)
    analytics.capture("evt", "u1", {})  # must not raise
