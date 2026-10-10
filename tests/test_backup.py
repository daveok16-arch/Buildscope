"""Database backup tests.

`create_backup` copies a live SQLite database with the online backup API and prunes to the
newest N files. These tests prove the snapshot is a valid database with the same rows, and that
pruning never removes more than it should (and never the file it just wrote).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oppintel.backup import create_backup, prune_backups


def _make_db(path: Path, rows: int = 3) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO project (name) VALUES (?)", [(f"p{i}",) for i in range(rows)])
    con.commit()
    con.close()


def test_backup_is_a_valid_copy(tmp_path):
    db = tmp_path / "oppintel.db"
    _make_db(db, rows=7)
    out = create_backup(db, tmp_path / "backups", keep=7)
    assert out.exists()
    con = sqlite3.connect(str(out))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 7
    finally:
        con.close()


def test_backup_keeps_only_the_newest_n(tmp_path):
    db = tmp_path / "oppintel.db"
    _make_db(db)
    backup_dir = tmp_path / "backups"
    base = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        create_backup(db, backup_dir, keep=3, now=base + timedelta(minutes=i))
        # A distinct stamp per call; the prune runs each time.
    kept = sorted(p.name for p in backup_dir.glob("oppintel-*.db"))
    assert len(kept) == 3
    # The three newest stamps survive: minutes 02, 03, 04.
    assert kept == [f"oppintel-20261009T120{i}00.db" for i in (2, 3, 4)]


def test_prune_never_removes_the_protected_file(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    files = [backup_dir / f"oppintel-20261009T1200{i}0.db" for i in range(5)]
    for f in files:
        f.write_bytes(b"x")
    protected = files[0]  # oldest
    removed = prune_backups(backup_dir, keep=1, protect=protected)
    assert protected.exists()
    assert protected not in removed


def test_backup_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_backup(tmp_path / "nope.db", tmp_path / "backups")


def test_default_keep_is_three(tmp_path, monkeypatch, caplog):
    """The default retention is small enough for a 1 GB Render disk (keep keeps count * snapshot)."""
    import inspect

    from oppintel import backup as backup_mod

    sig = inspect.signature(backup_mod.create_backup)
    assert sig.parameters["keep"].default == 3


def test_low_disk_warns_but_still_writes(tmp_path, caplog, monkeypatch):
    db = tmp_path / "oppintel.db"
    _make_db(db)
    # Force the free-space probe to report 1 MB, far below the threshold.
    monkeypatch.setattr(
        "oppintel.backup.shutil.disk_usage",
        lambda _p: type("U", (), {"free": 1_000_000, "total": 1_000_000, "used": 0})(),
    )
    with caplog.at_level("WARNING", logger="oppintel.backup"):
        out = create_backup(db, tmp_path / "backups", min_free_mb=200)
    assert out.exists(), "a low-disk warning must not abort the backup"
    assert any("low disk before backup" in r.message for r in caplog.records)

