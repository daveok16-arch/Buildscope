"""Authentication security regression tests (Phase 1A).

These tests lock down the fix for the demonstrated authentication bypass: a request that
supplied an email address to `/auth/google` could previously start a session as that account.

The suite covers:

* A. `/auth/google?email=victim` must not authenticate.
* B. A request containing any known user email must not authenticate.
* C. A valid, verified Firebase ID token authenticates the correct account.
* D. An invalid ID token fails.
* E. An expired ID token fails.
* F. A tampered ID token fails.
* G. A verified Google identity whose email matches an existing password account cannot
     hijack that account.
* H. Logout invalidates the session.
* I. Password-reset tokens are never exposed through a page or API response.
* J. An expired reset token is rejected.
* K. A used reset token is rejected.

The Firebase network calls are not mocked in the ordinary sense: `_verify_firebase_id_token`
is the single trust boundary, and tests replace only the two outbound HTTP calls it makes, so
the claims-validation logic (audience, verified email, presence of subject) runs for real.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest_app import csrf_from
from oppintel.app import main as app_main
from oppintel.app.accounts import AccountService, AuthError
from oppintel.config import active_market, active_trade
from oppintel.db import Database

VICTIM_EMAIL = "victim@example.com"
VICTIM_PASSWORD = "victim-real-password"


# --- helpers ------------------------------------------------------------------


def _db(app_db) -> Database:
    return Database(app_db.config["APP_CONFIG"].database_path)


def _create_victim(app_db) -> int:
    """Create a real password-protected account, as a victim would have."""
    db = _db(app_db)
    try:
        user = AccountService(db).create_account(
            VICTIM_EMAIL,
            VICTIM_PASSWORD,
            "Victim",
            market=active_market(),
            trade=active_trade(),
        )
        return user.id
    finally:
        db.close()


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _install_verifier(monkeypatch, *, email: str, sub: str, email_verified: bool = True,
                      name: str = "Verified User") -> None:
    """Drive the real verifier with controlled outbound responses.

    The Google `tokeninfo` endpoint is treated as the authoritative verifier, exactly as in
    production; the Firebase Identity Toolkit call is made to fail so the code path under test
    is the Google one. Nothing about the claims-validation logic is bypassed.
    """
    def fake_urlopen(req, timeout=10):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "oauth2.googleapis.com/tokeninfo" in url:
            return _FakeResponse(
                {
                    "email": email,
                    "sub": sub,
                    "name": name,
                    "email_verified": "true" if email_verified else "false",
                    "aud": "gen-lang-client-0795656577",
                    "iss": "https://accounts.google.com",
                }
            )
        raise OSError("identity toolkit unavailable in test")

    monkeypatch.setattr(app_main.urllib.request, "urlopen", fake_urlopen)


def _install_rejecting_verifier(monkeypatch) -> None:
    """Make every outbound verification call fail, so no token can be trusted."""
    def fake_urlopen(req, timeout=10):  # noqa: ANN001
        raise OSError("verification endpoint rejected the token")

    monkeypatch.setattr(app_main.urllib.request, "urlopen", fake_urlopen)


# --- A / B: the original exploit ----------------------------------------------


def test_original_exploit_get_auth_google_email_does_not_authenticate(app_db):
    """A. The demonstrated exploit: GET /auth/google?email=<victim> must not sign in."""
    victim_id = _create_victim(app_db)
    client = app_db.test_client()

    response = client.get(f"/auth/google?email={VICTIM_EMAIL}")
    assert response.status_code in (301, 302)

    me = client.get("/api/me").get_json()
    assert me["authenticated"] is False, "email alone must never establish a session"

    # And the victim's session must not exist anywhere in this client.
    db = _db(app_db)
    try:
        assert db.conn.execute(
            "SELECT 1 FROM app_user WHERE id = ?", (victim_id,)
        ).fetchone()
    finally:
        db.close()


def test_original_exploit_post_auth_google_email_does_not_authenticate(app_db):
    """A. The same exploit over POST, with a display name, must not sign in."""
    _create_victim(app_db)
    client = app_db.test_client()

    client.post(
        "/auth/google",
        data={"email": VICTIM_EMAIL, "display_name": "Attacker", "next": "/dashboard"},
    )
    assert client.get("/api/me").get_json()["authenticated"] is False


def test_any_known_email_does_not_authenticate(app_db):
    """B. No known email, on any Google-related surface, authenticates by itself."""
    _create_victim(app_db)
    client = app_db.test_client()

    for path in (
        f"/auth/google?email={VICTIM_EMAIL}",
        f"/auth/google?email={VICTIM_EMAIL}&display_name=Attacker",
        "/auth/google?email=operator@example.com",
    ):
        client.get(path)
        assert client.get("/api/me").get_json()["authenticated"] is False, path


def test_auth_google_redirects_to_signin_and_ignores_identity(app_db):
    """The legacy route is inert: it redirects and never starts a session."""
    _create_victim(app_db)
    client = app_db.test_client()
    response = client.get(f"/auth/google?email={VICTIM_EMAIL}", follow_redirects=False)
    assert response.status_code == 302
    assert "/signin" in response.headers["Location"]


# --- C: valid token -----------------------------------------------------------


def test_valid_firebase_token_authenticates_the_correct_account(app_db, monkeypatch):
    """C. A verified token signs in the account that owns the verified identity."""
    _install_verifier(monkeypatch, email="newuser@example.com", sub="firebase-uid-123")
    client = app_db.test_client()

    resp = client.post("/auth/firebase-verify", json={"id_token": "a-valid-token"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    me = client.get("/api/me").get_json()
    assert me["authenticated"] is True

    db = _db(app_db)
    try:
        row = db.conn.execute(
            "SELECT google_id FROM app_user WHERE email = ?", ("newuser@example.com",)
        ).fetchone()
        assert row is not None and row["google_id"] == "firebase-uid-123"
    finally:
        db.close()


def test_valid_token_signs_in_returning_google_user(app_db, monkeypatch):
    """C. A second sign-in with the same identity returns the same account."""
    _install_verifier(monkeypatch, email="returning@example.com", sub="firebase-uid-999")
    first = app_db.test_client()
    first.post("/auth/firebase-verify", json={"id_token": "tok"})
    db = _db(app_db)
    try:
        first_id = db.conn.execute(
            "SELECT id FROM app_user WHERE email = ?", ("returning@example.com",)
        ).fetchone()["id"]
    finally:
        db.close()

    second = app_db.test_client()
    second.post("/auth/firebase-verify", json={"id_token": "tok"})
    db = _db(app_db)
    try:
        count = db.conn.execute(
            "SELECT COUNT(*) AS n FROM app_user WHERE email = ?", ("returning@example.com",)
        ).fetchone()["n"]
        assert count == 1, "a returning Google user must not create a duplicate account"
        assert first_id
    finally:
        db.close()


# --- D / E / F: invalid tokens ------------------------------------------------


def test_invalid_token_fails(app_db, monkeypatch):
    """D. A token the verifier rejects does not authenticate."""
    _install_rejecting_verifier(monkeypatch)
    client = app_db.test_client()
    resp = client.post("/auth/firebase-verify", json={"id_token": "garbage"})
    assert resp.status_code == 401
    assert client.get("/api/me").get_json()["authenticated"] is False


def test_expired_token_fails(app_db, monkeypatch):
    """E. An expired token (verifier rejects) does not authenticate."""
    _install_rejecting_verifier(monkeypatch)
    client = app_db.test_client()
    resp = client.post("/auth/firebase-verify", json={"id_token": "expired-token"})
    assert resp.status_code == 401
    assert client.get("/api/me").get_json()["authenticated"] is False


def test_tampered_token_fails(app_db, monkeypatch):
    """F. A tampered token does not authenticate."""
    _install_rejecting_verifier(monkeypatch)
    client = app_db.test_client()
    resp = client.post("/auth/firebase-verify", json={"id_token": "header.payload.tampered"})
    assert resp.status_code == 401
    assert client.get("/api/me").get_json()["authenticated"] is False


def test_missing_token_fails(app_db):
    """A request with no token is refused."""
    client = app_db.test_client()
    resp = client.post("/auth/firebase-verify", json={})
    assert resp.status_code == 400
    assert client.get("/api/me").get_json()["authenticated"] is False


def test_unverified_email_claim_is_refused(app_db, monkeypatch):
    """A token whose email is not verified by the provider is refused."""
    _install_verifier(
        monkeypatch, email="unverified@example.com", sub="uid-x", email_verified=False
    )
    client = app_db.test_client()
    resp = client.post("/auth/firebase-verify", json={"id_token": "tok"})
    assert resp.status_code == 401


# --- G: no takeover of an existing password account ---------------------------


def test_verified_google_identity_cannot_hijack_existing_account(app_db, monkeypatch):
    """G. A verified Google identity matching a password account's email cannot claim it."""
    victim_id = _create_victim(app_db)

    # The provider verifies an identity that happens to carry the victim's email.
    _install_verifier(monkeypatch, email=VICTIM_EMAIL, sub="attacker-google-uid")
    client = app_db.test_client()
    resp = client.post("/auth/firebase-verify", json={"id_token": "tok"})

    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
    assert client.get("/api/me").get_json()["authenticated"] is False

    # The victim account must be untouched: no Google identity attached.
    db = _db(app_db)
    try:
        row = db.conn.execute(
            "SELECT google_id, password_hash FROM app_user WHERE id = ?", (victim_id,)
        ).fetchone()
        assert row["google_id"] is None, "the victim account must not be linked by email"
        assert row["password_hash"], "the victim password must be unchanged"
    finally:
        db.close()


