"""Intelligence-graph tests.

These drive the real pipeline against a real database and assert the properties the Phase 2
model promises: durable project identity, immutable evidence history, deterministic entity
resolution, evidence-driven events, normalized locations, configuration-driven trades, and a
procurement status that stays separate from discovery. No mocks.
"""

from __future__ import annotations

from datetime import date

import pytest

from oppintel.db import Database
from oppintel.intelligence import integrity_report, run_backfill
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline


def _permit(**overrides) -> Permit:
    defaults = dict(
        source_id="fort_worth_permits",
        permit_number="PB1",
        natural_key="PB1",
        permit_type="Commercial Building Permit",
        permit_subtype="New",
        permit_date=date(2026, 9, 1),
        status="Issued",
        address="100 MAIN ST",
        city="Fort Worth",
        state="TX",
        zip_code="76102",
        work_description="New construction of medical office building",
        land_use="OFFICE BUILDING",
        job_value=5_000_000.0,
        square_footage=40_000.0,
        owner="ACME HEALTH LLC",
        contractor="BUILDER CO",
        is_commercial=True,
        source_url="https://example.gov/PB1",
        source_date=date(2026, 9, 1),
    )
    defaults.update(overrides)
    return Permit(**defaults)


@pytest.fixture
def graph_db(tmp_path):
    db = Database(tmp_path / "graph.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    return db, pipeline


def _assemble(db, pipeline, *permits):
    for permit in permits:
        db.upsert_permit(permit, normalize_address(permit.address))
    db.commit()
    pipeline.assemble_and_classify()


def _first_project(db):
    return dict(db.conn.execute("SELECT * FROM project ORDER BY id LIMIT 1").fetchone())


# --- project identity ---------------------------------------------------------

def test_project_identity_is_stable_across_reassembly(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    first = _first_project(db)
    key = first["project_key"]

    # Re-run assembly over the same permit: identity must not move.
    pipeline.assemble_and_classify()
    again = _first_project(db)
    assert again["id"] == first["id"]
    assert again["project_key"] == key
    assert db.conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 1


def test_distinct_addresses_get_distinct_identities(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_number="A", natural_key="A", address="100 MAIN ST"),
        _permit(permit_number="B", natural_key="B", address="200 OTHER ST"),
    )
    keys = {r["project_key"] for r in db.conn.execute("SELECT project_key FROM project")}
    assert len(keys) == 2


# --- evidence immutability and provenance -------------------------------------

def test_evidence_history_is_appended_not_overwritten(graph_db):
    """A changed value appends a new observation; the prior one survives."""
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit(job_value=5_000_000.0))
    project_id = _first_project(db)["id"]

    first_count = db.evidence_history_count(project_id)
    assert first_count > 0

    # The source now declares a different value on the same permit.
    db.upsert_permit(_permit(job_value=7_500_000.0), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()

    rows = db.evidence_history_for(project_id, field_name="estimated_project_value")
    values = {r["value"] for r in rows}
    assert "5000000.0" in values, "the original observation must be retained"
    assert "7500000.0" in values, "the new observation must be appended"
    assert db.evidence_history_count(project_id) > first_count


def test_reobserving_the_same_fact_does_not_duplicate_history(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project_id = _first_project(db)["id"]
    before = db.evidence_history_count(project_id)

    pipeline.assemble_and_classify()
    assert db.evidence_history_count(project_id) == before


def test_evidence_history_preserves_source_url(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project_id = _first_project(db)["id"]
    rows = db.evidence_history_for(project_id, field_name="permit_number")
    assert rows and rows[0]["source_url"] == "https://example.gov/PB1"
    assert rows[0]["source_id"] == "fort_worth_permits"


# --- entity resolution --------------------------------------------------------

def test_company_names_resolve_to_one_entity_across_spellings(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_number="A", natural_key="A", address="100 MAIN ST",
                owner="ACME HEALTH LLC"),
        _permit(permit_number="B", natural_key="B", address="200 OTHER ST",
                owner="Acme Health, L.L.C."),
    )
    rows = db.conn.execute(
        "SELECT display_name, canonical_key FROM entity WHERE entity_type = 'company'"
    ).fetchall()
    acme = [r for r in rows if "acme" in r["canonical_key"]]
    assert len(acme) == 1, "the two spellings must resolve to one entity"
    # Both observed spellings are preserved as variants.
    variants = db.conn.execute("SELECT COUNT(*) FROM entity_name").fetchone()[0]
    assert variants >= 2


def test_ambiguous_companies_are_not_merged(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_number="A", natural_key="A", address="100 MAIN ST",
                owner="SHELTON LANDMARK", contractor=None),
        _permit(permit_number="B", natural_key="B", address="200 OTHER ST",
                owner="SHELTON LANDMARK FOUNDATION", contractor=None),
    )
    keys = {
        r["canonical_key"]
        for r in db.conn.execute("SELECT canonical_key FROM entity")
    }
    assert len(keys) == 2, "a distinguishing word must keep the entities separate"


def test_company_role_comes_from_evidence(graph_db):
    """A name on a permit is recorded with the role the source stated, not assumed."""
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit(owner="ACME HEALTH LLC", contractor="BUILDER CO"))
    project_id = _first_project(db)["id"]
    links = db.entities_for_project(project_id)
    roles = {(r["display_name"], r["role"]) for r in links}
    assert ("ACME HEALTH LLC", "owner") in roles
    assert ("BUILDER CO", "general_contractor") in roles


