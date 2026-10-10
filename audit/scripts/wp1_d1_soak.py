#!/usr/bin/env python3
"""D1 evidence: concurrent readers + web-style writes against a long ingest writer.

Runs for a fixed duration (default 60s) against a real on-disk SQLite database and reports
whether any operation raised "database is locked". Prints the counters so the run proves it did
work, not just that nothing happened.

    PYTHONPATH=src python audit/scripts/wp1_d1_soak.py --seconds 60
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from oppintel.db import Database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="d1soak-"))
    path = tmp / "soak.db"
    db = Database(path)
    db.init_schema()
    db.init_app_schema()
    db.conn.execute(
        "INSERT INTO source (id, name, kind, market_coverage, updated_at) "
        "VALUES ('s1', 'Source', 'arcgis', 'DFW', '2026-10-09T00:00:00+00:00')"
    )
    # A user and a project so the web writers hit the real watchlist/notes tables, which is
    # what a signed-in request actually writes (not a generic analytics row).
    db.conn.execute(
        "INSERT INTO app_user (id, email, password_hash, created_at) "
        "VALUES (1, 'soak@example.com', 'x', '2026-10-09T00:00:00+00:00')"
    )
    db.conn.execute(
        "INSERT INTO project (id, project_key, trade, city, permit_date, "
        "classification, procurement_status, created_at, updated_at) "
        "VALUES (1, 'soak-1', 'commercial_hvac', 'Dallas', '2026-10-01', "
        "'HIGH', 'Confirmed open', '2026-10-09T00:00:00+00:00', '2026-10-09T00:00:00+00:00')"
    )
    db.conn.commit()
    db.close()

    errors: list[str] = []
    stop = threading.Event()
    counters = {"ingest_commits": 0, "reads": 0, "watchlist_writes": 0, "note_writes": 0}
    duration = args.seconds

    def ingest_writer() -> None:
        writer = Database(path)
        try:
            end = time.monotonic() + duration
            i = 0
            while time.monotonic() < end:
                writer.conn.execute("BEGIN")
                for _ in range(25):
                    writer.conn.execute(
                        "INSERT INTO permit (source_id, natural_key, permit_number, "
                        "updated_at) VALUES ('s1', ?, ?, '2026-10-09T00:00:00+00:00')",
                        (f"k{i}", f"P{i}"),
                    )
                    i += 1
                writer.conn.commit()
                counters["ingest_commits"] += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ingest: {exc!r}")
        finally:
            writer.close()

    def reader() -> None:
        conn = Database(path)
        try:
            while not stop.is_set():
                conn.conn.execute("SELECT COUNT(*) FROM permit").fetchone()
                counters["reads"] += 1
                time.sleep(0.01)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reader: {exc!r}")
        finally:
            conn.close()

    def web_write() -> None:
        """A watchlist toggle and a note insert, alternating - the two writes a signed-in
        user's request actually performs against the app tables."""
        conn = Database(path)
        try:
            n = 0
            while not stop.is_set():
                if n % 2 == 0:
                    conn.conn.execute(
                        "INSERT OR REPLACE INTO watched_opportunity "
                        "(user_id, project_id, watched_at) VALUES (1, 1, ?)",
                        (f"2026-10-09T00:00:{n % 60:02d}+00:00",),
                    )
                    counters["watchlist_writes"] += 1
                else:
                    conn.conn.execute(
                        "INSERT INTO opportunity_note (user_id, project_id, body, created_at) "
                        "VALUES (1, 1, ?, ?)",
                        (f"note {n}", f"2026-10-09T00:00:{n % 60:02d}+00:00"),
                    )
                    counters["note_writes"] += 1
                conn.conn.commit()
                n += 1
                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"web: {exc!r}")
        finally:
            conn.close()

    threads = [threading.Thread(target=ingest_writer)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    threads += [threading.Thread(target=web_write) for _ in range(2)]
    started = time.monotonic()
    for t in threads:
        t.start()
    time.sleep(duration)
    stop.set()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.monotonic() - started

    locked = [e for e in errors if "database is locked" in e]
    # Read the pragmas back from a fresh connection so the run proves the WAL/busy_timeout
    # configuration is actually in effect, not merely what db.py intends to set.
    probe = Database(path)
    pragmas = {
        name: probe.conn.execute(f"PRAGMA {name}").fetchone()[0]
        for name in ("journal_mode", "busy_timeout", "synchronous", "foreign_keys")
    }
    probe.close()
    print(f"duration:            {elapsed:.1f}s")
    print(f"ingest commits:      {counters['ingest_commits']}")
    print(f"reader queries:      {counters['reads']}")
    print(f"watchlist writes:    {counters['watchlist_writes']}")
    print(f"note writes:         {counters['note_writes']}")
    print(f"errors:              {len(errors)}")
    print(f"'database is locked': {len(locked)}")
    print("pragmas:             " + ", ".join(f"{k}={v}" for k, v in pragmas.items()))
    for e in errors:
        print("  ", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
