"""Service-boundary tests for the intelligence graph.

The Phase 2 rule is that the graph is reached through the service boundary, never by a route,
template or component touching the database. These tests assert the accessors return structured
intelligence and that a project's public visibility still governs what they expose.
"""

from __future__ import annotations

from datetime import date

import pytest

from oppintel.config import classify_trade, load_trade_taxonomy
from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.service import OpportunityService


@pytest.fixture
def graph_service(tmp_path):
    """A service over a database that carries named parties, so entities exist."""
    db = Database(tmp_path / "svc.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()

    def permit(number, address, owner, permit_type="Commercial Mechanical Permit",
               description="Mechanical remodel of spec suite"):
        return Permit(
            source_id="fort_worth_permits", permit_number=number, natural_key=number,
            permit_type=permit_type, permit_subtype="commercial_mechanical",
            permit_date=date(2026, 9, 1), status="Issued", address=address,
            city="Fort Worth", state="TX", work_description=description,
            land_use="OFFICE BUILDING", job_value=2_500_000.0, owner=owner,
            contractor="BUILDER CO", is_commercial=True,
            source_url=f"https://example.gov/{number}", source_date=date(2026, 9, 1),
        )

    for p in (
        permit("M1", "10 ROSS AVE", "ACME HEALTH LLC"),
        permit("M2", "20 ELM ST", "ACME HEALTH, L.L.C."),
        permit("M3", "30 OAK ST", "PLUMBING PARTNERS LLC",
               permit_type="Commercial Plumbing Permit", description="New plumbing for office"),
    ):
        db.upsert_permit(p, normalize_address(p.address))
    db.commit()
    pipeline.assemble_and_classify()

    from oppintel.config import active_market, active_trade

    svc = OpportunityService(db, active_market(), active_trade())
    yield svc, db
    db.close()


def test_service_exposes_the_intelligence_graph(fixture_db):
    from oppintel.config import active_market, active_trade

    svc = OpportunityService(fixture_db, active_market(), active_trade())
    project_id = fixture_db.conn.execute("SELECT id FROM project ORDER BY id LIMIT 1").fetchone()[0]

    project = svc.get_project(project_id)
    assert project is not None
    assert project["id"] == project_id

    # Every accessor returns without raising and returns a list/dict of the expected shape.
    assert isinstance(svc.get_project_events(project_id), list)
    assert isinstance(svc.get_project_companies(project_id), list)
    assert isinstance(svc.get_project_documents(project_id), list)
    assert isinstance(svc.get_project_trades(project_id), list)
    assert isinstance(svc.get_project_evidence(project_id), list)
    loc = svc.get_project_location(project_id)
    assert loc is None or isinstance(loc, dict)


def test_get_project_unknown_id_is_none(fixture_db):
    from oppintel.config import active_market, active_trade

    svc = OpportunityService(fixture_db, active_market(), active_trade())
    assert svc.get_project(999999) is None


def test_search_companies_reads_the_entity_graph(graph_service):
    svc, _db = graph_service
    companies = svc.search_companies(limit=100)
    assert companies, "the fixture dataset has resolved companies"
    assert all(c["display_name"] for c in companies)
    # Two spellings of ACME HEALTH resolve to a single company entity.
    acme = [c for c in companies if "acme" in c["display_name"].lower()]
    assert len(acme) == 1
    assert acme[0]["project_count"] == 2
    # A role filter selects only entities observed in that role.
    owners = svc.search_companies(role="owner", limit=100)
    assert owners and all("owner" in c["roles"] for c in owners)


def test_search_projects_respects_public_visibility(graph_service):
    svc, _db = graph_service
    rows = svc.search_projects(limit=100)
    for row in rows:
        assert row["classification"] in ("HIGH", "MEDIUM")
        assert row["procurement_status"] not in ("Closed",)


def test_search_projects_by_trade(graph_service):
    svc, _db = graph_service
    mechanical = svc.search_projects(trade_id="hvac_mechanical", limit=100)
    assert mechanical, "the fixture has mechanical projects"
    for row in mechanical:
        trades = {t["trade_id"] for t in svc.get_project_trades(row["id"])}
        assert "hvac_mechanical" in trades


def test_project_trades_and_events_are_exposed(graph_service):
    svc, db = graph_service
    project_id = db.conn.execute(
        "SELECT id FROM project WHERE address = '10 ROSS AVE'"
    ).fetchone()[0]
    assert {t["trade_id"] for t in svc.get_project_trades(project_id)} == {"hvac_mechanical"}
    event_types = {e["event_type"] for e in svc.get_project_events(project_id)}
    assert "PROJECT_DISCOVERED" in event_types
    assert svc.get_project_companies(project_id)


# --- trade taxonomy -----------------------------------------------------------

def test_taxonomy_has_the_required_trades():
    ids = {t.id for t in load_trade_taxonomy()}
    assert {
        "hvac_mechanical", "electrical", "plumbing", "fire_protection",
        "general_construction", "roofing", "concrete", "structural", "other_specialty",
    } <= ids


def test_taxonomy_is_not_hvac_specific():
    """The taxonomy must describe more than the one active trade."""
    assert len(load_trade_taxonomy()) >= 8


def test_classify_trade_picks_the_longest_keyword():
    assert classify_trade("Fire protection sprinkler system") == "fire_protection"
    assert classify_trade("Electrical service upgrade") == "electrical"
    assert classify_trade("Roof replacement") == "roofing"


def test_classify_trade_returns_none_for_unknown_text():
    assert classify_trade("General municipal filing") is None
    assert classify_trade("") is None
