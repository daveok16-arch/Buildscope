"""E3/E4 evidence: axe-core over every public route at mobile and desktop widths.

Prints per-rule results for the structural rules the Director named (heading
order, landmarks, dialog roles) plus the full violation set, so the output can be
pasted as proof rather than summarised.

    PYTHONPATH=vendor/python python audit/scripts/wp2_axe_structural.py
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROUTES = [
    "/", "/opportunities", "/companies", "/markets/dfw", "/changes", "/trends",
    "/analytics", "/reports", "/how-it-works", "/guides",
    "/trades/commercial-hvac", "/commercial-construction-leads", "/signin", "/signup",
]
BASE = "http://127.0.0.1:12000"
AXE = Path("audit/a11y/axe.min.js").read_text()

# Rules the Director named, checked explicitly in addition to the full run.
STRUCTURAL = [
    "heading-order",
    "landmark-one-main",
    "landmark-unique",
    "region",
    "aria-allowed-role",
    "aria-dialog-name",
    "focus-order-semantics",
]

VIEWPORTS = {"mobile": {"width": 390, "height": 844}, "desktop": {"width": 1440, "height": 900}}

summary = {}
structural_results = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    for vname, vp in VIEWPORTS.items():
        ctx = browser.new_context(viewport=vp)
        page = ctx.new_page()
        for route in ROUTES:
            page.goto(BASE + route, wait_until="networkidle")
            page.add_script_tag(content=AXE)
            res = page.evaluate("async () => await axe.run(document)")
            viols = [
                {"id": v["id"], "impact": v["impact"], "nodes": [n["target"][0] for n in v["nodes"]]}
                for v in res["violations"]
            ]
            summary[(vname, route)] = viols
            for v in res["violations"]:
                structural_results.setdefault((vname, v["id"]), []).append(route)
            # Also record the pass/fail of the structural rules explicitly.
            for rule_id in STRUCTURAL:
                for chk in res["passes"]:
                    if chk["id"] == rule_id:
                        structural_results.setdefault((vname, "PASS:" + rule_id), []).append(route)
        ctx.close()
    browser.close()

print("== violation counts by viewport ==")
for vname in VIEWPORTS:
    total = sum(len(v) for (vn, _), v in summary.items() if vn == vname)
    routes_with = [r for (vn, r), v in summary.items() if vn == vname and v]
    print(f"{vname:8} routes={len(ROUTES)} violation_kinds={total} routes_with_violations={routes_with}")

print("\n== structural rules ==")
for (vname, key), routes in sorted(structural_results.items()):
    if key.startswith("PASS:"):
        print(f"{vname:8} {key:32} passed on {len(routes)} routes")
for (vname, key), routes in sorted(structural_results.items()):
    if not key.startswith("PASS:"):
        print(f"{vname:8} VIOLATION {key:20} on {routes}")

print("\n== any violations (raw) ==")
any_v = False
for (vname, route), viols in summary.items():
    if viols:
        any_v = True
        print(vname, route, json.dumps(viols))
if not any_v:
    print("none")