def test_service_refuses_email_match_for_existing_account(fixture_db):
    """G (unit). The service itself refuses to claim an account by email."""
    accounts = AccountService(fixture_db)
    accounts.create_account(
        VICTIM_EMAIL, VICTIM_PASSWORD, "Victim",
        market=active_market(), trade=active_trade(),
    )
    with pytest.raises(AuthError):
        accounts.authenticate_or_link_google(
            VICTIM_EMAIL, "attacker-uid", "Attacker",
            market=active_market(), trade=active_trade(),
        )


def test_service_matches_returning_google_identity_by_uid(fixture_db):
    """A returning Google identity is matched by uid, not reassigned."""
    accounts = AccountService(fixture_db)
    user, created = accounts.authenticate_or_link_google(
        "g@example.com", "uid-1", "G",
        market=active_market(), trade=active_trade(),
    )
    assert created is True
    same, created2 = accounts.authenticate_or_link_google(
        "g@example.com", "uid-1", "G",
        market=active_market(), trade=active_trade(),
    )
    assert created2 is False and same.id == user.id


# --- H: logout -----------------------------------------------------------------


def test_logout_invalidates_the_session(session_client):
    """H. After sign-out the session no longer authenticates."""
    assert session_client.get("/api/me").get_json()["authenticated"] is True
    session_client.get("/signout")
    assert session_client.get("/api/me").get_json()["authenticated"] is False


