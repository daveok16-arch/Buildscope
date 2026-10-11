"""V1f — LIVE screenshots at 390 and 720. Writes audit/live/V_*.png."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

BASE = "https://buildscope-xppz.onrender.com"
OUT = Path("audit/live")
DOSSIER = "/opportunities/3111-monticello-ave-a-dallas-031792"
QTEAM = "/opportunities/9855-walnut-hill-ln-dallas-e25b82"


def open_combo(page, match):
    page.evaluate(
        """(m) => {
            const boxes = [...document.querySelectorAll('.bs-combobox')];
            const b = boxes.find(x => new RegExp(m, 'i').test(x.textContent)) || boxes[0];
            b.querySelector('.bs-combobox-trigger').click();
        }""",
        match,
    )
    page.wait_for_timeout(600)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    for w, h in ((390, 844), (720, 1280)):
        ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        p = ctx.new_page()

        p.goto(BASE + "/", wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(600)
        p.screenshot(path=str(OUT / f"V_home_top_{w}.png"))

        # drawer
        try:
            p.click("#mobile-menu-open-btn", timeout=4000)
            p.wait_for_timeout(600)
            p.screenshot(path=str(OUT / f"V_drawer_{w}.png"))
            p.keyboard.press("Escape")
            p.wait_for_timeout(400)
        except Exception as e:
            print("drawer skip", w, e)

        p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        p.wait_for_timeout(600)
        p.screenshot(path=str(OUT / f"V_home_bottom_{w}.png"))

        # opportunities + city picker open
        p.goto(BASE + "/opportunities", wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(500)
        p.screenshot(path=str(OUT / f"V_opportunities_{w}.png"))
        open_combo(p, "cities")
        p.screenshot(path=str(OUT / f"V_citypicker_open_{w}.png"))

        # sort open
        p.goto(BASE + "/opportunities", wait_until="networkidle", timeout=45000)
        open_combo(p, "sort|newest|oldest|order")
        p.screenshot(path=str(OUT / f"V_sort_open_{w}.png"))

        # new filings
        p.goto(BASE + "/opportunities/new", wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(500)
        p.screenshot(path=str(OUT / f"V_new_filings_{w}.png"))

        # companies + role control open
        p.goto(BASE + "/companies", wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(500)
        p.screenshot(path=str(OUT / f"V_companies_{w}.png"))
        try:
            open_combo(p, "role|owner")
            p.screenshot(path=str(OUT / f"V_companies_role_open_{w}.png"))
        except Exception as e:
            print("companies role skip", w, e)

        # trends top + chart
        p.goto(BASE + "/trends", wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(600)
        p.screenshot(path=str(OUT / f"V_trends_top_{w}.png"))
        p.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.55)")
        p.wait_for_timeout(600)
        p.screenshot(path=str(OUT / f"V_trends_chart_{w}.png"))

        # dossier (Monticello/QTEAM)
        p.goto(BASE + DOSSIER, wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(600)
        p.screenshot(path=str(OUT / f"V_dossier_{w}.png"))

        # QTEAM title dossier (U3 target)
        p.goto(BASE + QTEAM, wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(600)
        p.screenshot(path=str(OUT / f"V_dossier_qteam_{w}.png"))

        # markets
        p.goto(BASE + "/markets", wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(500)
        p.screenshot(path=str(OUT / f"V_markets_{w}.png"))

        ctx.close()
    b.close()
print("done")
