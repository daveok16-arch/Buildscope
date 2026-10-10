"""H5 — crystallise the two button-colour options (before/after) with real rendered evidence.

Captures the home-page primary button (`.btn-primary`) in its default and hover states, at
1440x900 (desktop) and 390x844 (mobile), and prints the computed background/color and the WCAG
ratio for each state. Read-only: it loads pages and reads the DOM.

Requires the app running on 127.0.0.1:12001 and CHROMIUM_PATH (default /usr/bin/chromium).

    PYTHONPATH=vendor/python:src python audit/scripts/h5_button_options.py
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
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium")


def hexish(rgb: str) -> str:
    """'rgb(180, 83, 9)' -> '#b45309'."""
    inner = rgb[rgb.index("(") + 1: rgb.index(")")]
    parts = [int(p.strip()) for p in inner.split(",")[:3]]
    return "#" + "".join(f"{p:02x}" for p in parts)


def _first_visible_primary(page):
    for el in page.query_selector_all(".btn-primary"):
        if el.is_visible():
            return el
    return None


def probe(page, url: str, viewport: tuple[int, int], tag: str) -> list[str]:
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.goto(f"{BASE}{url}", wait_until="networkidle")
    out = []
    slug = url.strip("/").replace("/", "_") or "home"
    btn = _first_visible_primary(page)
    if btn is None:
        return [f"{tag} {url}: no visible .btn-primary found"]
    style = page.evaluate(
        """(el) => { const s = getComputedStyle(el);
             return {bg: s.backgroundColor, color: s.color,
                     w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height}; }""",
        btn,
    )
    fg, bg = hexish(style["color"]), hexish(style["bg"])
    r = ratio(fg, bg)
    out.append(f"{tag} {url}: default bg={bg} fg={fg} ratio={r:.2f} "
               f"size={style['w']:.0f}x{style['h']:.0f}")
    try:
        btn.screenshot(path=str(OUT / f"h5_{tag}_{slug}_default.png"), timeout=5000)
    except Exception:
        # Element may be off-screen/hidden at this viewport; a full-page shot still shows it.
        page.screenshot(path=str(OUT / f"h5_{tag}_{slug}_default.png"), full_page=True)
    btn.hover()
    page.wait_for_timeout(250)
    hstyle = page.evaluate(
        """(el) => { const s = getComputedStyle(el);
             return {bg: s.backgroundColor, color: s.color}; }""",
        btn,
    )
    hfg, hbg = hexish(hstyle["color"]), hexish(hstyle["bg"])
    hr = ratio(hfg, hbg)
    out.append(f"{tag} {url}: hover   bg={hbg} fg={hfg} ratio={hr:.2f}")
    try:
        btn.screenshot(path=str(OUT / f"h5_{tag}_{slug}_hover.png"), timeout=5000)
    except Exception:
        page.screenshot(path=str(OUT / f"h5_{tag}_{slug}_hover.png"), full_page=True)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        page = browser.new_page()
        for vp, tag in [((1440, 900), "desktop"), ((390, 844), "mobile")]:
            for url in ("/", "/opportunities", "/signup"):
                try:
                    lines += probe(page, url, vp, tag)
                except Exception as exc:  # pragma: no cover - diagnostic
                    lines.append(f"{tag} {url}: ERROR {exc!r}")
        browser.close()
    for line in lines:
        print(line)
    print(f"\nscreenshots: {len(list(OUT.glob('h5_*.png')))} written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
