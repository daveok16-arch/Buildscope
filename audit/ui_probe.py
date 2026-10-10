"""Read-only UI audit: screenshots at 3 viewports + overflow/tap-target detection.

Writes only under audit/screenshots/ (new dir). No app code is touched.
"""
import json
import os
import sys

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = os.environ.get("AUDIT_BASE", "http://127.0.0.1:12000")
OUT = "audit/screenshots"
os.makedirs(OUT, exist_ok=True)

ROUTES = [
    "/", "/opportunities", "/opportunities/new", "/markets", "/markets/dfw",
    "/companies", "/changes", "/trends", "/analytics", "/reports", "/how-it-works",
    "/trades", "/trades/commercial-hvac", "/commercial-construction-leads",
    "/guides", "/signin", "/signup", "/this-does-not-exist",
]
VIEWPORTS = [
    ("mobile", 390, 844),
    ("tablet", 820, 1180),
    ("desktop", 1440, 900),
]


def slug(route):
    s = route.strip("/").replace("/", "_")
    return s or "root"


OVERFLOW_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const bad = [];
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1) {
      const tag = el.tagName.toLowerCase();
      const cls = (el.className && typeof el.className === 'string') ? el.className.slice(0,60) : '';
      if (el.closest('svg')) return;
      bad.push({tag, cls, right: Math.round(r.right), vw});
    }
  });
  return {scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth,
          offending: bad.slice(0, 12)};
}"""

TAP_JS = """() => {
  const out = [];
  document.querySelectorAll('a,button,input,select,textarea,[role=button]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    if (r.height < 44 || r.width < 44) {
      const t = (el.textContent || el.getAttribute('aria-label') || el.name || '').trim().slice(0,30);
      out.push({tag: el.tagName.toLowerCase(), role: el.getAttribute('role')||'',
                w: Math.round(r.width), h: Math.round(r.height), text: t});
    }
  });
  return out.slice(0, 40);
}"""


def main():
    results = {"routes": {}, "viewport_screens": []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vp_name, w, h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            for route in ROUTES:
                key = f"{vp_name}__{slug(route)}"
                try:
                    page.goto(BASE + route, wait_until="networkidle", timeout=30000)
                except Exception as e:
                    results["routes"][key] = {"error": str(e)}
                    continue
                page.screenshot(path=os.path.join(OUT, key + ".png"), full_page=True)
                ov = page.evaluate(OVERFLOW_JS)
                entry = {"scrollWidth": ov["scrollWidth"], "innerWidth": ov["innerWidth"],
                         "overflow": ov["offending"]}
                if vp_name == "mobile":
                    entry["tap_targets"] = page.evaluate(TAP_JS)
                results["routes"][key] = entry
                results["viewport_screens"].append(key)
            ctx.close()
        browser.close()
    with open("audit/ui_probe_results.json", "w") as f:
        json.dump(results, f, indent=1)
    # console summary
    for k, v in results["routes"].items():
        if "error" in v:
            print("ERR", k, v["error"][:80]); continue
        if v["overflow"] or v["scrollWidth"] > v["innerWidth"]:
            print("OVERFLOW", k, "scroll", v["scrollWidth"], "vw", v["innerWidth"],
                  "|", [f'{o["tag"]}.{o["cls"]}@{o["right"]}' for o in v["overflow"]][:4])


if __name__ == "__main__":
    main()
