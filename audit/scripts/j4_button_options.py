"""J4 — primary-button colour options (A current, B gold-500, C gold-500 + navy border).

Read-only. Loads the running app, injects each option's styles into the live page, captures the
three buttons (home hero, header "Get Access", signup) at 1440x900 and 390x844, reads the
*computed* background/colour/border for the default and hover states, and prints WCAG ratios.
Combines every shot into audit/wp2/button_options.png (screenshot montage, no PIL needed).

    SMOKE_BASE=http://127.0.0.1:12001 PYTHONPATH=vendor/python:src \\
        python audit/scripts/j4_button_options.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wp2_contrast_table import ratio  # noqa: E402

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:12001")
OUT = Path(__file__).resolve().parents[1] / "wp2"
GOLD500, GOLD400, NAVY950, NAVY850 = "#d97706", "#f59e0b", "#060b17", "#101c38"
CURRENT, CURRENT_HOVER = "#b45309", "#92400e"

# (id, label, normal bg, hover bg, text colour, border colour)
OPTIONS = [
    ("A", "A current  white/gold-600", CURRENT, CURRENT_HOVER, "#ffffff", CURRENT),
    ("B", "B gold-500 + navy text", GOLD500, GOLD400, NAVY950, GOLD500),
    ("C", "C gold-500 + navy text + navy border", GOLD500, GOLD400, NAVY950, NAVY850),
]


def hexish(rgb: str) -> str:
    inner = rgb[rgb.index("(") + 1: rgb.index(")")]
    parts = [int(float(p.strip())) for p in inner.split(",")[:3]]
    return "#" + "".join(f"{p:02x}" for p in parts)


def css_for(bg: str, hover: str, fg: str, border: str) -> str:
    # `transition: none` is essential: the button animates `color` for 0.15s, so a computed-style
    # read taken right after the style is applied lands mid-transition and reports a blended
    # colour (e.g. #5c5f67) instead of the real value.
    return f"""
    * {{ transition: none !important; animation: none !important; }}
    .btn-primary {{ background: {bg} !important; color: {fg} !important;
                    border-color: {border} !important; }}
    .btn-primary:hover {{ background: {hover} !important; border-color: {hover} !important;
                          color: {fg} !important; }}
    """


CASES = [
    ("1440", 1440, 900, "/", ".hero .btn-primary", "hero"),
    ("1440", 1440, 900, "/", ".site-header .btn-primary", "header"),
    ("1440", 1440, 900, "/signup", "#main .btn-primary", "signup"),
    ("390", 390, 844, "/", ".hero .btn-primary", "hero"),
    ("390", 390, 844, "/signup", "#main .btn-primary", "signup"),
]

MEASURE = """el => { const s = getComputedStyle(el);
    return {bg: s.backgroundColor, color: s.color, border: s.borderColor}; }"""


def shots_for(page, opt, shots, rows):
    oid, label, bg, hover, fg, border = opt
    page.add_style_tag(content=css_for(bg, hover, fg, border))
    for vp, w, h, route, sel, which in CASES:
        page.set_viewport_size({"width": w, "height": h})
        page.goto(f"{BASE}{route}", wait_until="networkidle")
        page.add_style_tag(content=css_for(bg, hover, fg, border))
        el = page.query_selector(sel)
        if el is None or not el.is_visible():
            rows.append(f"{oid} {vp} {which}: selector {sel} not visible")
            continue
        st = page.evaluate(MEASURE, el)
        fg_c, bg_c = hexish(st["color"]), hexish(st["bg"])
        r = ratio(fg_c, bg_c)
        path = OUT / f"j4_{oid}_{vp}_{which}.png"
        el.screenshot(path=str(path))
        shots.append(str(path))
        rows.append(f"{oid} {vp} {which:6}: default bg={bg_c} fg={fg_c} border={hexish(st['border'])} "
                    f"ratio={r:.2f}:1  -> {path.name}")
        # hover
        el.hover()
        page.wait_for_timeout(120)
        sth = page.evaluate(MEASURE, el)
        rh = ratio(hexish(sth["color"]), hexish(sth["bg"]))
        hp = OUT / f"j4_{oid}_{vp}_{which}_hover.png"
        el.screenshot(path=str(hp))
        shots.append(str(hp))
        rows.append(f"{oid} {vp} {which:6}: hover   bg={hexish(sth['bg'])} fg={hexish(sth['color'])} "
                    f"ratio={rh:.2f}:1")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shots: list[str] = []
    rows: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for opt in OPTIONS:
            page = b.new_page()
            shots_for(page, opt, shots, rows)
            page.close()
        b.close()

    print("=== computed styles (live DOM, styles injected per option) ===")
    for r in rows:
        print(" ", r)

    print("\n=== contrast table (WCAG 2.1) ===")
    for oid, label, bg, hover, fg, border in OPTIONS:
        print(f"  {oid} {label:38} default={ratio(fg,bg):.2f}:1  hover={ratio(fg,hover):.2f}:1")
    print(f"  focus ring  current gold-500 on white = {ratio(GOLD500,'#ffffff'):.2f}:1 (fails 3:1 UI min)")
    print(f"  focus ring  navy-950 on white         = {ratio(NAVY950,'#ffffff'):.2f}:1 (passes)")


if __name__ == "__main__":
    main()
