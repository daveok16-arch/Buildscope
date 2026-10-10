"""G4 evidence: compute WCAG contrast ratios for the colour tokens and the overrides.

Prints the ratio for each (foreground, background) pair the app actually renders. No
browser needed; the values are read from app.css tokens and the literal overrides.

    PYTHONPATH=vendor/python:src python audit/scripts/wp2_contrast_table.py
"""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "src" / "oppintel" / "app" / "static" / "css" / "app.css"


def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def tokens() -> dict[str, str]:
    text = CSS.read_text()
    root = text[text.index(":root"): text.index("}", text.index(":root"))]
    return {m.group(1): m.group(2) for m in re.finditer(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root)}


def main() -> int:
    t = tokens()
    rows = [
        ("white on gold-500", "#ffffff", t["gold-500"]),
        ("white on gold-600", "#ffffff", t["gold-600"]),
        ("white on #92400e (E2 hover literal)", "#ffffff", "#92400e"),
        ("slate-300 on white", t["slate-300"], "#ffffff"),
        ("slate-500 on white", t["slate-500"], "#ffffff"),
        ("slate-600 on white", t["slate-600"], "#ffffff"),
        ("gold-600 on white", t["gold-600"], "#ffffff"),
        ("slate-500 on navy-950", t["slate-500"], t["navy-950"]),
        ("slate-300 on navy-950", t["slate-300"], t["navy-950"]),
        ("slate-300 on navy-900", t["slate-300"], t["navy-900"]),
    ]
    print(f"{'pair':42} {'fg':9} {'bg':9} {'ratio':>6}  verdict")
    for label, fg, bg in rows:
        r = ratio(fg, bg)
        verdict = "PASS AA" if r >= 4.5 else ("AA-large" if r >= 3.0 else "FAIL")
        print(f"{label:42} {fg:9} {bg:9} {r:6.2f}  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
