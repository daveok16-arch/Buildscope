"""U4 live evidence: the record foot states a collection date, not "Last verified <Month>".
Local demo server only (read-only)."""
import json
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"
DETAIL = "/opportunities/1701-s-university-dr-fort-worth-9c5275"


def main() -> int:
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, w, h in (("720", 720, 1280), ("390", 390, 844)):
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            page.goto(BASE + DETAIL, wait_until="networkidle")
            page.screenshot(path=f"{OUT}/u4_detail_{name}.png", full_page=True)
            out[f"detail_{name}"] = {
                "collected": page.eval_on_selector_all(
                    "text=Collected", "els => els.map(e => e.textContent.trim())"
                ) if page.locator("text=Collected").count() else [],
                "body_has_last_verified": "Last verified" in page.content(),
            }
            ctx.close()
        browser.close()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
