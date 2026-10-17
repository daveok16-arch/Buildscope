"""Historical-versus-new permit event integrity (Phase 12, defects B and D).

The property under test: a permit the source already recorded as issued long ago must never be
presented — or alerted on — as a newly filed permit merely because BuildScope ingested it today.
Only a permit dated inside the configured freshness window is a `new_permit`; an older or
undated one is a `historical_permit`, which appears on the timeline but cannot notify.

These tests run the real pipeline over a real database. They do not mock the change detector or
the alert service, because the behaviour under test *is* their interaction.
"""

from __future__ import annotations

from datetime import date

import pytest

from oppintel.app.alerts import AlertService
from oppintel.changes import (
    HISTORICAL_PERMIT,
    NEW_PERMIT,
    NEW_PROJECT,
    NOTIFIABLE_KINDS,
    classify_added_permit,
    diff_project,
    snapshot_values,
)
from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline

TODAY = date(2026, 10, 17)
RECENT = date(2026, 9, 1)
OLD = date(2019, 5, 1)


def _permit(**overrides) -> Permit:
    defaults = dict(
        source_id="fort_worth_permits",
        permit_number="PB1",
        natural_key="PB1",
        permit_type="Commercial Mechanical Permit",
        permit_subtype="commercial_mechanical",
        permit_date=RECENT,
        status="Issued",
        address="100 MAIN ST",
        city="Fort Worth",
        state="TX",
        work_description="New construction of medical office building",
        land_use="OFFICE BUILDING",
        job_value=5_000_000.0,
        is_commercial=True,
        source_url="https://example.gov/PB1",
        source_date=RECENT,
    )
    defaults.update(overrides)
    return Permit(**defaults)


