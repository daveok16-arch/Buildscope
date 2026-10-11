"""U3 live evidence: capture the founder's device (720 DPR1) and mobile (390) for the
cleaned titles. Local demo server only (read-only)."""
import json
import sqlite3
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = "audit/wp3"
DB = "/tmp/audit_u.db"

# The projects the founder flagged live (internal-marker titles), by stored id.
FLAGGED = [22, 33, 157, 207, 264, 363, 440, 482]
MARKERS = ("X TEAM", "QTEAM", "Q TEAM", "MEETING TBD", "INHOUSE", "///")


def slugs() -> dict[int, str]:
    conn = sqlite3.connect(DB)
    out = {}
    for pid in FLAGGED:
        row = conn.execute("SELECT slug FROM project_slug WHERE project_id=?", (pid,)).fetchone()
        if row:
            out[pid] = row[0]
    return out


def main() -> int:
    route = slugs()
    results = {"flagged": {}, "list": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, w, h in (("720", 720, 1280), ("390", 390, 844)):
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()

            page.goto(BASE + "/opportunities", wait_until="networkidle")
            page.screenshot(path=f"{OUT}/u3_list_{name}.png", full_page=True)
            titles = page.eval_on_selector_all(".card-title", "els => els.map(e => e.textContent.trim())")
            results["list"][name] = {
                "sample": titles[:6],
                "leaked_markers": [t for t in titles if any(m in t for m in MARKERS)],
                "overflow": page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"),
            }

            for pid, slug in route.items():
                page.goto(f"{BASE}/opportunities/{slug}", wait_until="networkidle")
                h1 = page.inner_text("h1")
                page.screenshot(path=f"{OUT}/u3_detail_{pid}_{name}.png", full_page=True)
                results["flagged"].setdefault(pid, {})[name] = {
                    "h1": h1,
                    "leaked_markers": [m for m in MARKERS if m in h1],
                    "overflow": page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"),
                }
            ctx.close()
        browser.close()
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