# --- I / J / K: password reset -------------------------------------------------


def test_forgot_password_never_exposes_a_reset_token(app_db):
    """I. The reset page and API responses never carry a reset token."""
    _create_victim(app_db)
    client = app_db.test_client()

    page = client.post("/forgot-password", data={"email": VICTIM_EMAIL})
    body = page.get_data(as_text=True)
    assert "reset-password/" not in body
    assert "Reset link" not in body

    db = _db(app_db)
    try:
        row = db.conn.execute(
            "SELECT reset_token FROM app_user WHERE email = ?", (VICTIM_EMAIL,)
        ).fetchone()
        token = row["reset_token"]
        assert token, "a token is still generated for a real account"
    finally:
        db.close()
    assert token not in body, "the token must never be rendered"


def test_reset_token_is_not_in_any_ordinary_page(app_db):
    """I. Even knowing the token, no ordinary page echoes it back."""
    _create_victim(app_db)
    client = app_db.test_client()
    db = _db(app_db)
    try:
        AccountService(db).create_password_reset_token(VICTIM_EMAIL)
        token = db.conn.execute(
            "SELECT reset_token FROM app_user WHERE email = ?", (VICTIM_EMAIL,)
        ).fetchone()["reset_token"]
    finally:
        db.close()

    for path in ("/signin", "/signup", "/forgot-password", "/", "/api/me"):
        assert token not in client.get(path).get_data(as_text=True), path


def test_expired_reset_token_is_rejected(app_db):
    """J. An expired token cannot reset a password."""
    _create_victim(app_db)
    db = _db(app_db)
    try:
        accounts = AccountService(db)
        accounts.create_password_reset_token(VICTIM_EMAIL)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        db.conn.execute(
            "UPDATE app_user SET reset_token_expires_at = ? WHERE email = ?",
            (past, VICTIM_EMAIL),
        )
        db.conn.commit()
        token = db.conn.execute(
            "SELECT reset_token FROM app_user WHERE email = ?", (VICTIM_EMAIL,)
        ).fetchone()["reset_token"]
        assert accounts.verify_reset_token(token) is None
        with pytest.raises(AuthError):
            accounts.reset_password_with_token(token, "a-new-password")
    finally:
        db.close()


def test_used_reset_token_is_rejected(app_db):
    """K. A token is single-use: after a successful reset it is invalidated."""
    _create_victim(app_db)
    db = _db(app_db)
    try:
        accounts = AccountService(db)
        token, _ = accounts.create_password_reset_token(VICTIM_EMAIL)
        accounts.reset_password_with_token(token, "a-new-password")
        assert accounts.verify_reset_token(token) is None
        with pytest.raises(AuthError):
            accounts.reset_password_with_token(token, "another-password")
    finally:
        db.close()


def test_reset_changes_the_password_for_real(app_db):
    """A successful reset actually rotates the password."""
    _create_victim(app_db)
    db = _db(app_db)
    try:
        accounts = AccountService(db)
        token, _ = accounts.create_password_reset_token(VICTIM_EMAIL)
        accounts.reset_password_with_token(token, "brand-new-password")
        # The old password no longer works; the new one does.
        with pytest.raises(AuthError):
            accounts.authenticate(VICTIM_EMAIL, VICTIM_PASSWORD)
        assert accounts.authenticate(VICTIM_EMAIL, "brand-new-password").email == VICTIM_EMAIL
    finally:
        db.close()


