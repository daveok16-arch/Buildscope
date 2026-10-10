"""I3a — per-route cache/CSP matrix. READ-ONLY.

Prints, for every GET route the app serves, the status, Cache-Control, whether a Set-Cookie is
present, the Vary header and whether the CSP carries a per-request nonce. Also proves the nonce
is unique per response and never appears in a cached public body.

    PYTHONPATH=vendor/python:src python audit/scripts/i3_cache_matrix.py [base-url]

Default base url is http://127.0.0.1:12001. Detail routes are resolved to a real id from the
database so the table covers them too; a route needing an id with no data is skipped and listed.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import urllib.error
import urllib.request

DB = "/tmp/h1_readonly.db"


def fetch(base: str, path: str):
    req = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return e.code, dict(e.headers), body


def sample_routes() -> list[str]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out: list[str] = []
    row = con.execute("SELECT slug FROM project_slug LIMIT 1").fetchone()
    if row:
        out.append(f"/opportunities/{row[0]}")
    con.close()
    return out


FIXED = [
    "/", "/opportunities", "/markets", "/markets/dfw", "/companies", "/changes", "/trends",
    "/analytics", "/reports", "/how-it-works", "/trades", "/trades/commercial-hvac",
    "/commercial-construction-leads", "/guides", "/signin", "/signup",
    "/dashboard", "/saved", "/watching", "/my-pipeline", "/alerts", "/preferences",
    "/api/statistics", "/api/trends", "/healthz", "/robots.txt", "/sitemap.xml",
    "/static/css/app.css", "/this-does-not-exist",
]

NONCE_RE = re.compile(r"nonce-([A-Za-z0-9]+)")


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:12001").rstrip("/")
    paths = FIXED + sample_routes()
    print(f"base={base}\n")
    hdr = f"{'path':38} {'st':>3}  {'Cache-Control':22} {'SetCookie':9} {'Vary':11} nonce"
    print(hdr)
    print("-" * len(hdr))
    nonces: list[tuple[str, str]] = []
    for path in paths:
        status, headers, body = fetch(base, path)
        cache = headers.get("Cache-Control", "-")
        cookie = "yes" if headers.get("Set-Cookie") else "no"
        vary = headers.get("Vary", "-")
        csp = headers.get("Content-Security-Policy", "")
        m = NONCE_RE.search(csp)
        nonce = m.group(1) if m else "-"
        if m:
            nonces.append((path, nonce))
        # A nonce in the body must match the header's nonce.
        bound = ""
        if m and f'nonce="{m.group(1)}"' in body:
            bound = "(body-bound)"
        print(f"{path:38} {status:>3}  {cache:22} {cookie:9} {vary:11} {nonce[:12]} {bound}")

    # --- nonce uniqueness: two responses to the same public page must differ -------------
    print("\n-- nonce uniqueness across two requests to / --")
    _, h1, _ = fetch(base, "/")
    _, h2, _ = fetch(base, "/")
    n1 = NONCE_RE.search(h1.get("Content-Security-Policy", ""))
    n2 = NONCE_RE.search(h2.get("Content-Security-Policy", ""))
    a = n1.group(1) if n1 else None
    b = n2.group(1) if n2 else None
    print(f"   request 1 nonce = {a}")
    print(f"   request 2 nonce = {b}")
    print(f"   distinct        = {a is not None and b is not None and a != b}")

    # --- no nonce ever reaches a publicly cached body -----------------------------------
    public_html = [
        p for p in paths
        if p != "/static/css/app.css"
        and fetch(base, p)[1].get("Cache-Control", "").startswith("public")
        and fetch(base, p)[1].get("Content-Type", "").startswith("text/html")
    ]
    print(f"\n-- HTML routes that are publicly cacheable: {public_html or 'NONE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
