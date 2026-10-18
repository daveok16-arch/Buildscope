"""Authentication security regression tests.

These tests lock down the authentication surface that remains: password sign-in, logout, and
password reset.

The suite covers:

* H. Logout invalidates the session.
* I. Password-reset tokens are never exposed through a page or API response.
* J. An expired reset token is rejected.
* K. A used reset token is rejected.
* L. Reset delivery is abstracted, and no backend writes the token to a page, response or log.
* M. The `smtp` transport really sends, verified against a local SMTP server (plain and STARTTLS).

The `/auth/google` bypass these tests were originally written around no longer exists: the
Google/Firebase sign-in integration was removed, so the route that accepted an email address is
gone rather than inert. The regression cannot recur through a route that is not registered.
"""

from __future__ import annotations

import base64
import shutil
import socket
import ssl
import subprocess
import threading
from datetime import datetime, timedelta, timezone

import pytest

from conftest_app import csrf_from
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
        delivers = True

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
        delivers = True

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


# --- L: the page must not promise an email the deployment cannot send ------------


def test_mailer_delivers_is_false_for_console_and_null(monkeypatch):
    """L. `delivers` is the deployment's honest capability, not an assumption."""
    from oppintel.app.mailer import BACKEND_CONSOLE, BACKEND_NULL, ResetMailer

    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert ResetMailer(backend=BACKEND_CONSOLE).delivers is False
    assert ResetMailer(backend=BACKEND_NULL).delivers is False


def test_mailer_delivers_is_false_for_smtp_without_a_relay_host(monkeypatch):
    """L. Selecting smtp is not enough: without SMTP_HOST it cannot deliver."""
    from oppintel.app.mailer import BACKEND_SMTP, ResetMailer

    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert ResetMailer(backend=BACKEND_SMTP).delivers is False


def test_mailer_delivers_is_true_for_a_configured_smtp_relay(monkeypatch):
    from oppintel.app.mailer import BACKEND_SMTP, ResetMailer

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    assert ResetMailer(backend=BACKEND_SMTP).delivers is True


def test_forgot_password_does_not_claim_an_email_when_delivery_is_off(app_db):
    """L. With no delivery channel the page must not say an email was sent.

    The default test configuration uses the `console` backend, which sends nothing, so the
    success message must not promise instructions "have been sent".
    """
    _create_victim(app_db)
    client = app_db.test_client()
    body = client.post("/forgot-password", data={"email": VICTIM_EMAIL}).get_data(as_text=True)
    assert "have been sent" not in body
    assert "not configured to send email" in body


def test_forgot_password_says_sent_only_when_a_relay_is_configured(app_db, monkeypatch):
    """L. When delivery is configured the message is the normal one."""
    from oppintel.app.mailer import BACKEND_SMTP, ResetMailer

    _create_victim(app_db)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MAIL_BACKEND", BACKEND_SMTP)
    # Replace only the transport, keeping the real `delivers` logic under test.
    monkeypatch.setattr(ResetMailer, "send_reset", lambda self, **kw: None)
    client = app_db.test_client()
    body = client.post("/forgot-password", data={"email": VICTIM_EMAIL}).get_data(as_text=True)
    assert "have been sent" in body
    assert "not configured to send email" not in body


def test_delivery_availability_is_identical_for_known_and_unknown_addresses(app_db, monkeypatch):
    """L. The delivery notice depends on configuration, never on the address.

    Even with the smtp backend unconfigured (the honest "cannot send" state), the registered and
    unregistered responses must stay byte-identical apart from the per-request CSP nonce, so the
    new copy does not reopen the enumeration oracle.
    """
    import re

    from oppintel.app.mailer import BACKEND_SMTP

    _create_victim(app_db)
    monkeypatch.setenv("MAIL_BACKEND", BACKEND_SMTP)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    client = app_db.test_client()

    known = client.post("/forgot-password", data={"email": VICTIM_EMAIL}).get_data(as_text=True)
    unknown = client.post("/forgot-password", data={"email": "nobody@example.com"}).get_data(
        as_text=True
    )

    def normalise(body: str) -> str:
        return re.sub(r'nonce="[^"]*"', 'nonce="X"', body)

    assert normalise(known) == normalise(unknown)


# --- M: the smtp transport, against a real local SMTP server ---------------------
#
# The tests above prove the *choice* of channel and the copy around it. These prove the transport
# itself: that MAIL_BACKEND=smtp actually opens a connection, negotiates STARTTLS, authenticates
# and delivers a message whose body carries the reset link. The server is a minimal stdlib
# implementation of the commands smtplib issues (EHLO/STARTTLS/AUTH/DATA/QUIT), so no dependency
# is added and nothing leaves the machine. The plain-text path is always run; the STARTTLS path
# runs when the `openssl` CLI can mint a throwaway certificate for 127.0.0.1, and is skipped
# otherwise rather than silently passing.


