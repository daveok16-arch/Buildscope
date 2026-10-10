"""Tests for Company & Stakeholder Intelligence.

The filter tests exist because the filtered directory used to fail: `list_companies` built one
`WHERE` fragment and string-substituted it into each branch of the union, which duplicated the
placeholders without duplicating their bindings. Every filtered query raised
`sqlite3.ProgrammingError` and the route answered 500. These tests pin the corrected behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest_app import _permit
from oppintel.app.config import AppConfig
from oppintel.app.main import create_app
from oppintel.companies import CompanyService, clean_company_name
from oppintel.db import Database
from oppintel.models import normalize_address
from oppintel.pipeline import Pipeline
from oppintel.search_index import rebuild_index
from oppintel.slugs import ensure_slugs

#: Two owners and two general contractors across two cities, so each filter can be shown to
#: select a strict subset. ACME appears in both cities and both roles; BUILDER CO appears in
#: both cities as a contractor only.
_STAKEHOLDER_PERMITS = [
    dict(permit_number="A1", natural_key="A1", address="100 MAIN ST", city="Fort Worth",
         owner="ACME HEALTH LLC", contractor="BUILDER CO"),
    dict(permit_number="A2", natural_key="A2", address="200 OAK AVE", city="Dallas",
         owner="ACME HEALTH LLC", contractor="RIVAL BUILDERS"),
    dict(permit_number="A3", natural_key="A3", address="300 ELM ST", city="Fort Worth",
         owner="TARRANT COUNTY", contractor="BUILDER CO"),
]


@pytest.fixture
def stakeholder_db(tmp_path):
    """A database whose assembled projects carry owners and contractors."""
    db = Database(tmp_path / "stakeholders.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    for overrides in _STAKEHOLDER_PERMITS:
        permit = _permit(
            permit_type="Commercial Mechanical Permit",
            permit_subtype="commercial_mechanical",
            work_description="Mechanical remodel of spec suite",
            land_use="",
            job_value=2_000_000.0,
            square_footage=20_000.0,
            **overrides,
        )
        db.upsert_permit(permit, normalize_address(permit.address))
    db.commit()
    pipeline.assemble_and_classify()
    ensure_slugs(db)
    rebuild_index(db)
    yield db
    db.close()


@pytest.fixture
def stakeholder_client(stakeholder_db, tmp_path):
    """A Flask client over the stakeholder database."""
    cfg = AppConfig(database_path=tmp_path / "stakeholders.db", secret_key="t", debug=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


def test_clean_company_name():
    assert clean_company_name("  DPR Construction  ") == "DPR Construction"
    assert clean_company_name('"Turner Construction"') == "Turner Construction"
    assert clean_company_name("N/A") is None
    assert clean_company_name("UNKNOWN") is None
    assert clean_company_name("none") is None


def test_list_companies_from_app_db(app_db):
    service = CompanyService(app_db)
    companies = service.list_companies(limit=10)
    assert isinstance(companies, list)
    for c in companies:
        assert c.name
        assert c.slug
        assert c.project_count >= 1


# --- filtered queries (regression: previously HTTP 500) ------------------------

def test_unfiltered_listing_returns_every_stakeholder(stakeholder_db):
    names = {c.name for c in CompanyService(stakeholder_db).list_companies(limit=50)}
    assert names == {"ACME HEALTH LLC", "BUILDER CO", "RIVAL BUILDERS", "TARRANT COUNTY"}


def test_each_single_filter_returns_a_subset_without_error(stakeholder_db):
    """The bug: any one filter raised sqlite3.ProgrammingError. Each must now succeed."""
    service = CompanyService(stakeholder_db)
    assert {c.name for c in service.list_companies(role="owner", limit=50)} == {
        "ACME HEALTH LLC", "TARRANT COUNTY"
    }
    assert {c.name for c in service.list_companies(role="contractor", limit=50)} == {
        "BUILDER CO", "RIVAL BUILDERS"
    }
    assert {c.name for c in service.list_companies(q="acme", limit=50)} == {"ACME HEALTH LLC"}
    assert {c.name for c in service.list_companies(city="Dallas", limit=50)} == {
        "ACME HEALTH LLC", "RIVAL BUILDERS"
    }


def test_combined_filters_apply_together(stakeholder_db):
    """Two and three filters at once must each contribute their own binding."""
    service = CompanyService(stakeholder_db)
    assert {c.name for c in service.list_companies(role="owner", q="acme", limit=50)} == {
        "ACME HEALTH LLC"
    }
    assert {c.name for c in service.list_companies(
        role="owner", city="Fort Worth", limit=50
    )} == {"ACME HEALTH LLC", "TARRANT COUNTY"}
    assert {c.name for c in service.list_companies(
        role="owner", q="acme", city="Dallas", limit=50
    )} == {"ACME HEALTH LLC"}
    # A combination that matches nothing is an empty list, not an error.
    assert service.list_companies(role="contractor", city="Austin", limit=50) == []


def test_an_unknown_role_is_treated_as_no_role_filter(stakeholder_db):
    """A value outside the fixed select options must not widen or break the query."""
    service = CompanyService(stakeholder_db)
    assert len(service.list_companies(role="bogus", limit=50)) == 4


def test_role_filter_does_not_leak_other_roles(stakeholder_db):
    """A role filter must never match a stakeholder who only holds another role."""
    service = CompanyService(stakeholder_db)
    contractors = {c.name for c in service.list_companies(role="contractor", limit=50)}
    assert "ACME HEALTH LLC" not in contractors
    assert "TARRANT COUNTY" not in contractors


def test_companies_route_with_filters_does_not_return_500(stakeholder_client):
    """End-to-end: the filtered directory page renders for every filter combination."""
    for path in (
        "/companies",
        "/companies?role=contractor",
        "/companies?role=owner",
        "/companies?role=architect",
        "/companies?q=acme",
        "/companies?city=Fort%20Worth",
        "/companies?role=owner&city=Fort%20Worth",
        "/companies?role=owner&q=acme&city=Dallas",
        "/companies?role=bogus",
    ):
        response = stakeholder_client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_companies_route_shows_the_filtered_company(stakeholder_client):
    body = stakeholder_client.get("/companies?q=acme").get_data(as_text=True)
    assert "ACME HEALTH LLC" in body
    assert "TARRANT COUNTY" not in body


# --- WP3 M5: the companies page states what the data holds -------------------------

def test_companies_page_does_not_claim_unpublished_stakeholder_roles(stakeholder_client):
    """The directory never found GCs/architects for live data; the copy must not promise them."""
    body = stakeholder_client.get("/companies").get_data(as_text=True)
    assert "project owners today" in body
