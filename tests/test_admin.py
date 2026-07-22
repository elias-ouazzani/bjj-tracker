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


# ---- domain (@atheal.com) entries ----

def test_domain_entry_matches_anyone_on_domain(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "@atheal.com")
    assert is_admin("elias.ouazzani@atheal.com") is True
    assert is_admin("someone.else@atheal.com") is True
    assert is_admin("HASSAN@Atheal.com") is True  # case-insensitive


def test_domain_entry_rejects_other_domains(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "@atheal.com")
    assert is_admin("outsider@gmail.com") is False
    # must not match on substring — a lookalike domain is rejected
    assert is_admin("me@notatheal.com") is False
    assert is_admin("me@atheal.com.evil.com") is False


def test_domain_and_exact_entries_mix(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "@atheal.com, guest@gmail.com")
    assert is_admin("staff@atheal.com") is True   # domain
    assert is_admin("guest@gmail.com") is True     # exact
    assert is_admin("other@gmail.com") is False


def test_malformed_email_without_at(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "@atheal.com")
    assert is_admin("notanemail") is False