class _SmtpStub:
    """A one-connection-at-a-time SMTP server speaking just enough for smtplib."""

    def __init__(self, *, tls_context: ssl.SSLContext | None = None) -> None:
        self.tls_context = tls_context
        self.messages: list[str] = []
        self.auth_lines: list[str] = []
        self.used_starttls = False
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port: int = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self) -> "_SmtpStub":
        self._thread.start()
        return self

    def stop(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(10)
        reader = conn.makefile("rb")

        def send(line: str) -> None:
            conn.sendall((line + "\r\n").encode())

        try:
            send("220 stub ESMTP ready")
            in_data = False
            data: list[str] = []
            while True:
                raw = reader.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if in_data:
                    if line == ".":
                        in_data = False
                        self.messages.append("\n".join(data))
                        data = []
                        send("250 OK queued")
                    else:
                        data.append(line[1:] if line.startswith("..") else line)
                    continue
                cmd = line.split(" ", 1)[0].upper()
                if cmd == "EHLO":
                    caps = ["250-stub", "250-SIZE 10485760"]
                    if self.tls_context is not None and not self.used_starttls:
                        caps.append("250-STARTTLS")
                    caps.append("250 AUTH PLAIN LOGIN")
                    for cap in caps:
                        send(cap)
                elif cmd == "HELO":
                    send("250 stub")
                elif cmd == "STARTTLS":
                    send("220 Ready to start TLS")
                    assert self.tls_context is not None
                    conn = self.tls_context.wrap_socket(conn, server_side=True)
                    reader = conn.makefile("rb")
                    self.used_starttls = True
                elif cmd == "AUTH":
                    self.auth_lines.append(line)
                    if len(line.split()) == 2:  # mechanism only; the blob arrives next
                        send("334 ")
                        self.auth_lines.append(reader.readline().decode().strip())
                    send("235 Authentication successful")
                elif cmd in ("MAIL", "RCPT"):
                    send("250 OK")
                elif cmd == "DATA":
                    send("354 End data with <CR><LF>.<CR><LF>")
                    in_data = True
                elif cmd == "QUIT":
                    send("221 Bye")
                    return
                else:
                    send("250 OK")
        except Exception:  # noqa: BLE001 - a dropped connection must not fail the test run
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


@pytest.fixture
def smtp_stub():
    """A plain (no TLS) SMTP server on a loopback port."""
    stub = _SmtpStub().start()
    try:
        yield stub
    finally:
        stub.stop()


def _self_signed_cert(tmp_path) -> tuple[str, str] | None:
    """Mint a throwaway cert for 127.0.0.1, or None when openssl is unavailable."""
    if shutil.which("openssl") is None:
        return None
    cert = tmp_path / "smtp-cert.pem"
    key = tmp_path / "smtp-key.pem"
    result = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
            "-out", str(cert), "-days", "2", "-nodes", "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        capture_output=True,
    )
    if result.returncode != 0 or not cert.exists():
        return None
    return str(cert), str(key)


def _configure_smtp(monkeypatch, stub: _SmtpStub, *, cert: tuple[str, str] | None = None) -> None:
    monkeypatch.setenv("MAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(stub.port))
    monkeypatch.setenv("SMTP_USER", "apikey")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-secret-password")
    monkeypatch.setenv("SMTP_FROM", "no-reply@example.com")
    monkeypatch.setenv("SMTP_USE_TLS", "true" if cert else "false")
    if cert:
        # Trust exactly the throwaway CA, so verification stays on rather than being disabled.
        monkeypatch.setenv("SSL_CERT_FILE", cert[0])


def test_smtp_backend_delivers_a_message_carrying_the_reset_link(monkeypatch, smtp_stub):
    """M. The transport really sends: a message arrives whose body holds the reset link."""
    from oppintel.app.mailer import ResetMailer

    _configure_smtp(monkeypatch, smtp_stub)
    ResetMailer(backend="smtp", base_url="https://buildscope.example").send_reset(
        to_email="victim@example.com", token="TOKEN-abc123"
    )

    assert len(smtp_stub.messages) == 1, "exactly one message must be delivered"
    body = smtp_stub.messages[0]
    assert "victim@example.com" in body
    assert "https://buildscope.example/reset-password/TOKEN-abc123" in body
    assert "Reset your BuildScope password" in body


def test_smtp_backend_authenticates_with_the_configured_credentials(monkeypatch, smtp_stub):
    """M. The relay credentials reach the server (AUTH), not the URL or a log."""
    from oppintel.app.mailer import ResetMailer

    _configure_smtp(monkeypatch, smtp_stub)
    ResetMailer(backend="smtp").send_reset(to_email="victim@example.com", token="t")

    assert smtp_stub.auth_lines, "the client must authenticate when a user is configured"
    # The client uses AUTH PLAIN, recorded whole: "AUTH PLAIN <base64(user\0user\0pass)>".
    line = smtp_stub.auth_lines[-1]
    assert line.upper().startswith("AUTH PLAIN ")
    decoded = base64.b64decode(line.split(None, 2)[2]).decode("utf-8", "replace")
    assert "smtp-secret-password" in decoded


def test_smtp_backend_negotiates_starttls_when_enabled(monkeypatch, tmp_path):
    """M. With SMTP_USE_TLS on, the session is upgraded before the credentials are sent."""
    cert = _self_signed_cert(tmp_path)
    if cert is None:
        pytest.skip("openssl CLI unavailable, cannot mint a throwaway TLS certificate")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert[0], cert[1])
    stub = _SmtpStub(tls_context=context).start()
    try:
        from oppintel.app.mailer import ResetMailer

        _configure_smtp(monkeypatch, stub, cert=cert)
        ResetMailer(backend="smtp").send_reset(to_email="victim@example.com", token="TLS-TOKEN")

        assert stub.used_starttls is True, "the client must upgrade the connection"
        assert len(stub.messages) == 1
        assert "TLS-TOKEN" in stub.messages[0]
    finally:
        stub.stop()


def test_smtp_backend_raises_when_the_relay_refuses_the_connection(monkeypatch):
    """M. A dead relay surfaces as DeliveryError, not a silent success."""
    from oppintel.app.mailer import DeliveryError, ResetMailer

    # Bind then close a socket to obtain a port nothing is listening on.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    monkeypatch.setenv("MAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(dead_port))
    monkeypatch.setenv("SMTP_USE_TLS", "false")

    with pytest.raises(DeliveryError):
        ResetMailer(backend="smtp").send_reset(to_email="a@example.com", token="t")