def test_noise_names_do_not_become_entities(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit(owner="N/A", contractor=None))
    count = db.conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    assert count == 0


# --- locations ----------------------------------------------------------------

def test_location_is_normalized_and_preserves_raw_address(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit(address="100 MAIN ST, Suite 400"))
    project_id = _first_project(db)["id"]
    loc = db.location_for_project(project_id)
    assert loc["raw_address"] == "100 MAIN ST, Suite 400", "the raw address is preserved"
    assert loc["normalized_address"]
    assert loc["building_key"]


def test_location_has_no_fabricated_coordinates(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project_id = _first_project(db)["id"]
    loc = db.location_for_project(project_id)
    assert loc["latitude"] is None and loc["longitude"] is None
    assert loc["geocode_source"] is None


def test_coordinates_require_provenance(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project_id = _first_project(db)["id"]
    with pytest.raises(ValueError):
        db.upsert_project_location(
            project_id, {"raw_address": "x", "latitude": 32.7, "longitude": -97.3}
        )


# --- trades -------------------------------------------------------------------

def test_trade_taxonomy_classifies_mechanical(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_type="Commercial Mechanical Permit",
                work_description="Mechanical remodel of spec suite"),
    )
    project_id = _first_project(db)["id"]
    trades = {t["trade_id"] for t in db.trades_for_project(project_id)}
    assert "hvac_mechanical" in trades


def test_trade_taxonomy_classifies_a_non_hvac_trade(graph_db):
    """The model is not HVAC-only: a plumbing record carries a plumbing trade."""
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_type="Commercial Plumbing Permit", permit_subtype="commercial_plumbing",
                work_description="New plumbing for new construction office building"),
    )
    project_id = _first_project(db)["id"]
    trades = {t["trade_id"] for t in db.trades_for_project(project_id)}
    assert "plumbing" in trades


def test_project_with_no_trade_text_has_no_trade(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit(permit_type="Commercial Building Permit",
                                    work_description="General work"))
    project_id = _first_project(db)["id"]
    assert db.trades_for_project(project_id) == []


# --- events -------------------------------------------------------------------

def test_project_discovered_event_is_created_once(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project_id = _first_project(db)["id"]
    types = [e["event_type"] for e in db.events_for_project(project_id)]
    assert "PROJECT_DISCOVERED" in types
    assert "PERMIT_RECORDED" in types


def test_no_new_events_when_nothing_changed(graph_db):
    """The core event rule: no change means no event."""
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project_id = _first_project(db)["id"]
    before = db.event_count(project_id)

    pipeline.assemble_and_classify()
    assert db.event_count(project_id) == before


def test_scope_event_requires_mechanical_evidence(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_type="Commercial Mechanical Permit",
                work_description="Mechanical remodel of spec suite"),
    )
    project_id = _first_project(db)["id"]
    types = {e["event_type"] for e in db.events_for_project(project_id)}
    assert "SCOPE_IDENTIFIED" in types


def test_no_scope_event_without_mechanical_evidence(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_type="Commercial Plumbing Permit", permit_subtype="commercial_plumbing",
                work_description="New plumbing for new construction office building"),
    )
    project_id = _first_project(db)["id"]
    types = {e["event_type"] for e in db.events_for_project(project_id)}
    assert "SCOPE_IDENTIFIED" not in types


