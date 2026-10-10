"""G3 evidence: axe-core with the full tag set, 14 routes x 2 viewports.

Run-only tags: wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa, best-practice.
Injects axe honouring the page CSP nonce (the app no longer allows 'unsafe-inline' scripts).

    PYTHONPATH=vendor/python:src python audit/scripts/wp2_axe_zero.py
"""
from __future__ import annotations

import sys
import threading
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_app import build_database  # noqa: E402
from oppintel.app.config import AppConfig  # noqa: E402
from oppintel.app.main import create_app  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

AXE = REPO_ROOT / "audit" / "a11y" / "axe.min.js"
ROUTES = [
    "/", "/opportunities", "/companies", "/markets/dfw", "/changes", "/trends",
    "/analytics", "/reports", "/how-it-works", "/guides", "/trades/commercial-hvac",
    "/commercial-construction-leads", "/signin", "/signup",
]
VIEWPORTS = {"mobile": {"width": 390, "height": 844}, "desktop": {"width": 1440, "height": 900}}
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="axezero-"))
    build_database(tmp / "z.db").close()
    app = create_app(AppConfig(database_path=tmp / "z.db", secret_key="x", debug=True))
    srv = make_server("127.0.0.1", 0, app)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    axe_src = AXE.read_text()

    total = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for vname, vp in VIEWPORTS.items():
            ctx = browser.new_context(viewport=vp)
            for route in ROUTES:
                page = ctx.new_page()
                console = []
                page.on("console", lambda m: console.append(m.text))
                page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
                page.goto(base + route, wait_until="networkidle")
                nonce = page.evaluate(
                    "() => { const s = document.querySelector('script[nonce]'); "
                    "return s ? (s.nonce || s.getAttribute('nonce')) : null; }"
                )
                page.evaluate(
                    """({src, nonce}) => {
                         const s = document.createElement('script');
                         if (nonce) s.setAttribute('nonce', nonce);
                         s.textContent = src; document.head.appendChild(s);
                       }""",
                    {"src": axe_src, "nonce": nonce},
                )
                res = page.evaluate(
                    "async (tags) => await axe.run(document, {runOnly: {type: 'tag', values: tags}})",
                    TAGS,
                )
                viol = res["violations"]
                total += len(viol)
                if viol:
                    print(f"{vname:8} {route:34} {len(viol)} violations")
                    for v in viol:
                        print(f"    {v['id']:26} {v['impact']:8} {v['nodes'][0]['target']}")
                page.close()
            ctx.close()
        browser.close()
    srv.shutdown()
    th.join(timeout=5)
    print(f"\nTOTAL violation instances: {total}  (tags={','.join(TAGS)})")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
