"""Accessibility and mobile-layout regression tests (WP2).

These tests render the real application over a real fixture database in a real
browser and run axe-core against it. They assert the guarantees the WP2 hardening
established, so a future change cannot silently reintroduce them:

* no serious or critical axe-core violation on any public route;
* no horizontal page overflow at a 390px viewport;
* interactive controls meet the WCAG 2.5.5 44px touch target.

The browser layer is optional: Playwright and its Chromium build are a developer
tool, not a runtime dependency. When they are absent the module skips rather than
failing, so `python -m pytest tests/ -q` stays green on a host that has not
installed them.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from conftest_app import build_database

from oppintel.app.config import AppConfig
from oppintel.app.main import create_app

pytest.importorskip("playwright", reason="Playwright is a developer-only tool for a11y tests")
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
AXE_PATH = REPO_ROOT / "audit" / "a11y" / "axe.min.js"

#: Public routes audited. Admin-only surfaces are covered by their own tests.
ROUTES = [
    "/",
    "/opportunities",
    "/companies",
    "/markets/dfw",
    "/changes",
    "/trends",
    "/analytics",
    "/reports",
    "/how-it-works",
    "/guides",
    "/trades/commercial-hvac",
    "/commercial-construction-leads",
    "/signin",
    "/signup",
]

#: Routes where the mobile feed/nav chrome is most at risk of regressing.
TOUCH_ROUTES = ["/", "/opportunities", "/companies", "/changes", "/signin", "/signup"]

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}

#: axe must be clean at both ends of the responsive range: the desktop header and
#: the mobile drawer each only exist at one width, so a single viewport misses half.
AXE_VIEWPORTS = {"mobile": MOBILE, "desktop": DESKTOP}


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Serve the app on an ephemeral port for the duration of the module.

    A real socket is required because axe-core evaluates computed styles that a
    Flask test client (which never fetches /static) cannot reproduce.
    """
    db_path = tmp_path_factory.mktemp("a11y") / "a11y.db"
    db = build_database(db_path)
    db.close()

    cfg = AppConfig(database_path=db_path, secret_key="a11y-secret", debug=True)
    application = create_app(cfg)
    application.config["TESTING"] = True

    server = make_server("127.0.0.1", 0, application)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _axe(page) -> dict:
    page.add_script_tag(content=AXE_PATH.read_text())
    return page.evaluate("async () => await axe.run(document)")


@pytest.mark.parametrize("viewport_name", list(AXE_VIEWPORTS))
@pytest.mark.parametrize("route", ROUTES)
def test_no_serious_or_critical_axe_violations(live_server, browser, route, viewport_name):
    context = browser.new_context(viewport=AXE_VIEWPORTS[viewport_name])
    try:
        page = context.new_page()
        page.goto(live_server + route, wait_until="networkidle")
        result = _axe(page)
        blocking = [
            (v["id"], v["impact"], v["nodes"][0]["target"])
            for v in result["violations"]
            if v["impact"] in ("serious", "critical")
        ]
        assert blocking == [], f"{route} @{viewport_name} has serious/critical a11y violations: {blocking}"
    finally:
        context.close()


@pytest.mark.parametrize("route", ROUTES)
def test_no_horizontal_overflow_at_mobile_width(live_server, browser, route):
    context = browser.new_context(viewport=MOBILE)
    try:
        page = context.new_page()
        page.goto(live_server + route, wait_until="networkidle")
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth"
        )
        assert not overflow, f"{route} scrolls horizontally at 390px"
    finally:
        context.close()


@pytest.mark.parametrize("route", TOUCH_ROUTES)
def test_touch_targets_meet_44px(live_server, browser, route):
    context = browser.new_context(viewport=MOBILE, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_server + route, wait_until="networkidle")
        small = page.evaluate(
            """() => {
              const out = [];
              document.querySelectorAll('button, input, select, textarea, a.btn, [role=button]')
                .forEach(el => {
                  const r = el.getBoundingClientRect();
                  const cs = getComputedStyle(el);
                  if (r.width === 0 || r.height === 0) return;
                  if (cs.display === 'none' || cs.visibility === 'hidden') return;
                  if (r.height < 44) out.push({
                    tag: el.tagName.toLowerCase(),
                    cls: (el.className || '').toString().slice(0, 40),
                    h: Math.round(r.height),
                    label: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30),
                  });
                });
              return out;
            }"""
        )
        assert small == [], f"{route} has controls under 44px: {small}"
    finally:
        context.close()


def test_mobile_drawer_focus_and_aria(live_server, browser):
    """Opening the drawer must expose it and move focus inside it; closing must
    hide it and return focus to the trigger. Regression for the visibility
    transition that made focus() a no-op."""
    context = browser.new_context(viewport=MOBILE, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_server + "/", wait_until="networkidle")
        page.click("#mobile-menu-open-btn")

        opened = page.evaluate(
            """() => {
              const d = document.getElementById('mobile-nav-drawer');
              return {
                role: d.getAttribute('role'),
                modal: d.getAttribute('aria-modal'),
                hidden: d.getAttribute('aria-hidden'),
                visibility: getComputedStyle(d).visibility,
                focusInDrawer: d.contains(document.activeElement),
              };
            }"""
        )
        assert opened["role"] == "dialog"
        assert opened["modal"] == "true"
        assert opened["hidden"] == "false"
        assert opened["visibility"] == "visible"
        assert opened["focusInDrawer"], "focus did not move into the open drawer"

        page.click("#mobile-menu-close-btn")
        # visibility is delayed by the slide-out transition (0.25s) so the close
        # animation is visible; wait past it before asserting the hidden state.
        page.wait_for_timeout(400)
        closed = page.evaluate(
            """() => {
              const d = document.getElementById('mobile-nav-drawer');
              return {
                hidden: d.getAttribute('aria-hidden'),
                visibility: getComputedStyle(d).visibility,
                active: document.activeElement.id,
              };
            }"""
        )
        assert closed["hidden"] == "true"
        assert closed["visibility"] == "hidden"
        assert closed["active"] == "mobile-menu-open-btn", "focus did not return to the trigger"
    finally:
        context.close()

