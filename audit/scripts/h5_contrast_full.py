"""H5 — full WCAG contrast table for every colour token pair the app renders. READ-ONLY.

Extends wp2_contrast_table.py with the raw signal hues vs their -text variants, the primary
button before/after, and the dark-panel pairs. Prints one row per rendered pair so the audit
can cite a ratio for each.

    PYTHONPATH=vendor/python:src python audit/scripts/h5_contrast_full.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wp2_contrast_table import ratio, tokens  # noqa: E402


def build_rows(t: dict[str, str]) -> list[tuple[str, str, str]]:
    return [
        ("primary btn: white on gold-500 (BEFORE wp2)", "#ffffff", t["gold-500"]),
        ("primary btn: white on gold-600 (AFTER wp2)", "#ffffff", t["gold-600"]),
        ("primary btn hover: white on #92400e (E2 literal)", "#ffffff", "#92400e"),
        ("body text: ink on white", t["ink"], "#ffffff"),
        ("ink-soft on white", t["ink-soft"], "#ffffff"),
        ("ink-muted on white", t["ink-muted"], "#ffffff"),
        ("ink-quiet on white (placeholder-scale)", t["ink-quiet"], "#ffffff"),
        ("muted/slate-500 on white", t["muted"], "#ffffff"),
        ("nav link slate-300 on navy-950", t["slate-300"], t["navy-950"]),
        ("nav link slate-300 on navy-900", t["slate-300"], t["navy-900"]),
        ("slate-400 on navy-950", t["slate-400"], t["navy-950"]),
        ("slate-400 on navy-900", t["slate-400"], t["navy-900"]),
        ("slate-500 on navy-950", t["slate-500"], t["navy-950"]),
        ("signal-high raw on white (old indicator)", t["signal-high"], "#ffffff"),
        ("signal-med raw on white (old indicator)", t["signal-med"], "#ffffff"),
        ("signal-low raw on white", t["signal-low"], "#ffffff"),
        ("signal-high-text on signal-high-bg", t["signal-high-text"], t["signal-high-bg"]),
        ("signal-med-text on signal-med-bg", t["signal-med-text"], t["signal-med-bg"]),
        ("signal-low-text on signal-low-bg", t["signal-low-text"], t["signal-low-bg"]),
        ("signal-closed-text on signal-closed-bg", t["signal-closed-text"], t["signal-closed-bg"]),
        ("accent-light (#1d4ed8) link on white", t["accent-light"], "#ffffff"),
    ]


def main() -> int:
    t = tokens()
    print(f"{'pair':52} {'fg':9} {'bg':9} {'ratio':>6}  verdict")
    fails = 0
    for label, fg, bg in build_rows(t):
        r = ratio(fg, bg)
        verdict = "PASS AA" if r >= 4.5 else ("AA-large-only" if r >= 3.0 else "FAIL AA")
        if r < 4.5:
            fails += 1
        print(f"{label:52} {fg:9} {bg:9} {r:6.2f}  {verdict}")
    print(f"\n{'-' * 52} {len(build_rows(t))} pairs, {fails} below AA (4.5:1) for small text")
    print("Indicator ratios are checked against small text: the signal text now uses the")
    print("*-text variants. AA-large-only pairs are acceptable only for >=18.66px bold / 24px text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
