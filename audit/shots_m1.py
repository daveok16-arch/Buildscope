import sys
sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
SHOTS = [("opportunities", "/opportunities"), ("companies", "/companies"),
         ("home", "/"), ("trends", "/trends"), ("new_filings", "/opportunities/new")]
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, route in SHOTS:
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(BASE + route, wait_until="networkidle")
        pg.wait_for_timeout(700)
        pg.screenshot(path=f"audit/releases/m1/{name}_390.png", full_page=True)
        ctx.close()
    b.close()
print("release screenshots done")
