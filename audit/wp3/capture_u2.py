"""U2 live evidence: on each of the 7 routes, count visible native <select> (expect 0) and
capture the branded combobox open at the founder's 720. Local demo server only (read-only)."""
import json
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"
ROUTES = ["/opportunities", "/companies", "/changes", "/analytics", "/reports", "/signin", "/signup"]

VISIBLE_SELECT_JS = """() => {
  const vis = [];
  document.querySelectorAll('select').forEach(s => {
    const r = s.getBoundingClientRect();
    const cs = getComputedStyle(s);
    const shown = r.width > 1 && r.height > 1 && cs.display !== 'none' && cs.visibility !== 'hidden'
                  && !s.classList.contains('bs-select-native') && cs.position !== 'absolute';
    if (shown) vis.push({id: s.id, name: s.name});
  });
  return vis;
}"""


def main() -> int:
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 720, "height": 1600}, device_scale_factor=1)
        page = ctx.new_page()
        for route in ROUTES:
            page.goto(BASE + route, wait_until="networkidle")
            vis = page.evaluate(VISIBLE_SELECT_JS)
            combos = page.eval_on_selector_all(
                ".bs-combobox-trigger", "els => els.map(e => e.textContent.trim())"
            )
            out[route] = {"visible_native_selects": vis, "branded_comboboxes": combos}
            # Open the first combobox to prove it works, then screenshot.
            if combos:
                page.evaluate(
                    "() => { const t = document.querySelector('.bs-combobox-trigger'); if (t) t.click(); }"
                )
                page.wait_for_timeout(250)
            slug = route.strip("/").replace("/", "_") or "home"
            page.screenshot(path=f"{OUT}/u2_{slug}_720.png", full_page=False)
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
        ctx.close()
        browser.close()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
