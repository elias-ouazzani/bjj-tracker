"""Tests for services/admin.py — the /admin allow-list gate."""

from services.admin import is_admin


def test_no_email_is_not_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "a@x.com")
    assert is_admin("") is False
    assert is_admin(None) is False


def test_unset_env_nobody_is_admin(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    assert is_admin("a@x.com") is False


def test_email_in_list_is_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "a@x.com, b@x.com")
    assert is_admin("a@x.com") is True
    assert is_admin("b@x.com") is True


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Admin@X.com")
    assert is_admin("admin@x.com") is True
    assert is_admin("  ADMIN@X.COM  ") is True


def test_email_not_in_list(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "a@x.com")
    assert is_admin("intruder@x.com") is False
