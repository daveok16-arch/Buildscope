"""Compose audit/wp2/button_options.png from the j4 tiles using chromium (no PIL dependency).

Renders an annotated 3-column grid (A current / B gold-500 / C gold-500+navy border) of the
captured buttons and screenshots the grid.

    PYTHONPATH=vendor/python:src python audit/scripts/j4_montage.py
"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

WP2 = Path(__file__).resolve().parents[1] / "wp2"
OUT = WP2 / "button_options.png"

COLS = [
    ("A", "A — current: white on gold-600 #b45309 (5.02:1)"),
    ("B", "B — gold-500 #d97706 + navy-950 text (6.17:1)"),
    ("C", "C — gold-500 + navy-950 border + navy text (6.17:1)"),
]
ROWS = [
    ("1440_hero", "Home hero · 1440"),
    ("1440_header", "Header Get Access · 1440"),
    ("1440_signup", "Signup · 1440"),
    ("390_hero", "Home hero · 390"),
    ("390_signup", "Signup · 390"),
]


def main() -> None:
    cells = []
    for oid, label in COLS:
        imgs = []
        for key, rowlabel in ROWS:
            f = WP2 / f"j4_{oid}_{key}.png"
            src = f"file://{f}" if f.exists() else ""
            imgs.append(
                f'<div class="cell"><div class="lbl">{rowlabel}</div>'
                f'<img src="{src}"></div>'
            )
        cells.append(f'<div class="col"><h3>{label}</h3>{"".join(imgs)}</div>')
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      body {{ background:#0b1328; color:#e2e8f0; font-family:system-ui,sans-serif; margin:0; padding:24px; }}
      h1 {{ font-size:18px; margin:0 0 16px; }}
      .grid {{ display:flex; gap:20px; align-items:flex-start; }}
      .col {{ background:#101c38; padding:14px; border-radius:10px; }}
      h3 {{ font-size:13px; margin:0 0 10px; color:#fcd34d; }}
      .cell {{ margin-bottom:12px; }}
      .lbl {{ font-size:11px; color:#94a3b8; margin-bottom:4px; }}
      img {{ max-width:320px; background:#fff; border-radius:6px; display:block;
             border:1px solid #2a4382; }}
      footer {{ margin-top:16px; font-size:11px; color:#94a3b8; }}
    </style></head><body>
      <h1>BuildScope primary button — colour options (computed from the live DOM)</h1>
      <div class="grid">{"".join(cells)}</div>
      <footer>Focus ring: current gold-500 on white = 3.19:1 (fails the 3:1 non-text min);
      navy-950 on white = 19.66:1 (passes). Captured with transition: none so no value is mid-animation.</footer>
    </body></html>"""
    tmp = WP2 / "_montage.html"
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1120, "height": 900})
        pg.goto(f"file://{tmp}")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(OUT), full_page=True)
        b.close()
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
