"""U0 live evidence: the auth stat strip must not overflow at 390 or the founder's 720.
Local demo server only (read-only)."""
import json
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"
ROUTES = {"signin": "/signin", "signup": "/signup"}


def main() -> int:
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, w, h in (("720", 720, 1280), ("390", 390, 844)):
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            for label, route in ROUTES.items():
                page.goto(BASE + route, wait_until="networkidle")
                page.screenshot(path=f"{OUT}/u0_{label}_{name}.png", full_page=True)
                out[f"{label}_{name}"] = page.evaluate(
                    """() => {
                      const strip = document.querySelector('.auth-stats-strip');
                      const items = [...document.querySelectorAll('.auth-stat-item')];
                      const vw = window.innerWidth;
                      return {
                        overflow: document.documentElement.scrollWidth - vw,
                        itemRights: items.map(e => Math.round(e.getBoundingClientRect().right)),
                        itemMaxRight: items.length ? Math.round(Math.max(...items.map(e => e.getBoundingClientRect().right))) : null,
                        cols: strip ? getComputedStyle(strip).gridTemplateColumns : null,
                        viewport: vw,
                      };
                    }"""
                )
            ctx.close()
        browser.close()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