def test_timeline_is_ordered_oldest_first(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_number="EARLY", natural_key="EARLY", permit_date=date(2026, 1, 4)),
        _permit(permit_number="LATE", natural_key="LATE", permit_date=date(2026, 3, 11)),
    )
    # Both permits are at the same address, so they assemble into one project.
    project_id = _first_project(db)["id"]
    events = db.events_for_project(project_id)
    occurred = [e["occurred_at"] for e in events if e["occurred_at"]]
    assert occurred == sorted(occurred)


def test_detected_change_becomes_a_project_revised_event(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit(job_value=1_000_000.0))
    project_id = _first_project(db)["id"]

    db.upsert_permit(_permit(job_value=9_000_000.0), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()

    types = {e["event_type"] for e in db.events_for_project(project_id)}
    assert "PROJECT_REVISED" in types


# --- procurement separation ---------------------------------------------------

def test_procurement_status_is_never_confirmed_open_without_evidence(graph_db):
    """A permit existing does not mean bidding is open."""
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project = _first_project(db)
    assert project["procurement_status"] != "Confirmed open"


def test_company_identified_event_from_a_party(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit(owner="ACME HEALTH LLC"))
    project_id = _first_project(db)["id"]
    types = {e["event_type"] for e in db.events_for_project(project_id)}
    assert "COMPANY_IDENTIFIED" in types


# --- backfill and integrity ---------------------------------------------------

def test_backfill_links_existing_projects_and_preserves_counts(graph_db):
    db, pipeline = graph_db
    _assemble(
        db, pipeline,
        _permit(permit_number="A", natural_key="A", address="100 MAIN ST"),
        _permit(permit_number="B", natural_key="B", address="200 OTHER ST"),
    )
    before = {
        "project": db.conn.execute("SELECT COUNT(*) FROM project").fetchone()[0],
        "permit": db.conn.execute("SELECT COUNT(*) FROM permit").fetchone()[0],
        "evidence": db.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
    }
    report = run_backfill(db)
    after = {
        "project": db.conn.execute("SELECT COUNT(*) FROM project").fetchone()[0],
        "permit": db.conn.execute("SELECT COUNT(*) FROM permit").fetchone()[0],
        "evidence": db.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
    }
    assert report.projects_considered == 2
    assert report.projects_linked == 2
    assert before == after, "backfill must not add or remove intelligence records"
    assert report.locations_written == 2
    # The pipeline already derived these projects, so the backfill re-derives the same facts and
    # inserts no new events — the idempotency the event model promises.
    assert report.events_created == 0
    total_events = db.conn.execute("SELECT COUNT(*) FROM project_event").fetchone()[0]
    assert total_events > 0


def test_backfill_is_idempotent(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    run_backfill(db)
    events_after_first = db.conn.execute("SELECT COUNT(*) FROM project_event").fetchone()[0]
    entities_after_first = db.conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]

    run_backfill(db)
    assert db.conn.execute("SELECT COUNT(*) FROM project_event").fetchone()[0] == events_after_first
    assert db.conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == entities_after_first


def test_integrity_report_is_healthy_on_a_clean_graph(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    run_backfill(db)
    report = integrity_report(db)
    assert report["healthy"], report["findings"]
    assert report["counts"]["events"] > 0
    assert report["counts"]["evidence_history"] > 0


def test_schema_upgrade_on_a_populated_database_preserves_records(graph_db):
    """Re-initialising the schema on a populated database adds the graph tables and loses nothing.

    This is the migration path for a host whose database predates Phase 2: the new tables are
    created with `IF NOT EXISTS`, and no existing row is altered or removed.
    """
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    before = {
        table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("project", "permit", "evidence", "project_party")
    }

    db.init_schema()
    db.init_app_schema()

    after = {
        table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("project", "permit", "evidence", "project_party")
    }
    assert before == after, "re-initialising the schema must not change existing records"

    # The new intelligence tables exist and are queryable.
    for table in (
        "entity", "entity_name", "entity_project", "project_location", "document",
        "project_event", "evidence_history", "project_trade",
    ):
        db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()


def test_integrity_report_detects_fabricated_coordinates(graph_db):
    db, pipeline = graph_db
    _assemble(db, pipeline, _permit())
    project_id = _first_project(db)["id"]
    # Write a coordinate directly, bypassing the provenance guard, to prove the audit catches it.
    db.conn.execute(
        "UPDATE project_location SET latitude = 32.7, longitude = -97.3 WHERE project_id = ?",
        (project_id,),
    )
    db.commit()
    report = integrity_report(db)
    assert not report["healthy"]
    assert any(f["check"] == "coords_without_provenance" for f in report["findings"])
