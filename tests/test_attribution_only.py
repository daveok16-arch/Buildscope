"""Attribution-only enrichment safety (Phase 12, defect C).

Attribution-only means: add a participant or source attribution to a record the system already
holds, without inventing a relationship and without manufacturing a false "new project" event.

The mechanism that makes this safe already exists and is reused rather than rebuilt:

* project identity is `project_key = sha1(trade|address_key)`, so enriching a permit at a known
  address updates the same project row in place — it does not create a second project;
* every asserted field flows through `provenance.assert_field`, which refuses a value with no
  identified source and records the evidence that licenses it;
* the evidence set and party rows are rebuilt from the permits on every pass, so a repeated
  enrichment converges instead of accumulating.

These tests assert those properties against a real database. Where a requirement cannot be met
without a schema change or a broad redesign, the report documents the blocker; nothing here
invents a participant or a role.
"""

from __future__ import annotations

from datetime import date

import pytest

from oppintel.changes import NEW_PROJECT
from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline

RECENT = date(2026, 9, 1)


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
def attribution_db(tmp_path):
    db = Database(tmp_path / "attribution.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    db.upsert_permit(_permit(), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()
    yield db, pipeline
    db.close()


def _project_id(db) -> int:
    return int(db.conn.execute("SELECT id FROM project LIMIT 1").fetchone()["id"])


def _project_count(db) -> int:
    return int(db.conn.execute("SELECT COUNT(*) FROM project").fetchone()[0])


def _parties(db, project_id: int) -> list[dict]:
    return [dict(r) for r in db.conn.execute(
        "SELECT role, name, source_id FROM project_party WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()]


# --- enriching a known record does not create a second project -------------------

def test_adding_an_owner_to_a_known_permit_keeps_one_project(attribution_db):
    """The project is keyed on the address, so new attribution updates it in place."""
    db, pipeline = attribution_db
    before = _project_count(db)
    project_id = _project_id(db)

    db.upsert_permit(
        _permit(owner="ACME HOLDINGS LLC"), normalize_address("100 MAIN ST")
    )
    db.commit()
    pipeline.assemble_and_classify()

    assert _project_count(db) == before, "attribution must not fork a second project"
    assert _project_id(db) == project_id


def test_attribution_only_update_raises_no_new_project_event(attribution_db):
    db, pipeline = attribution_db
    db.upsert_permit(_permit(owner="ACME HOLDINGS LLC"), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()

    kinds = [r["change_kind"] for r in db.conn.execute(
        "SELECT change_kind FROM project_change ORDER BY id"
    ).fetchall()]
    # The only new_project row is the original first observation.
    assert kinds.count(NEW_PROJECT) == 1, "enrichment must not re-announce the project"


def test_attribution_is_evidenced_and_records_the_source(attribution_db):
    db, pipeline = attribution_db
    project_id = _project_id(db)
    db.upsert_permit(_permit(owner="ACME HOLDINGS LLC"), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()

    owner_evidence = [dict(r) for r in db.conn.execute(
        "SELECT source_id, source_record_key, excerpt FROM evidence "
        "WHERE project_id = ? AND field_name = 'owner'",
        (project_id,),
    ).fetchall()]
    assert owner_evidence, "an asserted owner must carry evidence"
    assert owner_evidence[0]["source_id"] == "fort_worth_permits"
    assert owner_evidence[0]["source_record_key"] == "PB1"


def test_a_contractor_role_is_recorded_only_when_the_source_states_it(attribution_db):
    """No source value means no party row — a role is never inferred."""
    db, pipeline = attribution_db
    project_id = _project_id(db)

    # The permit as ingested carries no contractor.
    assert not [p for p in _parties(db, project_id) if p["role"] == "general_contractor"]

    # The source later publishes a contractor of record.
    db.upsert_permit(
        _permit(contractor="BUILDRIGHT CONSTRUCTION LP"),
        normalize_address("100 MAIN ST"),
    )
    db.commit()
    pipeline.assemble_and_classify()

    contractors = [p for p in _parties(db, project_id) if p["role"] == "general_contractor"]
    assert len(contractors) == 1
    assert contractors[0]["name"] == "BUILDRIGHT CONSTRUCTION LP"
    assert contractors[0]["source_id"] == "fort_worth_permits"


# --- repeated enrichment converges (idempotency) --------------------------------

def test_repeated_enrichment_does_not_duplicate_parties_or_evidence(attribution_db):
    db, pipeline = attribution_db
    project_id = _project_id(db)

    for _ in range(3):
        db.upsert_permit(
            _permit(owner="ACME HOLDINGS LLC", contractor="BUILDRIGHT CONSTRUCTION LP"),
            normalize_address("100 MAIN ST"),
        )
        db.commit()
        pipeline.assemble_and_classify()

    parties = _parties(db, project_id)
    owner_rows = [p for p in parties if p["role"] == "owner"]
    contractor_rows = [p for p in parties if p["role"] == "general_contractor"]
    assert len(owner_rows) == 1, "a repeated pass must not accumulate party rows"
    assert len(contractor_rows) == 1

    evidence = db.conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE project_id = ? AND field_name = 'owner'",
        (project_id,),
    ).fetchone()[0]
    assert evidence == 1, "the evidence set is rebuilt, not appended to"


def test_entity_resolution_is_idempotent_across_repeated_enrichment(attribution_db):
    db, pipeline = attribution_db
    project_id = _project_id(db)
    db.upsert_permit(
        _permit(contractor="BUILDRIGHT CONSTRUCTION LP"), normalize_address("100 MAIN ST")
    )
    db.commit()
    pipeline.assemble_and_classify()
    links_before = db.conn.execute(
        "SELECT COUNT(*) FROM entity_project WHERE project_id = ?", (project_id,)
    ).fetchone()[0]

    for _ in range(2):
        pipeline.assemble_and_classify()

    links_after = db.conn.execute(
        "SELECT COUNT(*) FROM entity_project WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    assert links_after == links_before, "an unchanged entity link must not be duplicated"


# --- a new participant record at the same address is a record event, not a new project

def test_a_second_permit_adding_a_participant_is_a_record_event_not_a_new_project(
    attribution_db,
):
    """A genuinely new permit is a `new_permit`/`historical_permit` event on the existing
    project — never a second project for the same address."""
    db, pipeline = attribution_db
    project_id = _project_id(db)
    before = _project_count(db)

    db.upsert_permit(
        _permit(
            permit_number="PB2",
            natural_key="PB2",
            permit_type="Commercial Building Permit",
            permit_subtype=None,
            contractor="BUILDRIGHT CONSTRUCTION LP",
            permit_date=date(2026, 9, 20),
            source_date=date(2026, 9, 20),
        ),
        normalize_address("100 MAIN ST"),
    )
    db.commit()
    pipeline.assemble_and_classify()

    assert _project_count(db) == before
    kinds = [r["change_kind"] for r in db.conn.execute(
        "SELECT change_kind FROM project_change ORDER BY id"
    ).fetchall()]
    assert NEW_PROJECT not in kinds[1:], "a second permit must not re-announce the project"
