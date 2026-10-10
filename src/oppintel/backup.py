"""Database backup for the mounted disk.

SQLite's online backup API (`sqlite3.Connection.backup`) is the programmatic form of the
`.backup` shell command: it copies a consistent snapshot even while the refresh loop is writing,
and it checkpoints the WAL into the snapshot. `VACUUM INTO` would also work but rewrites the
whole file and needs exclusive access, so the online API is used here.

Backups are dated files in `<data_dir>/backups/`. The newest `keep` are retained; older ones are
removed. Nothing outside `backups/` is touched.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

#: Warn (do not abort) when free space before a backup is below this. A backup that fills the
#: disk is worse than a late warning, so this is a threshold, not a gate.
DEFAULT_MIN_FREE_MB = 200


def _checkpoint(db_path: Path) -> None:
    """Fold the WAL back into the main database file before copying.

    A passive checkpoint does not block readers or the refresh writer; it just lets the backup
    snapshot see a current main file. It is best-effort: if another connection holds the lock the
    online backup API still produces a consistent snapshot, so a busy WAL is not an error.
    """
    try:
        con = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            con.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            con.close()
    except sqlite3.Error:
        pass


def _warn_if_low_disk(backup_dir: Path, snapshot_bytes: int, min_free_mb: int) -> None:
    """Log a warning when free space is below the threshold plus one snapshot's size."""
    try:
        free = shutil.disk_usage(str(backup_dir)).free
    except OSError:
        return
    needed = snapshot_bytes + min_free_mb * 1024 * 1024
    if free < needed:
        log.warning(
            "low disk before backup: %.0f MB free, need ~%.0f MB "
            "(snapshot %.0f MB + %d MB headroom). Backups on the same volume do not survive "
            "disk loss — copy them off-disk.",
            free / 1e6, needed / 1e6, snapshot_bytes / 1e6, min_free_mb,
        )


def create_backup(
    db_path: Path,
    backup_dir: Path,
    *,
    keep: int = 3,
    now: datetime | None = None,
    min_free_mb: int = DEFAULT_MIN_FREE_MB,
) -> Path:
    """Write a dated snapshot of ``db_path`` into ``backup_dir`` and prune to ``keep`` files.

    Returns the new backup path. Raises if the source database does not exist, so a scheduled
    backup of a missing file is a visible failure rather than an empty file.

    Before writing, a warning is logged (never raised) when the free space on the backup volume
    is below ``min_free_mb`` plus the size of the snapshot being written. The write still
    proceeds: a stale backup is often better than none, and the warning is the signal an operator
    (or an alert on the log) acts on. Same-volume backups do not survive the volume's loss, so an
    off-disk copy is required — see docs/deployment.md.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    _warn_if_low_disk(backup_dir, db_path.stat().st_size, min_free_mb)

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")
    target = backup_dir / f"oppintel-{stamp}.db"
    tmp = backup_dir / f".oppintel-{stamp}.db.tmp"

    _checkpoint(db_path)
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(tmp, target)

    prune_backups(backup_dir, keep=keep, protect=target)
    return target


def prune_backups(backup_dir: Path, *, keep: int, protect: Path | None = None) -> list[Path]:
    """Remove the oldest ``oppintel-*.db`` files beyond ``keep``. Never removes ``protect``."""
    files = sorted(
        backup_dir.glob("oppintel-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for index, path in enumerate(files):
        if index < keep or (protect is not None and path == protect):
            continue
        path.unlink()
        removed.append(path)
    return removed
