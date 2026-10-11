"""WP3 gate: axe-core (full tag set) on every public route at 390 / 720 / 1440.

Injects axe honouring the page CSP nonce, against the local demo server. Read-only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "vendor/python")
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
AXE = (ROOT / "audit" / "a11y" / "axe.min.js").read_text()
BASE = "http://127.0.0.1:12000"
ROUTES = [
    "/", "/opportunities", "/companies", "/markets", "/markets/dfw", "/changes", "/trends",
    "/analytics", "/reports", "/how-it-works", "/guides", "/trades", "/trades/commercial-hvac",
    "/commercial-construction-leads", "/signin", "/signup",
]
VIEWPORTS = [("390", {"width": 390, "height": 844}),
             ("720", {"width": 720, "height": 1280}),
             ("1440", {"width": 1440, "height": 900})]
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]

total = 0
summary = []
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for vname, vp in VIEWPORTS:
        ctx = browser.new_context(viewport=vp)
        for route in ROUTES:
            page = ctx.new_page()
            console = []
            page.on("console", lambda m: console.append(m.text))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(BASE + route, wait_until="networkidle")
            nonce = page.evaluate(
                "() => { const s = document.querySelector('script[nonce]'); "
                "return s ? (s.nonce || s.getAttribute('nonce')) : null; }"
            )
            page.evaluate(
                """({src, nonce}) => { const s = document.createElement('script');
                     if (nonce) s.setAttribute('nonce', nonce);
                     s.textContent = src; document.head.appendChild(s); }""",
                {"src": AXE, "nonce": nonce},
            )
            res = page.evaluate(
                "async (tags) => await axe.run(document, {runOnly: {type: 'tag', values: tags}})",
                TAGS,
            )
            viol = res["violations"]
            total += len(viol)
            summary.append({"viewport": vname, "route": route, "violations": len(viol)})
            if viol:
                print(f"{vname:5} {route:34} {len(viol)} violations")
                for v in viol:
                    print(f"    {v['id']:26} {v['impact']:8} {v['nodes'][0]['target']}")
            csp = [c for c in console if "Content Security Policy" in c or "Refused" in c]
            if csp:
                print(f"    CSP console: {csp[:2]}")
            page.close()
        ctx.close()
    browser.close()

print(f"\nTOTAL violation instances: {total} (tags={','.join(TAGS)})")
print("ZERO_VIOLATIONS" if total == 0 else "VIOLATIONS_PRESENT")
Path("audit/wp3/axe_u5.json").write_text(json.dumps(summary, indent=2))
