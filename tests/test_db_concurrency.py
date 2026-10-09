"""SQLite safety tests.

The refresh thread writes to the database while the web workers may also write (watch, save,
notes, migrations) and read. WAL and a busy timeout are what keep a writer from surfacing as a
"database is locked" error. These tests build a real database on disk and exercise real
concurrent readers against a real writer — no mocks.
"""

from __future__ import annotations

import threading

from oppintel.db import Database


def test_database_enables_wal_and_a_busy_timeout(tmp_path):
    db = Database(tmp_path / "safety.db")
    db.init_schema()
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = db.conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 30000
    db.close()


def test_concurrent_readers_survive_a_writer(tmp_path):
    path = tmp_path / "concurrent.db"
    db = Database(path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO source (id, name, kind, market_coverage, updated_at) "
        "VALUES ('s1', 'Source', 'arcgis', 'DFW', '2026-10-09T00:00:00+00:00')"
    )
    db.conn.commit()
    db.close()

    errors: list[str] = []
    stop = threading.Event()

    def do_writes() -> None:
        writer = Database(path)
        try:
            for i in range(200):
                writer.conn.execute(
                    "INSERT INTO permit (source_id, natural_key, permit_number, "
                    "updated_at) VALUES ('s1', ?, ?, '2026-10-09T00:00:00+00:00')",
                    (f"k{i}", f"P{i}"),
                )
                writer.conn.commit()
        except Exception as exc:  # pragma: no cover - only on a locking failure
            errors.append(f"writer: {exc}")
        finally:
            writer.close()
            stop.set()

    def do_reads() -> None:
        reader = Database(path)
        try:
            while not stop.is_set():
                reader.conn.execute("SELECT COUNT(*) FROM permit").fetchone()
        except Exception as exc:  # pragma: no cover - only on a locking failure
            errors.append(f"reader: {exc}")
        finally:
            reader.close()

    threads = [threading.Thread(target=do_writes)]
    threads += [threading.Thread(target=do_reads) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
