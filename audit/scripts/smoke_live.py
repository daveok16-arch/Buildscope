"""H7 — live smoke test against a running server. READ-ONLY.

Hits the running app over real HTTP (not the Flask test client) and prints a PASS/FAIL table:

* every public route returns 200 (404 for a deliberate miss);
* the four headline figures are identical across three consecutive loads (snapshot stability);
* the "Last collection:" label is present and the retired "Continuous Ingestion" is absent;
* the CSP nonce differs per response and is present on the response and its scripts;
* time-to-first-byte for the home page.

    SMOKE_BASE=http://127.0.0.1:12001 PYTHONPATH=vendor/python:src python audit/scripts/smoke_live.py
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:12001")

ROUTES = [
    "/", "/opportunities", "/companies", "/markets", "/markets/dfw", "/changes", "/trends",
    "/analytics", "/reports", "/how-it-works", "/trades", "/trades/commercial-hvac",
    "/commercial-construction-leads", "/guides", "/signin", "/signup", "/healthz",
]


def fetch(path: str) -> tuple[int, str, dict[str, str], float]:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "BuildScope-audit/1.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body, dict(resp.headers), time.time() - t0
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers), time.time() - t0


def numbers_from_home(body: str) -> list[tuple[str, str]]:
    """Pull the rendered headline stat pairs (value,label) from the home page."""
    pairs = re.findall(
        r'stat-value[^>]*>\s*([0-9][0-9,]*)\s*<[^>]*>\s*(?:</[^>]+>\s*)*'
        r'<[^>]*stat-label[^>]*>\s*([^<]+?)\s*<',
        body,
    )
    if pairs:
        return [(v.strip(), l.strip()) for v, l in pairs]
    # Fallback: any value/label adjacency.
    pairs = re.findall(r'>([0-9][0-9,]{0,9})<[^>]*>\s*<[^>]*>([^<]{3,40})<', body)
    return [(v.strip(), l.strip()) for v, l in pairs]


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    # 1. Route status codes.
    for path in ROUTES:
        status, body, headers, _ = fetch(path)
        ok = status == 200
        results.append((f"GET {path}", ok, f"HTTP {status}"))

    # 2. Deliberate miss -> 404.
    status, _, _, _ = fetch("/this-route-does-not-exist")
    results.append(("GET /this-route-does-not-exist", status == 404, f"HTTP {status}"))

    # 3. Snapshot stability: three loads show identical headline numbers.
    runs = [numbers_from_home(fetch("/")[1]) for _ in range(3)]
    stable = runs[0] == runs[1] == runs[2]
    shown = ", ".join(f"{v} {l}" for v, l in runs[0][:4])
    results.append(("home numbers stable over 3 loads", stable, shown or "no stats parsed"))

    # 4. Collection label honesty.
    home = fetch("/")[1]
    hiw = fetch("/how-it-works")[1]
    results.append(("'Last collection:' present on /",
                    "Last collection:" in home, "present" if "Last collection:" in home else "MISSING"))
    results.append(("'Continuous Ingestion' absent",
                    "Continuous Ingestion" not in home and "Continuous Ingestion" not in hiw,
                    "absent" if "Continuous Ingestion" not in home else "PRESENT"))

    # 5. CSP nonce differs per response and is bound to the response header.
    _, body1, h1, _ = fetch("/")
    _, _, h2, _ = fetch("/")
    csp1 = next((v for k, v in h1.items() if k.lower() == "content-security-policy"), "")
    csp2 = next((v for k, v in h2.items() if k.lower() == "content-security-policy"), "")
    n1 = re.search(r"'nonce-([^']+)'", csp1)
    n2 = re.search(r"'nonce-([^']+)'", csp2)
    nonce_present = bool(n1)
    nonce_varies = bool(n1 and n2 and n1.group(1) != n2.group(1))
    nonce_in_page = bool(n1 and f'nonce="{n1.group(1)}"' in body1)
    results.append(("CSP nonce present on response", nonce_present, csp1[:60] or "no CSP header"))
    results.append(("CSP nonce varies per response", nonce_varies,
                    "varies" if nonce_varies else "SAME nonce on two responses"))
    results.append(("CSP nonce matches a page script", nonce_in_page,
                    "bound" if nonce_in_page else "no script carries the response nonce"))

    # 6. TTFB on the home page (first byte already includes render for this app).
    ttfb = min(fetch("/")[3] for _ in range(3))
    results.append(("home TTFB < 1.5s (local)", ttfb < 1.5, f"{ttfb*1000:.0f} ms"))

    print(f"=== live smoke against {BASE} ===")
    width = max(len(name) for name, _, _ in results) + 2
    passed = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL':4} {name:<{width}} {detail}")
        passed += ok
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
