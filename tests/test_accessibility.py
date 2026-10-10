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
TOUCH_ROUTES = ["/", "/opportunities", "/companies", "/trends", "/changes", "/signin", "/signup"]

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}

#: The widths the responsive layout must survive. 360 is the narrowest common phone,
#: 820 the tablet breakpoint where the filter sidebar is still collapsed.
OVERFLOW_VIEWPORTS = [360, 390, 430, 820, 1440]

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


#: Rules that a "moderate" impact still makes a real defect: heading order,
#: landmarks and dialog roles. The serious/critical gate above does not catch
#: these, and a sidebar <h3> that precedes the results <h2> is exactly the kind
#: of moderate regression that slipped through at desktop width.
STRUCTURAL_RULES = (
    "heading-order",
    "landmark-one-main",
    "landmark-unique",
    "region",
    "aria-allowed-role",
    "aria-dialog-name",
)


@pytest.mark.parametrize("viewport_name", list(AXE_VIEWPORTS))
@pytest.mark.parametrize("route", ROUTES)
def test_structural_axe_rules_are_clean(live_server, browser, route, viewport_name):
    context = browser.new_context(viewport=AXE_VIEWPORTS[viewport_name])
    try:
        page = context.new_page()
        page.goto(live_server + route, wait_until="networkidle")
        result = _axe(page)
        offenders = [
            (v["id"], v["nodes"][0]["target"])
            for v in result["violations"]
            if v["id"] in STRUCTURAL_RULES
        ]
        assert offenders == [], f"{route} @{viewport_name} structural a11y: {offenders}"
    finally:
        context.close()


