"""J2 screenshots + computed-style proof for the primary button (Option B).

Viewport screenshots at 1440x900 (desktop) and 390x844 (mobile) for the home hero, the header
"Get Access" button and the signup button, plus the *computed* background/colour default and
hover so the CSS that shipped is what is measured.
    PYTHONPATH=vendor/python:src python audit/scripts/j2_button_shots.py
"""
from __future__ import annotations

import json
import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:12001")
OUT = "audit/wp2"
os.makedirs(OUT, exist_ok=True)

CASES = [
    ("1440", 1440, 900, "/", ".site-header .btn-primary", "header_get_access"),
    ("1440", 1440, 900, "/", ".hero .btn-primary", "home_hero"),
    ("1440", 1440, 900, "/signup", "#main .btn-primary", "signup"),
    ("390", 390, 844, "/", ".hero .btn-primary", "home_hero"),
    ("390", 390, 844, "/", ".mobile-drawer .btn-primary", "header_get_access"),
    ("390", 390, 844, "/signup", "#main .btn-primary", "signup"),
]

MEASURE = """el => {
    const cs = getComputedStyle(el);
    return {bg: cs.backgroundColor, color: cs.color, border: cs.borderColor,
            fontSize: cs.fontSize, fontWeight: cs.fontWeight};
}"""


def main() -> None:
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for label, w, h, route, sel, which in CASES:
            ctx = b.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.goto(BASE + route, wait_until="networkidle")
            # The mobile "Get Access" lives in the nav drawer, which is hidden until opened.
            if which == "header_get_access" and label == "390":
                page.click("#mobile-menu-open-btn")
                page.wait_for_timeout(400)
            page.screenshot(path=f"{OUT}/j2_{label}_{which}.png", full_page=False)
            try:
                default = page.eval_on_selector(sel, MEASURE)
                page.hover(sel)
                page.wait_for_timeout(250)
                hover = page.eval_on_selector(sel, MEASURE)
                page.focus(sel)
                page.wait_for_timeout(150)
                foc = page.eval_on_selector(
                    sel,
                    "el => { const cs = getComputedStyle(el); "
                    "return {outline: cs.outlineColor + ' ' + cs.outlineWidth + ' ' + cs.outlineStyle}; }",
                )
                out[f"{label}_{which}"] = {"selector": sel, "default": default, "hover": hover, "focus": foc}
            except Exception as e:  # noqa: BLE001 - report which selector failed, keep going
                out[f"{label}_{which}"] = {"selector": sel, "error": str(e).splitlines()[0]}
            ctx.close()
        b.close()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
