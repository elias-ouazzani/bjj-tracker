"""Tests for auth.py — firebase_admin is mocked, no real init or network."""

from unittest.mock import MagicMock, patch

import pytest

import auth


class TestVerifyIdToken:
    @pytest.fixture(autouse=True)
    def _stub_init(self, monkeypatch):
        """Skip the firebase_admin init step for token-verification tests."""
        monkeypatch.setattr(auth, "_ensure_initialized", lambda: None)

    def test_returns_decoded(self):
        fake_decoded = {"uid": "abc123", "email": "user@example.com", "name": "User Name"}
        with patch.object(auth.firebase_auth, "verify_id_token", return_value=fake_decoded):
            result = auth.verify_id_token("real-looking-token")
        assert result == fake_decoded

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No token provided"):
            auth.verify_id_token("")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="No token provided"):
            auth.verify_id_token(None)  # type: ignore[arg-type]

    def test_invalid_raises(self):
        with patch.object(auth.firebase_auth, "verify_id_token", side_effect=Exception("expired")):
            with pytest.raises(ValueError, match="Token verification failed"):
                auth.verify_id_token("expired-token")


class TestEnsureInitialized:
    """Direct tests of the init helper — these DON'T stub it out."""

    def test_with_creds_env(self, monkeypatch):
        monkeypatch.setattr(auth.firebase_admin, "_apps", {})
        fake_init = MagicMock()
        fake_cert = MagicMock(return_value="cert-obj")
        monkeypatch.setattr(auth.firebase_admin, "initialize_app", fake_init)
        monkeypatch.setattr(auth.credentials, "Certificate", fake_cert)
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/key.json")

        auth._ensure_initialized()

        fake_cert.assert_called_once_with("/path/key.json")
        fake_init.assert_called_once_with("cert-obj")

    def test_default_credentials(self, monkeypatch):
        monkeypatch.setattr(auth.firebase_admin, "_apps", {})
        fake_init = MagicMock()
        monkeypatch.setattr(auth.firebase_admin, "initialize_app", fake_init)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        auth._ensure_initialized()
        fake_init.assert_called_once_with()

    def test_skips_if_already_initialized(self, monkeypatch):
        monkeypatch.setattr(auth.firebase_admin, "_apps", {"[DEFAULT]": object()})
        fake_init = MagicMock()
        monkeypatch.setattr(auth.firebase_admin, "initialize_app", fake_init)

        auth._ensure_initialized()
        fake_init.assert_not_called()
