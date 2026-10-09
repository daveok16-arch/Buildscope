"""Before/after screenshots for WP2 (B1-B3).

Serves two live instances (the pre-change build and the current build) and captures
/, /opportunities, /companies and /trends at 390px and 1440px into audit/wp2/.

Usage:
    python audit/scripts/wp2_before_after.py [BEFORE_URL] [AFTER_URL]

Defaults: before http://127.0.0.1:12001, after http://127.0.0.1:12000.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BEFORE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:12001"
AFTER = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:12000"
OUT = "audit/wp2"
os.makedirs(OUT, exist_ok=True)

VIEWPORTS = {"390": {"width": 390, "height": 844}, "1440": {"width": 1440, "height": 900}}
ROUTES = {"home": "/", "opportunities": "/opportunities",
          "companies": "/companies", "trends": "/trends"}

with sync_playwright() as p:
    b = p.chromium.launch()
    for label, base in (("before", BEFORE), ("after", AFTER)):
        for vname, vp in VIEWPORTS.items():
            ctx = b.new_context(viewport=vp)
            page = ctx.new_page()
            for rname, route in ROUTES.items():
                page.goto(base + route, wait_until="networkidle")
                page.screenshot(path=f"{OUT}/{label}__{vname}__{rname}.png", full_page=True)
                sw = page.evaluate("() => document.documentElement.scrollWidth")
                iw = page.evaluate("() => window.innerWidth")
                print(f"{label:6} {vname:>4} {rname:14} scrollWidth={sw} innerWidth={iw} "
                      f"{'OVERFLOW' if sw > iw else 'ok'}")
            ctx.close()
    b.close()
print("wrote", OUT)
