"""U3 evidence: sweep every stored project name through the display-layer title cleaner.

READ-ONLY. Copies nothing and writes nothing to the database; it only SELECTs. Run against a
copy of data/oppintel.db (the standing rule) or the live file for a read.

    PYTHONPATH="vendor/python:src" python3 audit/wp3/title_sweep.py [DB]

Prints: totals, how many names carry an internal marker, how many are cleaned to a real
headline, how many still need the fallback, and the 50 worst titles before/after (worst =
longest residual note, then longest title).
"""
from __future__ import annotations

import sqlite3
import sys

from oppintel.app.display import _looks_like_a_note, clean_title


def main() -> int:
    db = sys.argv[1] if len(sys.argv) > 1 else "/tmp/audit_u3.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, project_name, address FROM project").fetchall()

    markers = 0
    cleaned = 0
    still_note = 0
    worst: list[tuple[str, str, str]] = []
    for row in rows:
        raw = row["project_name"] or ""
        if not raw:
            continue
        low = raw.lower()
        is_marker = any(
            token in low for token in ("qteam", "q team", "x team", "x-team", "meeting tbd",
                                       "meeting tba", "inhouse", "in-house")
        ) or "///" in raw
        out = clean_title(raw, {"address": row["address"]})
        if is_marker:
            markers += 1
            if out and not _looks_like_a_note(out):
                cleaned += 1
            else:
                still_note += 1
                worst.append((f"#{row['id']}", raw, out))

    worst.sort(key=lambda t: -len(t[1]))
    print(f"db: {db}")
    print(f"projects: {len(rows)}")
    print(f"names with an internal marker: {markers}")
    print(f"  cleaned to a real headline:  {cleaned}")
    print(f"  still a note (need fallback): {still_note}")
    print()

    # The residual-note list is empty when the cleaner handles every marker. The other thing a
    # reviewer needs is the 50 longest titles (the ones the 3-line clamp and truncate act on).
    longest = sorted(
        ((row["id"], row["project_name"] or "", clean_title(row["project_name"], {"address": row["address"]}))
         for row in rows if row["project_name"]),
        key=lambda t: -len(t[1]),
    )[:50]
    print("50 worst (longest titles) before -> after:")
    for pid, raw, out in longest:
        print(f"  #{pid} [{len(raw)}] {raw[:100]!r}")
        print(f"       -> [{len(out)}] {out[:100]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
