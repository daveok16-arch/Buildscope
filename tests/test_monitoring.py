"""Monitoring integrity audit.

Each test drives a real database into a broken condition and asserts that the audit reports
it. The audit is read-only, so the property under test is that it names the fault rather than
that it fixes it.
"""

from __future__ import annotations

from datetime import date

import pytest

from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.monitoring import (
    SEVERITY_FAULT,
    change_volume,
    duplicate_changes,
    mismatched_source_links,
    monitoring_report,
    stranded_changes,
    watch_summary,
)
from oppintel.pipeline import Pipeline


def _permit(**overrides) -> Permit:
    defaults = dict(
        source_id="fort_worth_permits",
        permit_number="PB1",
        natural_key="PB1",
        permit_type="Commercial Mechanical Permit",
        permit_subtype="commercial_mechanical",
        permit_date=date(2026, 9, 1),
        status="Issued",
        address="100 MAIN ST",
        city="Fort Worth",
        state="TX",
        work_description="New construction of medical office building",
        land_use="OFFICE BUILDING",
        job_value=5_000_000.0,
        is_commercial=True,
        source_url="https://example.gov/PB1",
        source_date=date(2026, 9, 1),
    )
    defaults.update(overrides)
    return Permit(**defaults)


@pytest.fixture
def monitored_db(tmp_path):
    db = Database(tmp_path / "monitor.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    db.upsert_permit(_permit(), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()
    yield db, pipeline
    db.close()


def _first_project_id(db) -> int:
    return int(db.conn.execute("SELECT id FROM project ORDER BY id LIMIT 1").fetchone()[0])


def test_healthy_database_reports_no_findings(monitored_db):
    db, _ = monitored_db
    report = monitoring_report(db)
    assert report["healthy"]
    assert report["findings"] == []
    assert report["volume"]["total"] == 1


def test_stranded_change_is_a_fault(monitored_db):
    db, _ = monitored_db
    project_id = _first_project_id(db)
    db.conn.execute(
        "INSERT INTO app_user (email, password_hash, created_at) VALUES ('u@example.com', 'x', '2026-01-01')"
    )
    user_id = int(db.conn.execute("SELECT id FROM app_user ORDER BY id DESC LIMIT 1").fetchone()[0])
    # A watch that has never advanced past a change that exists.
    db.conn.execute(
        "INSERT INTO watched_opportunity (user_id, project_id, watched_at, last_seen_change_id) "
        "VALUES (?, ?, '2026-01-01', NULL)",
        (user_id, project_id),
    )
    db.conn.commit()
    stranded = stranded_changes(db)
    assert len(stranded) == 1
    report = monitoring_report(db)
    assert not report["healthy"]
    assert any(f["issue"] == "stranded_changes" and f["severity"] == SEVERITY_FAULT
               for f in report["findings"])


def test_advanced_watch_is_not_stranded(monitored_db):
    db, _ = monitored_db
    project_id = _first_project_id(db)
    newest = int(db.conn.execute("SELECT MAX(id) FROM project_change").fetchone()[0])
    db.conn.execute(
        "INSERT INTO app_user (email, password_hash, created_at) VALUES ('u@example.com', 'x', '2026-01-01')"
    )
    user_id = int(db.conn.execute("SELECT id FROM app_user ORDER BY id DESC LIMIT 1").fetchone()[0])
    db.conn.execute(
        "INSERT INTO watched_opportunity (user_id, project_id, watched_at, last_seen_change_id) "
        "VALUES (?, ?, '2026-01-01', ?)",
        (user_id, project_id, newest),
    )
    db.conn.commit()
    assert stranded_changes(db) == []


def test_duplicate_change_rows_are_reported(monitored_db):
    db, _ = monitored_db
    project_id = _first_project_id(db)
    row = db.conn.execute("SELECT * FROM project_change LIMIT 1").fetchone()
    db.conn.execute(
        """
        INSERT INTO project_change (project_id, field_name, previous_value, current_value,
            change_kind, summary, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, row["field_name"], row["previous_value"], row["current_value"],
         row["change_kind"], row["summary"], row["detected_at"]),
    )
    db.conn.commit()
    dups = duplicate_changes(db)
    assert len(dups) == 1
    assert dups[0]["duplicates"] == 2


def test_mismatched_source_link_is_reported(monitored_db):
    db, _ = monitored_db
    project_id = _first_project_id(db)
    db.conn.execute(
        "UPDATE project SET source_url = 'https://example.gov/REAL' WHERE id = ?",
        (project_id,),
    )
    db.conn.execute(
        "UPDATE project_change SET source_url = 'https://example.gov/DIFFERENT' WHERE project_id = ?",
        (project_id,),
    )
    db.conn.commit()
    mismatched = mismatched_source_links(db)
    assert len(mismatched) == 1
    assert any(f["issue"] == "mismatched_source_links" for f in monitoring_report(db)["findings"])


def test_change_volume_splits_notifiable_from_routine(monitored_db):
    db, _ = monitored_db
    # The first-pass event is NEW_PROJECT, which is notifiable.
    volume = change_volume(db)
    assert volume["notifiable"] == 1
    assert volume["routine"] == 0
    assert volume["by_kind"]["new_project"] == 1


def test_watch_summary_counts_advanced_and_not(monitored_db):
    db, _ = monitored_db
    project_id = _first_project_id(db)
    newest = int(db.conn.execute("SELECT MAX(id) FROM project_change").fetchone()[0])
    db.conn.execute(
        "INSERT INTO app_user (email, password_hash, created_at) VALUES ('a@example.com', 'x', '2026-01-01')"
    )
    db.conn.execute(
        "INSERT INTO app_user (email, password_hash, created_at) VALUES ('b@example.com', 'x', '2026-01-01')"
    )
    ids = [int(r[0]) for r in db.conn.execute("SELECT id FROM app_user ORDER BY id").fetchall()]
    db.conn.execute(
        "INSERT INTO watched_opportunity (user_id, project_id, watched_at, last_seen_change_id) "
        "VALUES (?, ?, '2026-01-01', ?)", (ids[0], project_id, newest),
    )
    db.conn.execute(
        "INSERT INTO watched_opportunity (user_id, project_id, watched_at, last_seen_change_id) "
        "VALUES (?, ?, '2026-01-01', NULL)", (ids[1], project_id),
    )
    db.conn.commit()
    summary = watch_summary(db)
    assert summary["watches"] == 2
    assert summary["watches_with_changes"] == 1
    assert summary["watches_without_changes"] == 1
