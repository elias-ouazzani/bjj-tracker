"""Firebase Auth — server-side ID token verification.

The browser obtains an ID token from Firebase Auth via the JS SDK
(signed by Google). The token gets posted to our backend. We cannot
trust anything that came from a browser, so we re-verify the token's
signature here using firebase-admin. The verified claims include the
user's stable `uid`, which we then use as the `user_id` on Session.
"""

from __future__ import annotations

import logging
import os

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials

log = logging.getLogger("strain.auth")


def _ensure_initialized() -> None:
    """Initialize firebase_admin if not already done. Idempotent.

    Same auth resolution as db._client(): GOOGLE_APPLICATION_CREDENTIALS
    locally, attached service account on Cloud Run.
    """
    if firebase_admin._apps:
        return
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path:
        log.info("firebase-admin init: key file at %s", cred_path)
        firebase_admin.initialize_app(credentials.Certificate(cred_path))
    else:
        log.info("firebase-admin init: Application Default Credentials "
                    "(ambient service account on Cloud Run)")
        firebase_admin.initialize_app()


def verify_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token. Returns decoded claims on success.

    The decoded claims include (at minimum):
    - uid:   stable user identifier — what we store as Session.user_id
    - email: user's verified email (when available)
    - name:  Google display name (when available)

    Raises ValueError on any verification failure — missing, malformed,
    expired, signature mismatch, etc.
    """
    if not id_token:
        raise ValueError("No token provided")
    _ensure_initialized()
    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        log.warning("verify_id_token failed: %s", e)
        raise ValueError(f"Token verification failed: {e}") from e
    log.debug("verify_id_token ok uid=%s", decoded.get("uid"))
    return decoded
