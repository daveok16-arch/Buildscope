import os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp2"
os.makedirs(OUT, exist_ok=True)
VIEWPORTS = {"mobile": {"width": 390, "height": 844},
             "tablet": {"width": 820, "height": 1180},
             "desktop": {"width": 1440, "height": 900}}
ROUTES = {"companies": "/companies", "opportunities": "/opportunities", "home": "/",
          "guides": "/guides", "markets_dfw": "/markets/dfw"}

with sync_playwright() as p:
    b = p.chromium.launch()
    for vname, vp in VIEWPORTS.items():
        ctx = b.new_context(viewport=vp)
        page = ctx.new_page()
        for rname, route in ROUTES.items():
            page.goto(BASE + route, wait_until="networkidle")
            page.screenshot(path=f"{OUT}/{vname}__{rname}.png", full_page=True)
        ctx.close()
    # drawer open state
    ctx = b.new_context(viewport=VIEWPORTS["mobile"], has_touch=True)
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="networkidle")
    page.click("#mobile-menu-open-btn")
    page.wait_for_timeout(400)
    state = page.evaluate("""() => {
      const d = document.getElementById('mobile-nav-drawer');
      return {ariaHidden: d.getAttribute('aria-hidden'), role: d.getAttribute('role'),
              ariaModal: d.getAttribute('aria-modal'),
              focus: document.activeElement ? (document.activeElement.id||document.activeElement.tagName) : null,
              visibility: getComputedStyle(d).visibility};
    }""")
    page.screenshot(path=f"{OUT}/mobile__drawer_open.png")
    print("drawer open:", state)
    page.click("#mobile-menu-close-btn")
    page.wait_for_timeout(400)
    state2 = page.evaluate("""() => {
      const d = document.getElementById('mobile-nav-drawer');
      return {ariaHidden: d.getAttribute('aria-hidden'), visibility: getComputedStyle(d).visibility,
              focus: document.activeElement ? (document.activeElement.id||document.activeElement.tagName) : null};
    }""")
    print("drawer closed:", state2)
    ctx.close()
    b.close()
print("wrote", OUT)
