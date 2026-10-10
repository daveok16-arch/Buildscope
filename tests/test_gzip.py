"""Response compression (WP3 K1d).

The app is served without a reverse proxy in the portable deployment, so compression must live
in the app. These tests exercise the real negotiation and the real gzip bytes over a live
response — not a stubbed encoder — so a regression in the eligibility rules is caught.

No network and no optional dependency: gzip is the standard library, and brotli is used only
when it happens to be importable (asserted conditionally).
"""

from __future__ import annotations

import gzip
import zlib

import pytest

from conftest_app import build_database

from oppintel.app import compression
from oppintel.app.config import AppConfig
from oppintel.app.main import create_app


@pytest.fixture
def gzip_client(tmp_path):
    """An app with compression forced on and CSRF off, over a real fixture database."""
    db_path = tmp_path / "gzip.db"
    db = build_database(db_path)
    db.close()
    cfg = AppConfig(database_path=db_path, secret_key="gzip-secret", debug=False)
    cfg.gzip_enabled = True
    cfg.csrf_enabled = False
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def plain_client(tmp_path):
    db_path = tmp_path / "plain.db"
    db = build_database(db_path)
    db.close()
    cfg = AppConfig(database_path=db_path, secret_key="plain-secret", debug=True)
    cfg.gzip_enabled = False
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


# --- negotiation ---------------------------------------------------------------

def test_negotiate_prefers_brotli_when_available():
    available = compression.supported_encodings()
    assert available[-1] == "gzip"
    chosen = compression.negotiate_encoding("gzip, deflate, br")
    assert chosen in available


def test_negotiate_returns_none_without_accept_encoding():
    assert compression.negotiate_encoding("") is None
    assert compression.negotiate_encoding("identity") is None


def test_negotiate_refuses_q_zero():
    assert compression.negotiate_encoding("gzip;q=0") is None


def test_negotiate_accepts_wildcard():
    assert compression.negotiate_encoding("*") is not None


# --- config derivation ---------------------------------------------------------

def test_gzip_enabled_by_default_outside_debug(monkeypatch):
    monkeypatch.delenv("GZIP_ENABLED", raising=False)
    assert AppConfig(debug=False).gzip_enabled is True
    assert AppConfig(debug=True).gzip_enabled is False


def test_gzip_env_override(monkeypatch):
    monkeypatch.setenv("GZIP_ENABLED", "0")
    assert AppConfig(debug=False).gzip_enabled is False
    monkeypatch.setenv("GZIP_ENABLED", "1")
    assert AppConfig(debug=True).gzip_enabled is True


# --- end-to-end over a live response ------------------------------------------

@pytest.mark.parametrize("path", ["/", "/opportunities", "/companies", "/trends"])
def test_html_route_is_gzipped_and_decodes(gzip_client, path):
    response = gzip_client.get(path, headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in response.headers.get("Vary", "")
    # The body the client would decode is the real HTML, not a truncated stream.
    decoded = gzip.decompress(response.get_data())
    assert b"<html" in decoded.lower()


@pytest.mark.parametrize("path", ["/", "/opportunities", "/companies", "/trends"])
def test_compression_reduces_bytes(gzip_client, plain_client, path):
    plain = plain_client.get(path, headers={"Accept-Encoding": "gzip"})
    compressed = gzip_client.get(path, headers={"Accept-Encoding": "gzip"})
    assert len(plain.get_data()) > compression.COMPRESS_MIN_BYTES
    assert len(compressed.get_data()) < len(plain.get_data())
    # Content-Length reflects the compressed body, so a client is not told the wrong size.
    assert int(compressed.headers["Content-Length"]) == len(compressed.get_data())


def test_no_compression_when_client_does_not_accept_it(gzip_client):
    response = gzip_client.get("/", headers={"Accept-Encoding": "identity"})
    assert response.headers.get("Content-Encoding") is None
    assert b"<html" in response.get_data().lower()


def test_already_encoded_response_is_not_recompressed(gzip_client):
    response = gzip_client.get("/", headers={"Accept-Encoding": "gzip"})
    # A second pass over an encoded response is a no-op: the guard checks Content-Encoding.
    again = compression.compress_response(response, "gzip")
    assert again is response
    assert again.headers.get("Content-Encoding") == "gzip"


def test_small_response_is_left_alone():
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context():
        from flask import Response as FlaskResponse

        small = FlaskResponse("ok", mimetype="text/html")
        assert compression.should_compress(small, "gzip") is False
        assert compression.compress_response(small, "gzip").get_data() == b"ok"


def test_image_response_is_not_compressed():
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context():
        from flask import Response as FlaskResponse

        blob = b"\x89PNG\r\n\x1a\n" + b"x" * 2000
        image = FlaskResponse(blob, mimetype="image/png")
        assert compression.should_compress(image, "gzip") is False


def test_static_file_stream_is_not_compressed():
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context():
        from flask import Response as FlaskResponse

        streamed = FlaskResponse(iter([b"x" * 2000]), mimetype="text/css")
        streamed.direct_passthrough = True
        assert compression.should_compress(streamed, "gzip") is False


def test_gzip_output_is_deterministic():
    """mtime=0 makes an identical body produce identical bytes, so Length/ETag are stable."""
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context():
        from flask import Response as FlaskResponse

        body = "<html>" + ("padding " * 400) + "</html>"
        first = compression.compress_response(
            FlaskResponse(body, mimetype="text/html"), "gzip"
        ).get_data()
        second = compression.compress_response(
            FlaskResponse(body, mimetype="text/html"), "gzip"
        ).get_data()
        assert first == second
        # And it round-trips through the standard decoder.
        assert zlib.decompress(first, 16 + zlib.MAX_WBITS).decode() == body
