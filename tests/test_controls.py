"""WP3 M1 — custom filter controls (kill the native black dropdowns).

These assert the *markup contract* the controls island relies on, so the island and the
templates cannot drift: the filter selects live inside a `[data-bs-control]` scope, the
progressive-enhancement islands are loaded (vendored, nonced, deferred), and a no-JS fallback
keeps the mobile filter panel reachable. They do not run a browser; the interactive behaviour
(keyboard, bottom sheet) is covered by audit/m1_probe.py and the accessibility suite.
"""

from __future__ import annotations

from conftest_app import build_database

from oppintel.app.config import AppConfig
from oppintel.app.main import create_app


def _client(tmp_path):
    db_path = tmp_path / "m1.db"
    build_database(db_path).close()
    cfg = AppConfig(database_path=db_path, secret_key="m1-secret", debug=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


def _html(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    return resp.get_data(as_text=True)


def test_directory_filter_selects_are_scoped_for_enhancement(tmp_path):
    html = _html(_client(tmp_path), "/opportunities")
    assert "data-bs-control" in html
    # the five filter selects sit inside the scope the island upgrades
    scope = html.split("data-bs-control", 1)[1].split("</form>", 1)[0]
    for name in ("classification", "city", "project_type", "procurement_status", "sort"):
        assert f'name="{name}"' in scope, name


def test_companies_filter_selects_are_scoped(tmp_path):
    html = _html(_client(tmp_path), "/companies")
    assert "data-bs-control" in html
    scope = html.split("data-bs-control", 1)[1].split("</form>", 1)[0]
    assert 'name="role"' in scope
    assert 'name="city"' in scope


def test_motion_and_controls_islands_are_loaded_vendored_and_deferred(tmp_path):
    html = _html(_client(tmp_path), "/opportunities")
    assert "vendor/motion/motion.min.js" in html
    assert "js/motion.js" in html
    assert "js/controls.js" in html
    # every island script is deferred and carries a nonce (strict CSP)
    for src in ("vendor/motion/motion.min.js", "js/motion.js", "js/controls.js"):
        idx = html.index(src)
        tag = html[html.rindex("<script", 0, idx):html.index(">", idx) + 1]
        assert "defer" in tag, src
        assert "nonce=" in tag, src


def test_no_js_fallback_keeps_mobile_filters_reachable(tmp_path):
    # With JS off the sidebar would otherwise be display:none on mobile; the <noscript> block
    # must exist to reveal it and hide the inoperable toggle.
    html = _html(_client(tmp_path), "/opportunities")
    assert "<noscript>" in html
    noscript = html.split("<noscript>", 1)[1].split("</noscript>", 1)[0]
    assert ".filter-sidebar" in noscript
    assert "display: block" in noscript


def test_public_css_has_combobox_tokens(tmp_path):
    # The branded listbox styles must ship, or the enhanced controls would render unstyled.
    html = _html(_client(tmp_path), "/opportunities")
    css_href = None
    for line in html.splitlines():
        if "css/app.css" in line and "<link" in line:
            css_href = line.split('href="', 1)[1].split('"', 1)[0]
    assert css_href
    css = _client(tmp_path).get(css_href.split("?", 1)[0]).get_data(as_text=True)
    assert ".bs-combobox" in css
    assert ".bs-listbox" in css
    assert "prefers-reduced-motion" in css
