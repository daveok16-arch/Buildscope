"""E5 evidence: 390px screenshots of the filter sheets and the active-filter count.

    PYTHONPATH=vendor/python python audit/scripts/wp2_e5_screenshots.py
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:12000"
OUT = Path("audit/wp2")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    page = ctx.new_page()

    page.goto(BASE + "/opportunities", wait_until="networkidle")
    page.click("#mobile-filter-btn")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "e5__390__opportunities_sheet_open.png"), full_page=False)

    page.goto(BASE + "/companies", wait_until="networkidle")
    page.click("#mobile-filter-btn")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "e5__390__companies_sheet_open.png"), full_page=False)

    page.goto(BASE + "/opportunities?city=Plano&classification=MEDIUM", wait_until="networkidle")
    page.screenshot(path=str(OUT / "e5__390__opportunities_active_filter_count.png"), full_page=False)

    ctx.close()
    browser.close()

for f in sorted(OUT.glob("e5__*.png")):
    print(f, f.stat().st_size, "bytes")
