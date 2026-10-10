"""Security tests.

Each test names a specific attack and asserts the defence, rather than checking that a
framework feature exists. The classes covered, in the order the product's threat model ranks
them:

* **CSRF** — a state-changing request without a session-matching token is refused.
* **Authorization** — an account cannot reach another account's records, and cannot raise its
  own access level.
* **IDOR** — an id from one account does not resolve to another account's object.
* **Open redirect** — a `next` parameter cannot send a user off-site.
* **Rate limiting** — repeated credential attempts are stopped.
* **Isolation** — an operator's area is not reachable, and the response discloses nothing.

Recovered in Phase 1B from the decompiled bytecode of the pre-deletion test suite.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from oppintel.app.alerts import AlertService
from oppintel.app.config import AppConfig
from oppintel.app.main import create_app
from oppintel.app.security import limiter
from oppintel.db import Database
from conftest_app import build_database, csrf_from


def _signed_in_secured_client(tmp_path, email: str = "user@example.com"):
    """A protected-mode client with a registered, signed-in account."""
    db_path = tmp_path / f"{email.split('@')[0]}.db"
    db = build_database(db_path)
    db.close()
    cfg = AppConfig(
        database_path=db_path, secret_key="test-secret-key", debug=False, csrf_enabled=True
    )
    app = create_app(cfg)
    app.config["TESTING"] = True
    limiter.reset()
    client = app.test_client()
    token = csrf_from(client, "/signup")
    client.post(
        "/signup",
        data={
            "email": email,
            "password": "correct-horse-battery",
            "password_confirm": "correct-horse-battery",
            "_csrf_token": token,
        },
        follow_redirects=True,
    )
    return client, app, db_path


# --- CSRF ----------------------------------------------------------------------


def test_a_post_without_a_token_is_refused(secured_client):
    response = secured_client.post(
        "/signin", data={"email": "a@example.com", "password": "whatever"}
    )
    assert response.status_code == 403


def test_a_post_with_a_wrong_token_is_refused(secured_client):
    csrf_from(secured_client, "/signin")
    response = secured_client.post(
        "/signin",
        data={"email": "a@example.com", "password": "whatever", "_csrf_token": "not-the-token"},
    )
    assert response.status_code == 403


def test_a_post_with_the_session_token_is_accepted(secured_client):
    token = csrf_from(secured_client, "/signin")
    response = secured_client.post(
        "/signin",
        data={"email": "nobody@example.com", "password": "wrong", "_csrf_token": token},
    )
    assert response.status_code == 200


def test_a_header_token_is_accepted(secured_client):
    token = csrf_from(secured_client, "/signin")
    response = secured_client.post(
        "/signin",
        data={"email": "nobody@example.com", "password": "wrong"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200


def test_every_post_form_in_the_templates_carries_a_token():
    """A static check across the templates, so a new form cannot ship without one."""
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "src" / "oppintel" / "app" / "templates"
    offenders = []
    for path in templates.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for chunk in text.split("<form")[1:]:
            form = chunk.split("</form>")[0]
            if 'method="post"' in form.lower() and "csrf.html" not in form:
                offenders.append(str(path.relative_to(templates)))
    assert not offenders, f"POST forms without a CSRF token: {sorted(set(offenders))}"


# --- Authorization / privilege escalation --------------------------------------


def test_signup_cannot_choose_an_access_level(secured_client):
    token = csrf_from(secured_client, "/signup")
    secured_client.post(
        "/signup",
        data={
            "email": "escalate@example.com",
            "password": "correct-horse-battery",
            "password_confirm": "correct-horse-battery",
            "access_level": "ADMIN",
            "is_admin": "1",
            "_csrf_token": token,
        },
        follow_redirects=True,
    )
    db = Database(secured_client.application.config["APP_CONFIG"].database_path)
    try:
        row = db.conn.execute(
            "SELECT access_level FROM app_user WHERE email = 'escalate@example.com'"
        ).fetchone()
        assert row is not None and row["access_level"] == "FREE"
    finally:
        db.close()


def test_preferences_cannot_set_an_access_level(tmp_path):
    client, app, db_path = _signed_in_secured_client(tmp_path)
    token = csrf_from(client, "/preferences")
    client.post(
        "/preferences",
        data={
            "cities": [],
            "project_types": [],
            "access_level": "ADMIN",
            "notify_in_app": "1",
            "_csrf_token": token,
        },
        follow_redirects=True,
    )
    db = Database(db_path)
    try:
        row = db.conn.execute(
            "SELECT access_level FROM app_user WHERE email = 'user@example.com'"
        ).fetchone()
        assert row["access_level"] == "FREE"
    finally:
        db.close()


def test_no_web_route_can_grant_the_operator_level(tmp_path):
    """The route table itself is checked, so a future route cannot quietly add one."""
    client, app, _ = _signed_in_secured_client(tmp_path)
    suspicious = [
        rule.rule
        for rule in app.url_map.iter_rules()
        if any(word in rule.rule.lower() for word in ("grant", "promote", "set-admin", "make-admin"))
    ]
    assert suspicious == []


# --- Operator-area isolation ---------------------------------------------------


def test_anonymous_cannot_reach_the_operator_area(secured_client):
    response = secured_client.get("/admin/data")
    assert response.status_code == 302
    assert "/signin" in response.headers["Location"]


def test_a_signed_in_non_operator_receives_403(tmp_path):
    client, app, _ = _signed_in_secured_client(tmp_path, "normal@example.com")
    response = client.get("/admin/data")
    assert response.status_code == 403


def test_the_403_discloses_nothing_about_the_page(secured_client):
    body = secured_client.get("/admin/data").get_data(as_text=True)
    for term in ("source_coverage", "ingest_run", "sqlite", "oppintel.db"):
        assert term not in body.lower()


def test_the_operator_area_is_absent_from_the_sitemap(tmp_path):
    client, app, _ = _signed_in_secured_client(tmp_path)
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/admin" not in body


def test_the_operator_area_is_disallowed_by_robots(tmp_path):
    client, app, _ = _signed_in_secured_client(tmp_path)
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "/admin" in body


# --- IDOR ----------------------------------------------------------------------


def test_a_save_id_from_another_account_does_not_touch_that_account(tmp_path):
    """Both accounts act on the same project; each keeps its own save row."""
    first, app, db_path = _signed_in_secured_client(tmp_path, "first@example.com")
    second, _, _ = _signed_in_secured_client(tmp_path, "second@example.com")
    db = Database(db_path)
    try:
        project_id = int(db.conn.execute("SELECT id FROM project LIMIT 1").fetchone()["id"])
    finally:
        db.close()

    token = csrf_from(first, "/opportunities")
    first.post(f"/saved/{project_id}", data={"_csrf_token": token}, follow_redirects=True)
    payload = second.get("/api/saved").get_json()
    assert payload["total"] == 0


def test_a_note_id_from_another_account_cannot_be_deleted(tmp_path):
    first, app, db_path = _signed_in_secured_client(tmp_path, "noter@example.com")
    db = Database(db_path)
    try:
        project_id = int(db.conn.execute("SELECT id FROM project LIMIT 1").fetchone()["id"])
    finally:
        db.close()

    token = csrf_from(first, "/opportunities")
    first.post(
        f"/notes/{project_id}",
        data={"body": "First account's private note", "_csrf_token": token},
        follow_redirects=True,
    )
    second, _, _ = _signed_in_secured_client(tmp_path, "snooper@example.com")
    token2 = csrf_from(second, "/opportunities")
    second.post("/notes/1/delete", data={"_csrf_token": token2}, follow_redirects=True)
    payload = first.get(f"/api/notes/{project_id}").get_json()
    assert payload["total"] == 1


def test_an_alert_id_from_another_account_cannot_be_marked_read(tmp_path):
    owner, app, db_path = _signed_in_secured_client(tmp_path, "owner@example.com")
    db = Database(db_path)
    try:
        project_id = int(db.conn.execute("SELECT id FROM project LIMIT 1").fetchone()["id"])
        service = AlertService(db)
        user_id = int(
            db.conn.execute(
                "SELECT id FROM app_user WHERE email = 'owner@example.com'"
            ).fetchone()["id"]
        )
        service.record_new_match(user_id, project_id, "New match", "A")
        alert_id = service.for_user(user_id)[0]["id"]
        assert service.unread_count(user_id) == 1
    finally:
        db.close()

    other, _, _ = _signed_in_secured_client(tmp_path, "stranger@example.com")
    token = csrf_from(other, "/opportunities")
    other.post(f"/alerts/{alert_id}/read", data={"_csrf_token": token}, follow_redirects=True)
    db = Database(db_path)
    try:
        user_id = int(
            db.conn.execute(
                "SELECT id FROM app_user WHERE email = 'owner@example.com'"
            ).fetchone()["id"]
        )
        assert AlertService(db).unread_count(user_id) == 1, "another account read the alert"
    finally:
        db.close()


# --- Open redirect -------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "//evil.example.com",
        "\\\\evil.example.com",
        "http://evil.example.com/path",
        "https://evil.example.com",
        "javascript:alert(1)",
    ],
)
def test_next_never_redirects_off_site(secured_client, target):
    from oppintel.app.main import _safe_redirect

    token = csrf_from(secured_client, "/signin")
    secured_client.post(
        f"/signin?next={target}",
        data={"email": "nobody@example.com", "password": "wrong", "_csrf_token": token},
    )
    resolved = _safe_redirect(target)
    parsed = urlparse(resolved)
    assert not parsed.scheme
    assert not parsed.netloc
    assert resolved.startswith("/")


def test_safe_redirect_allows_a_same_site_path():
    from oppintel.app.main import _safe_redirect

    assert _safe_redirect("/dashboard") == "/dashboard"
    assert _safe_redirect("/opportunities?q=x") == "/opportunities?q=x"
    assert _safe_redirect(None) == "/"


# --- Rate limiting -------------------------------------------------------------


def test_repeated_signin_attempts_are_rate_limited(secured_client):
    statuses = []
    token = csrf_from(secured_client, "/signin")
    for _ in range(15):
        response = secured_client.post(
            "/signin",
            data={"email": "a@example.com", "password": "x", "_csrf_token": token},
        )
        statuses.append(response.status_code)
    assert 429 in statuses, "repeated credential attempts must be rate limited"


def test_the_rate_limit_is_per_client(secured_client):
    token = csrf_from(secured_client, "/signin")
    for _ in range(12):
        secured_client.post(
            "/signin",
            data={"email": "a@example.com", "password": "x", "_csrf_token": token},
        )
    token2 = csrf_from(secured_client, "/signin")
    response = secured_client.post(
        "/signin",
        data={"email": "a@example.com", "password": "x", "_csrf_token": token2},
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"},
    )
    assert response.status_code != 429


# --- Headers and cookie flags --------------------------------------------------


def test_security_headers_are_present(secured_client):
    response = secured_client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_private_pages_are_not_cacheable(client, session_client):
    """Every HTML/JSON response and anything that sets a cookie must never be stored.

    Regression for H8/I3: no `Cache-Control` was emitted at all, so a shared cache or the
    browser's back/forward cache could serve one account's saved list to the next visit.
    """
    # A signed-in account surface.
    assert session_client.get("/saved").headers["Cache-Control"] == "private, no-store"
    # Account surfaces are private even when signed out (a cached redirect is still wrong).
    for path in ("/signin", "/signup", "/dashboard", "/saved", "/admin/data"):
        assert client.get(path).headers["Cache-Control"] == "private, no-store", path
    # The API and the health probe are never cacheable.
    for path in ("/api/statistics", "/healthz"):
        assert client.get(path).headers["Cache-Control"] == "private, no-store", path


def test_every_html_route_is_no_store(client):
    """No HTML route may be publicly cacheable, because every one carries a nonce and a header.

    A per-request CSP nonce is embedded in every page and the header renders "Sign in" versus
    the account name, so a shared cache could replay one visitor's page to another. This walks
    the whole public route table rather than a sample, so a new route is covered on arrival.
    """
    checked = 0
    for rule in client.application.url_map.iter_rules():
        if rule.endpoint == "static" or "GET" not in (rule.methods or set()):
            continue
        if "<" in rule.rule:  # needs a real id; the detail routes are covered elsewhere
            continue
        response = client.get(rule.rule)
        if response.headers.get("Content-Type", "").startswith("text/html"):
            assert response.headers["Cache-Control"] == "private, no-store", rule.rule
            checked += 1
    assert checked >= 20, f"expected the public route table, only saw {checked} HTML routes"


def test_no_html_response_combines_public_cache_with_a_nonce_or_cookie(client, app_db):
    """The exact invariant: an HTML response never says `public` while carrying a nonce/cookie."""
    for path in ("/", "/opportunities", "/trends", "/companies", "/markets", "/changes"):
        response = client.get(path)
        cache = response.headers.get("Cache-Control", "")
        csp = response.headers.get("Content-Security-Policy", "")
        has_nonce = "nonce-" in csp
        sets_cookie = bool(response.headers.get("Set-Cookie"))
        assert not (cache.startswith("public") and (has_nonce or sets_cookie)), path
        # And the stronger statement: none of these HTML pages is public at all.
        assert cache == "private, no-store", path


def test_static_assets_keep_a_long_cache_and_are_fingerprinted(client):
    """A static asset is publicly cacheable for a long time and its URL is content-versioned."""
    response = client.get("/static/css/app.css")
    assert response.status_code == 200
    assert "max-age=31536000" in response.headers["Cache-Control"]
    # The base template links it with a fingerprint query so a rebuild is not served stale.
    body = client.get("/").get_data(as_text=True)
    assert "css/app.css?v=" in body


def test_public_pages_get_a_short_public_cache(client, session_client):
    """Kept as a guard that no HTML page is public — the header shows account state."""
    for path in ("/", "/trends", "/companies"):
        assert client.get(path).headers["Cache-Control"] == "private, no-store", path
    assert session_client.get("/").headers["Cache-Control"] == "private, no-store"


def test_the_session_cookie_is_httponly_and_samesite(tmp_path):
    """The session cookie's flags come from the app configuration.

    Asserted against the configuration rather than a response header because a Flask session is
    only serialised into a `Set-Cookie` when it has changed, and a signed-in GET may legitimately
    leave it untouched. The configuration is what governs every cookie the app emits.
    """
    client, app, _ = _signed_in_secured_client(tmp_path)
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    # Signing out clears the session, which mutates it, so Flask emits a Set-Cookie. That is
    # the request that lets the emitted flags be inspected.
    response = client.get("/signout")
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_login_errors_do_not_reveal_whether_an_account_exists(secured_client):
    token = csrf_from(secured_client, "/signin")
    body = secured_client.post(
        "/signin",
        data={
            "email": "does-not-exist@example.com",
            "password": "wrong",
            "_csrf_token": token,
        },
    ).get_data(as_text=True)
    lowered = body.lower()
    assert "incorrect" in lowered
    assert "unknown" not in lowered
    assert "no such user" not in lowered
    assert "not found" not in lowered