@pytest.fixture
def monitored_db(tmp_path):
    db = Database(tmp_path / "phase12.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    db.upsert_permit(_permit(), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()
    yield db, pipeline
    db.close()


def _changes(db) -> list[dict]:
    return [dict(r) for r in db.conn.execute(
        "SELECT * FROM project_change ORDER BY id"
    ).fetchall()]


def _project_id(db) -> int:
    return int(db.conn.execute("SELECT id FROM project LIMIT 1").fetchone()["id"])


def _make_user(db: Database, email: str = "watcher@example.com") -> int:
    from werkzeug.security import generate_password_hash

    cursor = db.conn.execute(
        """
        INSERT INTO app_user (email, password_hash, display_name, access_level, is_active,
            created_at)
        VALUES (?, ?, 'Watcher', 'FREE', 1, ?)
        """,
        (email, generate_password_hash("correct-horse-battery"), "2026-09-01T00:00:00+00:00"),
    )
    user_id = int(cursor.lastrowid)
    db.conn.execute(
        """
        INSERT INTO user_preference (user_id, market_id, trade_id, cities, project_types,
            notify_in_app, notify_email, updated_at)
        VALUES (?, 'dfw', 'commercial_hvac', '[]', '[]', 1, 0, '2026-09-01T00:00:00+00:00')
        """,
        (user_id,),
    )
    db.conn.commit()
    return user_id


# --- the policy, exercised directly ---------------------------------------------

def test_a_permit_dated_inside_the_window_is_a_new_permit():
    kind, summary = classify_added_permit(
        {"permit_number": "PB9", "permit_date": RECENT}, today=TODAY, recent_days=180
    )
    assert kind == NEW_PERMIT
    assert "PB9" in summary


def test_a_permit_dated_before_the_window_is_a_historical_import():
    kind, summary = classify_added_permit(
        {"permit_number": "PB9", "permit_date": OLD}, today=TODAY, recent_days=180
    )
    assert kind == HISTORICAL_PERMIT
    assert "2019-05-01" in summary


def test_a_permit_with_no_usable_date_is_never_claimed_as_new():
    """An undated record cannot be shown to be fresh, so it is treated as historical."""
    kind, _ = classify_added_permit({"permit_number": "PB9"}, today=TODAY, recent_days=180)
    assert kind == HISTORICAL_PERMIT


def test_historical_permit_is_not_alertable():
    assert HISTORICAL_PERMIT not in NOTIFIABLE_KINDS
    assert NEW_PERMIT in NOTIFIABLE_KINDS


# --- a backfill must not manufacture a current-new-permit alert ------------------

def test_an_old_permit_added_to_a_known_project_is_recorded_as_historical(monitored_db):
    db, pipeline = monitored_db
    db.upsert_permit(
        _permit(
            permit_number="PB-OLD",
            natural_key="PB-OLD",
            permit_date=OLD,
            source_date=OLD,
        ),
        normalize_address("100 MAIN ST"),
    )
    db.commit()
    pipeline.assemble_and_classify()

    kinds = {c["change_kind"] for c in _changes(db)}
    assert HISTORICAL_PERMIT in kinds, "the imported permit must still be recorded on the timeline"
    assert NEW_PERMIT not in kinds, "an old permit must not be recorded as a new filing"


def test_a_genuinely_new_permit_is_still_a_new_permit(monitored_db):
    db, pipeline = monitored_db
    db.upsert_permit(
        _permit(
            permit_number="PB-NEW",
            natural_key="PB-NEW",
            permit_date=date(2026, 10, 15),
            source_date=date(2026, 10, 15),
        ),
        normalize_address("100 MAIN ST"),
    )
    db.commit()
    pipeline.assemble_and_classify()

    kinds = {c["change_kind"] for c in _changes(db)}
    assert NEW_PERMIT in kinds, "a freshly filed permit must still be detected"


def test_an_old_permit_raises_no_alert_for_a_watcher(monitored_db):
    """The end-to-end guarantee: a backfilled historical permit is silent in the inbox."""
    from oppintel.app.workflow import WorkflowService

    db, pipeline = monitored_db
    user_id = _make_user(db)
    project_id = _project_id(db)
    WorkflowService(db).watch(user_id, project_id)

    db.upsert_permit(
        _permit(permit_number="PB-OLD", natural_key="PB-OLD", permit_date=OLD, source_date=OLD),
        normalize_address("100 MAIN ST"),
    )
    db.commit()
    pipeline.assemble_and_classify()

    AlertService(db).generate_from_changes()
    alerts = db.conn.execute(
        "SELECT kind, change_id FROM alert_event WHERE user_id = ?", (user_id,)
    ).fetchall()
    assert not [a for a in alerts if a["kind"] == "new_permit"], (
        "a historical permit must never produce a new-permit alert"
    )


def test_a_genuinely_new_permit_raises_an_alert(monitored_db):
    from oppintel.app.workflow import WorkflowService

    db, pipeline = monitored_db
    user_id = _make_user(db)
    project_id = _project_id(db)
    WorkflowService(db).watch(user_id, project_id)

    db.upsert_permit(
        _permit(
            permit_number="PB-NEW",
            natural_key="PB-NEW",
            permit_date=date(2026, 10, 15),
            source_date=date(2026, 10, 15),
        ),
        normalize_address("100 MAIN ST"),
    )
    db.commit()
    pipeline.assemble_and_classify()

    result = AlertService(db).generate_from_changes()
    assert result.created >= 1
    kinds = [a["kind"] for a in db.conn.execute(
        "SELECT kind FROM alert_event WHERE user_id = ?", (user_id,)
    ).fetchall()]
    assert "new_permit" in kinds


def test_a_first_pass_backfill_records_no_new_permit_change(monitored_db):
    """A whole-dataset backfill: every project is a first observation, none is a new filing."""
    db, _ = monitored_db
    kinds = {c["change_kind"] for c in _changes(db)}
    assert kinds == {NEW_PROJECT}


# --- idempotency (defect D) ------------------------------------------------------

def test_reingesting_the_same_record_creates_no_duplicate_change_or_event(monitored_db):
    db, pipeline = monitored_db
    before = len(_changes(db))

    # Same permit, ingested again (e.g. the next refresh returns the same row).
    db.upsert_permit(_permit(), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()

    assert len(_changes(db)) == before, "a re-ingest of an unchanged record must emit nothing"


def test_repeated_assembly_does_not_duplicate_events(monitored_db):
    db, pipeline = monitored_db
    project_id = _project_id(db)
    before = db.event_count(project_id)
    for _ in range(3):
        pipeline.assemble_and_classify()
    assert db.event_count(project_id) == before


def test_repeated_alert_generation_is_idempotent(monitored_db):
    from oppintel.app.workflow import WorkflowService

    db, pipeline = monitored_db
    user_id = _make_user(db)
    project_id = _project_id(db)
    WorkflowService(db).watch(user_id, project_id)
    db.upsert_permit(
        _permit(
            permit_number="PB-NEW",
            natural_key="PB-NEW",
            permit_date=date(2026, 10, 15),
            source_date=date(2026, 10, 15),
        ),
        normalize_address("100 MAIN ST"),
    )
    db.commit()
    pipeline.assemble_and_classify()

    first = AlertService(db).generate_from_changes()
    second = AlertService(db).generate_from_changes()
    assert first.created >= 1
    assert second.created == 0, "the same change must not alert twice"
    assert AlertService(db).alerts_without_event() == []


# --- event time versus ingestion time -------------------------------------------

def test_a_permit_event_is_dated_by_the_source_not_by_ingestion(monitored_db):
    """`occurred_at` is the permit's own date; `recorded_at` is when we observed it."""
    db, _ = monitored_db
    project_id = _project_id(db)
    row = db.conn.execute(
        "SELECT occurred_at, recorded_at FROM project_event "
        "WHERE project_id = ? AND event_type = 'PERMIT_RECORDED'",
        (project_id,),
    ).fetchone()
    assert row is not None
    assert row["occurred_at"] == RECENT.isoformat(), "the event is dated by the permit date"
    assert row["recorded_at"] != row["occurred_at"], (
        "the observation time is kept separate from the source date"
    )


def test_the_discovery_event_is_dated_by_observation_not_the_permit(monitored_db):
    """Discovery is our observation, so it is dated by `created_at`, not by the permit date."""
    db, _ = monitored_db
    project_id = _project_id(db)
    row = db.conn.execute(
        "SELECT occurred_at, recorded_at FROM project_event "
        "WHERE project_id = ? AND event_type = 'PROJECT_DISCOVERED'",
        (project_id,),
    ).fetchone()
    assert row is not None
    assert row["recorded_at"], "discovery must carry an observation timestamp"
    assert (row["occurred_at"] or "") != OLD.isoformat()


# --- the diff function, exercised directly with the configured window -----------

def test_diff_marks_an_old_added_permit_as_historical():
    project = {"id": 1, "address": "10 ROSS AVE"}
    previous = snapshot_values(project, permits=[])
    permits = [{
        "natural_key": "X1", "permit_number": "X1",
        "permit_date": OLD.isoformat(), "source_id": "s",
    }]
    changes = diff_project(project, previous, permits, recent_days=180, today=TODAY)
    assert [c.change_kind for c in changes] == [HISTORICAL_PERMIT]
    assert changes[0].is_notifiable is False


def test_diff_marks_a_recent_added_permit_as_new():
    project = {"id": 1, "address": "10 ROSS AVE"}
    previous = snapshot_values(project, permits=[])
    permits = [{
        "natural_key": "X2", "permit_number": "X2",
        "permit_date": RECENT.isoformat(), "source_id": "s",
    }]
    changes = diff_project(project, previous, permits, recent_days=180, today=TODAY)
    assert [c.change_kind for c in changes] == [NEW_PERMIT]
    assert changes[0].is_notifiable is True