@pytest.mark.parametrize("width", OVERFLOW_VIEWPORTS)
@pytest.mark.parametrize("route", ROUTES)
def test_no_horizontal_overflow(live_server, browser, route, width):
    """No route may scroll horizontally at any supported width.

    The offending elements are collected so a failure names the cause rather than
    only reporting that the page is too wide.
    """
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        page.goto(live_server + route, wait_until="networkidle")
        offenders = page.evaluate(
            """() => {
              const inner = window.innerWidth;
              const doc = document.documentElement;
              if (doc.scrollWidth <= inner) return [];
              const clipped = el => {
                let n = el.parentElement;
                while (n && n !== doc) {
                  if (getComputedStyle(n).overflowX !== 'visible') return true;
                  n = n.parentElement;
                }
                return false;
              };
              const out = [];
              document.querySelectorAll('*').forEach(el => {
                const cs = getComputedStyle(el);
                // position:fixed elements (the off-canvas drawer) are outside the
                // document flow and cannot widen scrollWidth; a clipped ancestor
                // already absorbs the child. Neither is a real cause.
                if (cs.position === 'fixed' || cs.visibility === 'hidden' || clipped(el)) return;
                const r = el.getBoundingClientRect();
                if (r.right > inner + 0.5 || r.left < -0.5) {
                  out.push({
                    tag: el.tagName.toLowerCase(),
                    cls: (el.className || '').toString().slice(0, 50),
                    left: Math.round(r.left),
                    right: Math.round(r.right),
                    w: Math.round(r.width),
                  });
                }
              });
              return out.slice(0, 8);
            }"""
        )
        assert offenders == [], (
            f"{route} scrolls horizontally at {width}px; offenders={offenders}"
        )
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
    hide it and return focus to the trigger.

    The drawer is a modal dialog: while open the page behind it is inert and body
    scroll is locked. The hidden state is expressed with `visibility:hidden` (so it
    is out of the accessibility tree without a stale `aria-hidden`), not the old
    hand-toggled `aria-hidden` attribute."""
    context = browser.new_context(viewport=MOBILE, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_server + "/", wait_until="networkidle")
        page.click("#mobile-menu-open-btn")

        opened = page.evaluate(
            """() => {
              const d = document.getElementById('mobile-nav-drawer');
              const main = document.getElementById('main');
              return {
                role: d.getAttribute('role'),
                modal: d.getAttribute('aria-modal'),
                visibility: getComputedStyle(d).visibility,
                focusInDrawer: d.contains(document.activeElement),
                bodyLocked: getComputedStyle(document.body).overflow === 'hidden',
                backgroundInert: main.hasAttribute('inert'),
                expanded: document.getElementById('mobile-menu-open-btn').getAttribute('aria-expanded'),
              };
            }"""
        )
        assert opened["role"] == "dialog"
        assert opened["modal"] == "true"
        assert opened["visibility"] == "visible"
        assert opened["focusInDrawer"], "focus did not move into the open drawer"
        assert opened["bodyLocked"], "body scroll was not locked while the drawer is open"
        assert opened["backgroundInert"], "content behind the drawer is not inert"
        assert opened["expanded"] == "true"

        page.click("#mobile-menu-close-btn")
        # visibility is delayed by the slide-out transition (0.25s) so the close
        # animation is visible; wait past it before asserting the hidden state.
        page.wait_for_timeout(400)
        closed = page.evaluate(
            """() => {
              const d = document.getElementById('mobile-nav-drawer');
              return {
                visibility: getComputedStyle(d).visibility,
                active: document.activeElement.id,
                bodyLocked: getComputedStyle(document.body).overflow === 'hidden',
                backgroundInert: document.getElementById('main').hasAttribute('inert'),
              };
            }"""
        )
        assert closed["visibility"] == "hidden"
        assert closed["active"] == "mobile-menu-open-btn", "focus did not return to the trigger"
        assert not closed["bodyLocked"], "body scroll stayed locked after close"
        assert not closed["backgroundInert"], "background stayed inert after close"
    finally:
        context.close()


def _assert_focus_stays_inside(page, panel_id: str, presses: int = 20) -> None:
    """Press Tab (and Shift+Tab) and assert focus never leaves the dialog."""
    for i in range(presses):
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "p => document.getElementById(p).contains(document.activeElement)", panel_id
        )
        assert inside, f"Tab #{i + 1} moved focus outside #{panel_id}"
    for i in range(presses):
        page.keyboard.press("Shift+Tab")
        inside = page.evaluate(
            "p => document.getElementById(p).contains(document.activeElement)", panel_id
        )
        assert inside, f"Shift+Tab #{i + 1} moved focus outside #{panel_id}"


@pytest.mark.parametrize("route,panel", [
    ("/opportunities", "filter-sidebar-panel"),
    ("/companies", "company-filter-panel"),
])
def test_filter_sheet_traps_tab_focus(live_server, browser, route, panel):
    """E1: Tab and Shift+Tab wrap inside the open filter sheet; the page behind it
    is inert and body scroll is locked."""
    context = browser.new_context(viewport=MOBILE, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_server + route, wait_until="networkidle")
        page.click("#mobile-filter-btn")

        state = page.evaluate(
            """p => {
              const el = document.getElementById(p);
              return {
                role: el.getAttribute('role'),
                modal: el.getAttribute('aria-modal'),
                bodyLocked: getComputedStyle(document.body).overflow === 'hidden',
                headerInert: document.querySelector('header.site-header').hasAttribute('inert'),
                barInert: document.querySelector('.mobile-filter-bar').hasAttribute('inert'),
              };
            }""",
            panel,
        )
        assert state["role"] == "dialog", f"{route}: open sheet is not a dialog"
        assert state["modal"] == "true", f"{route}: open sheet is not aria-modal"
        assert state["bodyLocked"], f"{route}: body scroll not locked"
        assert state["headerInert"], f"{route}: header behind the sheet is not inert"
        assert state["barInert"], f"{route}: filter bar behind the sheet is not inert"

        _assert_focus_stays_inside(page, panel)

        page.keyboard.press("Escape")
        after = page.evaluate(
            """p => ({
              headerInert: document.querySelector('header.site-header').hasAttribute('inert'),
              bodyLocked: getComputedStyle(document.body).overflow === 'hidden',
              active: document.activeElement.id,
            })""",
            panel,
        )
        assert not after["headerInert"], f"{route}: background stayed inert after Escape"
        assert not after["bodyLocked"], f"{route}: body stayed locked after Escape"
        assert after["active"] == "mobile-filter-btn", f"{route}: focus did not return"
    finally:
        context.close()


def test_mobile_drawer_traps_tab_focus(live_server, browser):
    """E1: Tab and Shift+Tab wrap inside the open nav drawer on a filter route."""
    context = browser.new_context(viewport=MOBILE, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_server + "/opportunities", wait_until="networkidle")
        page.click("#mobile-menu-open-btn")
        _assert_focus_stays_inside(page, "mobile-nav-drawer")
    finally:
        context.close()


@pytest.mark.parametrize("route,panel", [
    ("/opportunities", "filter-sidebar-panel"),
    ("/companies", "company-filter-panel"),
])
def test_mobile_filter_sheet_opens_and_closes(live_server, browser, route, panel):
    """The filter form is a bottom sheet on mobile: hidden until the "Filters"
    button opens it, then pinned so results are not pushed below the form."""
    context = browser.new_context(viewport=MOBILE, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_server + route, wait_until="networkidle")

        hidden = page.evaluate(
            "p => getComputedStyle(document.getElementById(p)).display",
            panel,
        )
        assert hidden == "none", f"{route}: filter panel visible before opening"

        page.click("#mobile-filter-btn")
        opened = page.evaluate(
            """p => {
              const el = document.getElementById(p);
              const r = el.getBoundingClientRect();
              return {
                display: getComputedStyle(el).display,
                position: getComputedStyle(el).position,
                bottom: Math.round(window.innerHeight - r.bottom),
                focusInside: el.contains(document.activeElement),
                expanded: document.getElementById('mobile-filter-btn').getAttribute('aria-expanded'),
                bodyOpen: document.body.classList.contains('filter-sheet-open'),
              };
            }""",
            panel,
        )
        assert opened["display"] != "none", f"{route}: filter panel did not open"
        assert opened["position"] == "fixed", f"{route}: filter panel is not a bottom sheet"
        assert abs(opened["bottom"]) <= 2, f"{route}: filter panel not pinned to the bottom"
        assert opened["expanded"] == "true"
        assert opened["bodyOpen"]
        assert opened["focusInside"], f"{route}: focus did not move into the sheet"

        page.keyboard.press("Escape")
        closed = page.evaluate(
            """p => ({
              display: getComputedStyle(document.getElementById(p)).display,
              expanded: document.getElementById('mobile-filter-btn').getAttribute('aria-expanded'),
              active: document.activeElement.id,
            })""",
            panel,
        )
        assert closed["display"] == "none", f"{route}: Escape did not close the sheet"
        assert closed["expanded"] == "false"
        assert closed["active"] == "mobile-filter-btn", f"{route}: focus did not return"
    finally:
        context.close()


def test_filter_date_presets_and_native_inputs(live_server, browser):
    """The permit-date filter offers native date inputs and one-click presets that
    submit without JavaScript (they are ordinary links)."""
    context = browser.new_context(viewport=MOBILE, has_touch=True)
    try:
        page = context.new_page()
        page.goto(live_server + "/opportunities", wait_until="networkidle")
        page.click("#mobile-filter-btn")

        dates = page.eval_on_selector_all(
            "input[type=date]", "els => els.map(e => e.id)"
        )
        assert set(dates) == {"date_from", "date_to"}, f"unexpected date inputs: {dates}"

        presets = page.eval_on_selector_all(
            ".date-presets a", "els => els.map(e => e.textContent.trim())"
        )
        assert presets == ["Last 7 days", "Last 30 days", "This year"], presets

        href = page.get_attribute(".date-presets a", "href")
        assert "date_from=" in href and "/opportunities?" in href, href
    finally:
        context.close()