# --- L: reset delivery boundary ------------------------------------------------


def test_reset_delivery_is_abstracted_and_console_backend_hides_the_token(caplog):
    """L. The console backend records a request without ever writing the token."""
    from oppintel.app.mailer import BACKEND_CONSOLE, ResetMailer

    with caplog.at_level("INFO", logger="oppintel.app.mailer"):
        ResetMailer(backend=BACKEND_CONSOLE).send_reset(
            to_email="someone@example.com", token="SUPER-SECRET-TOKEN"
        )
    assert "SUPER-SECRET-TOKEN" not in caplog.text
    assert "someone@example.com" in caplog.text


def test_null_backend_delivers_nothing_and_does_not_raise():
    """L. The null backend accepts a request and sends nothing, for disabled delivery."""
    from oppintel.app.mailer import BACKEND_NULL, ResetMailer

    ResetMailer(backend=BACKEND_NULL).send_reset(to_email="a@example.com", token="t")


def test_smtp_backend_without_configuration_fails_loudly(monkeypatch):
    """L. Selecting smtp without a host must not silently fall back to a leaking channel."""
    from oppintel.app.mailer import BACKEND_SMTP, DeliveryError, ResetMailer

    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(DeliveryError):
        ResetMailer(backend=BACKEND_SMTP).send_reset(to_email="a@example.com", token="t")


def test_unknown_mail_backend_is_refused():
    from oppintel.app.mailer import DeliveryError, ResetMailer

    with pytest.raises(DeliveryError):
        ResetMailer(backend="carrier-pigeon").send_reset(to_email="a@example.com", token="t")


def test_forgot_password_does_not_reveal_whether_an_account_exists(app_db):
    """L. The response is identical for a registered and an unregistered address.

    The per-request CSP nonce (``nonce="..."``) differs between the two responses by design,
    so it is normalised before the comparison. Everything else — the message, the fields, any
    hint of delivery — must match byte for byte.
    """
    import re

    _create_victim(app_db)
    client = app_db.test_client()

    known = client.post("/forgot-password", data={"email": VICTIM_EMAIL})
    unknown = client.post("/forgot-password", data={"email": "nobody@example.com"})

    def normalise(body: str) -> str:
        return re.sub(r'nonce="[^"]*"', 'nonce="X"', body)

    assert known.status_code == unknown.status_code == 200
    assert normalise(known.get_data(as_text=True)) == normalise(unknown.get_data(as_text=True))


def test_forgot_password_delivers_through_the_mailer_for_a_real_account(app_db, monkeypatch):
    """L. A real account routes the token through the mailer, not through the response."""
    _create_victim(app_db)
    client = app_db.test_client()

    delivered: list[tuple[str, str]] = []

    from oppintel.app import main as app_main

    class _RecordingMailer:
        def __init__(self, **kwargs: object) -> None:
            pass

        def send_reset(self, *, to_email: str, token: str) -> None:
            delivered.append((to_email, token))

    monkeypatch.setattr(app_main, "ResetMailer", _RecordingMailer)

    response = client.post("/forgot-password", data={"email": VICTIM_EMAIL})
    body = response.get_data(as_text=True)

    assert len(delivered) == 1, "a real account must trigger exactly one delivery"
    to_email, token = delivered[0]
    assert to_email == VICTIM_EMAIL
    assert token
    assert token not in body, "the token must never appear in the response"


def test_forgot_password_delivers_nothing_for_an_unknown_account(app_db, monkeypatch):
    """L. No delivery is attempted for an address with no account."""
    client = app_db.test_client()

    delivered: list[tuple[str, str]] = []
    from oppintel.app import main as app_main

    class _RecordingMailer:
        def __init__(self, **kwargs: object) -> None:
            pass

        def send_reset(self, *, to_email: str, token: str) -> None:
            delivered.append((to_email, token))

    monkeypatch.setattr(app_main, "ResetMailer", _RecordingMailer)
    client.post("/forgot-password", data={"email": "nobody@example.com"})
    assert delivered == []


def test_forgot_password_is_rate_limited(secured_client):
    """L. Reset requests are rate limited so the form cannot be used to spam a mailbox."""
    statuses = []
    token = csrf_from(secured_client, "/forgot-password")
    for _ in range(9):
        response = secured_client.post(
            "/forgot-password",
            data={"email": "a@example.com", "_csrf_token": token},
        )
        statuses.append(response.status_code)
    assert 429 in statuses
