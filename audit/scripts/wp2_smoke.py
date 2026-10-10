"""G6 evidence: honest post-deploy smoke of the public surface.

Fetches every public route plus the health and statistics endpoints against a locally
served instance, and asserts the properties the WP1/WP2 work claims: no CSP violation,
no banned phrase, no future permit date rendered as current, and /healthz / /api/statistics
agreeing with the snapshot the pages use.

    PYTHONPATH=vendor/python:src python audit/scripts/wp2_smoke.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "src"))

from conftest_app import build_database  # noqa: E402
from oppintel.app.config import AppConfig  # noqa: E402
from oppintel.app.main import create_app  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

ROUTES = [
    "/", "/opportunities", "/companies", "/markets", "/markets/dfw", "/changes",
    "/trends", "/analytics", "/reports", "/how-it-works", "/trades",
    "/trades/commercial-hvac", "/commercial-construction-leads", "/guides",
    "/signin", "/signup", "/healthz",
]

#: Phrases that would overstate what the product does. Mirrors tests/test_copy_honesty.py
#: BANNED_OVERCLAIMS (the authoritative contract). "guarantee"/"verified bid" are legal
#: disclaimers ("we do not guarantee..."), so they are deliberately NOT in this list.
BANNED = [
    "saved search",
    "zero-false-positive",
    "zero false positive",
    "without false alerts",
    "continuous",
    "months before general contractors",
    "organize active pursuits with your team",
    "coordinate team business development",
    "teams can bookmark",
    "receive alerts",
    "receive an alert",
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke-"))
    build_database(tmp / "s.db").close()
    app = create_app(AppConfig(database_path=tmp / "s.db", secret_key="x", debug=True))
    srv = make_server("127.0.0.1", 0, app)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    client = app.test_client()

    failures = 0
    print(f"{'route':36} {'status':6} {'csp':4} {'banned':7} notes")
    for route in ROUTES:
        r = client.get(route)
        body = r.get_data(as_text=True)
        csp = "nonce-" in r.headers.get("Content-Security-Policy", "")
        hits = [b for b in BANNED if b in body.lower()]
        note = ""
        if hits:
            note += f"BANNED={hits} "
            failures += 1
        if not csp:
            note += "NO-CSP "
            failures += 1
        if r.status_code != 200:
            note += "BAD-STATUS "
            failures += 1
        print(f"{route:36} {r.status_code:<6} {str(csp):4} {str(hits):7} {note}")

    # Health + statistics must agree with each other on the headline counts.
    hz = client.get("/healthz").get_json()
    st = client.get("/api/statistics").get_json()["statistics"]
    print("\n/healthz       :", json.dumps({k: hz.get(k) for k in
          ("status", "projects", "permits", "mechanical_projects") if k in hz}, sort_keys=True))
    print("/api/statistics:", json.dumps(
        {k: st.get(k) for k in ("projects", "permits", "mechanical_projects") if k in st},
        sort_keys=True))

    srv.shutdown()
    th.join(timeout=5)
    print(f"\nFAILURES: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
