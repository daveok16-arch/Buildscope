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


# --- role counts drive the filter control (WP3 M5) -----------------------------

def test_role_counts_reports_only_present_roles(stakeholder_db):
    counts = CompanyService(stakeholder_db).role_counts()
    # Two distinct owners (ACME, Tarrant) and two distinct contractors (Builder Co, Rival).
    assert counts.get("owner") == 2
    assert counts.get("contractor") == 2
    # No architect or developer is published by this fixture, so neither is offered.
    assert "architect" not in counts
    assert "developer" not in counts


def test_role_counts_omits_a_role_that_would_filter_to_nothing(stakeholder_client):
    """The role control must not offer an option the data cannot honour."""
    body = stakeholder_client.get("/companies").get_data(as_text=True)
    assert "General Contractor (2)" in body
    assert "Property Owner / Developer (2)" in body
    assert "Architect / Designer" not in body


def test_role_counts_narrow_with_a_city_filter(stakeholder_db):
    counts = CompanyService(stakeholder_db).role_counts(city="Dallas")
    # In Dallas only ACME (owner) and Rival Builders (contractor) are present.
    assert counts.get("owner") == 1
    assert counts.get("contractor") == 1


# --- evidence-backed aggregation: one company, one row, counted once ---------------
#
# Two defects were proven here. First, a project reached the union through both the
# project_party branch and a direct project-column branch, so its declared value and its
# mechanical flag were summed twice (ACME with one $5M mechanical project reported $10M and
# two mechanical projects). Second, the aggregation grouped by the raw spelling, so the two
# spellings `identity.py` merges into one company appeared as two directory entries with one
# project each, contradicting the entity graph the rest of the product reads.

_AGGREGATE_PERMITS = [
    dict(permit_number="G1", natural_key="G1", address="100 MAIN ST", city="Fort Worth",
         owner="ACME HEALTH LLC", contractor="BUILDER CO", job_value=2_000_000.0),
    dict(permit_number="G2", natural_key="G2", address="200 OAK AVE", city="Dallas",
         owner="ACME HEALTH LLC", contractor="RIVAL BUILDERS", job_value=3_000_000.0),
    dict(permit_number="G3", natural_key="G3", address="300 ELM ST", city="Fort Worth",
         owner="TARRANT COUNTY", contractor="BUILDER CO", job_value=1_000_000.0),
]


def _assemble(db: Database, permits: list[dict]) -> None:
    pipeline = Pipeline(db)
    pipeline._register_sources()
    for overrides in permits:
        permit = _permit(
            permit_type="Commercial Mechanical Permit",
            permit_subtype="commercial_mechanical",
            work_description="Mechanical remodel of spec suite",
            land_use="",
            square_footage=20_000.0,
            **overrides,
        )
        db.upsert_permit(permit, normalize_address(permit.address))
    db.commit()
    pipeline.assemble_and_classify()


@pytest.fixture
def aggregate_db(tmp_path):
    db = Database(tmp_path / "aggregate.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, _AGGREGATE_PERMITS)
    yield db
    db.close()


def test_a_projects_value_and_evidence_are_counted_once(aggregate_db):
    """A project that appears in more than one union branch must not be summed twice."""
    by_name = {c.name: c for c in CompanyService(aggregate_db).list_companies(limit=50)}
    # ACME owns two $2M/$3M mechanical projects; the double-count reported $10M and 4.
    assert by_name["ACME HEALTH LLC"].project_count == 2
    assert by_name["ACME HEALTH LLC"].mechanical_project_count == 2
    assert by_name["ACME HEALTH LLC"].total_declared_value == 5_000_000.0
    assert by_name["BUILDER CO"].project_count == 2
    assert by_name["BUILDER CO"].total_declared_value == 3_000_000.0
    assert by_name["RIVAL BUILDERS"].project_count == 1
    assert by_name["RIVAL BUILDERS"].mechanical_project_count == 1


def test_two_spellings_of_one_company_are_one_directory_entry(tmp_path):
    """The directory must merge names the entity graph already merges, or it shows two firms."""
    db = Database(tmp_path / "spellings.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="S1", natural_key="S1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor=None, job_value=2_000_000.0),
        dict(permit_number="S2", natural_key="S2", address="20 ELM ST", city="Fort Worth",
             owner="ACME HEALTH, L.L.C.", contractor=None, job_value=3_000_000.0),
    ])
    companies = CompanyService(db).list_companies(limit=50)
    acme = [c for c in companies if "acme" in c.name.lower()]
    assert len(acme) == 1, f"the two spellings must be one company, got {[c.name for c in acme]}"
    assert acme[0].project_count == 2
    assert acme[0].total_declared_value == 5_000_000.0
    db.close()


def test_a_project_without_a_contractor_yields_no_contractor_claim(tmp_path):
    """No source publishes a GC for this project, so none may be shown or counted.

    The evidence rule is that absence stays absent. The directory must not promote an owner,
    or any other name, into the contractor role to fill the gap.
    """
    db = Database(tmp_path / "no_gc.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="X1", natural_key="X1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor=None, job_value=1_000_000.0),
    ])
    service = CompanyService(db)
    assert service.list_companies(role="contractor", limit=50) == []
    assert "contractor" not in service.role_counts()
    assert [c.name for c in service.list_companies(limit=50)] == ["ACME HEALTH LLC"]
    db.close()


def test_a_name_query_counts_the_whole_merged_company(tmp_path):
    """Searching one spelling must report the merged company's full project total.

    The name query selects companies, not raw rows. If it were applied to the raw spellings,
    typing the spelling that happened to have fewer observations would report a smaller total
    than the same company shows without the query.
    """
    db = Database(tmp_path / "query.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="N1", natural_key="N1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor=None, job_value=1_000_000.0),
        dict(permit_number="N2", natural_key="N2", address="20 ELM ST", city="Fort Worth",
             owner="ACME HEALTH, L.L.C.", contractor=None, job_value=1_000_000.0),
    ])
    service = CompanyService(db)
    for query in ("ACME HEALTH LLC", "ACME HEALTH, L.L.C.", "acme"):
        found = service.list_companies(q=query, limit=50)
        assert len(found) == 1, (query, [c.name for c in found])
        assert found[0].project_count == 2, (query, found[0].project_count)
    db.close()


def test_role_counts_agree_with_the_filtered_list(tmp_path):
    """A role count and the list it filters to must count the same canonical companies."""
    db = Database(tmp_path / "counts.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="R1", natural_key="R1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor=None, job_value=1_000_000.0),
        dict(permit_number="R2", natural_key="R2", address="20 ELM ST", city="Fort Worth",
             owner="ACME HEALTH, L.L.C.", contractor=None, job_value=1_000_000.0),
    ])
    service = CompanyService(db)
    owners = service.list_companies(role="owner", limit=50)
    assert len(owners) == 1
    assert service.role_counts().get("owner") == len(owners)
    db.close()


def test_company_profile_reaches_the_same_row_for_every_spelling(tmp_path):
    """A slug, the display name, and any observed variant must resolve to one dossier."""
    db = Database(tmp_path / "profile.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="P1", natural_key="P1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor=None, job_value=1_000_000.0),
        dict(permit_number="P2", natural_key="P2", address="20 ELM ST", city="Fort Worth",
             owner="ACME HEALTH, L.L.C.", contractor=None, job_value=1_000_000.0),
    ])
    service = CompanyService(db)
    listed = next(c for c in service.list_companies(limit=50) if "acme" in c.name.lower())
    profile = service.get_company_profile(listed.slug)
    assert profile is not None
    assert profile.name == listed.name
    assert profile.project_count == 2
    # The other observed spelling resolves to the same dossier.
    variant = service.get_company_profile("ACME HEALTH LLC")
    assert variant is not None and variant.slug == listed.slug
    db.close()


def test_profile_reports_every_observed_role_and_no_unobserved_one(tmp_path):
    """The dossier lists the roles a record states, and never one no source published.

    A company can be an owner on one filing and the contractor on another. The profile must
    show both, because both are stored facts; and it must not show a role the data does not
    contain (here, architect).
    """
    db = Database(tmp_path / "roles.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="Z1", natural_key="Z1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor="BUILDER CO", job_value=1_000_000.0),
        dict(permit_number="Z2", natural_key="Z2", address="20 ELM ST", city="Fort Worth",
             owner="TARRANT COUNTY", contractor="ACME HEALTH LLC", job_value=1_000_000.0),
    ])
    service = CompanyService(db)
    acme = service.get_company_profile("ACME HEALTH LLC")
    assert set(acme.roles_observed) == {"Owner", "General Contractor"}
    builder = service.get_company_profile("BUILDER CO")
    assert builder.roles_observed == ["General Contractor"]
    assert "Architect" not in builder.roles_observed
    db.close()


def test_company_detail_shows_an_additional_observed_role(tmp_path):
    """The dossier states the extra role a source published, and stays quiet when there is none.

    ACME is an owner on one filing and the contractor on another, so its page must say it was
    also observed as a General Contractor. BUILDER CO is only ever a contractor, so its page
    must not invent a second role.
    """
    db = Database(tmp_path / "multirole.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="Y1", natural_key="Y1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor="BUILDER CO", job_value=1_000_000.0),
        dict(permit_number="Y2", natural_key="Y2", address="20 ELM ST", city="Fort Worth",
             owner="TARRANT COUNTY", contractor="ACME HEALTH LLC", job_value=1_000_000.0),
    ])
    ensure_slugs(db)
    rebuild_index(db)
    db.close()

    cfg = AppConfig(database_path=tmp_path / "multirole.db", secret_key="t", debug=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    client = app.test_client()
    acme = client.get("/companies/acme-health-llc").get_data(as_text=True)
    assert "Also observed as" in acme
    assert "General Contractor" in acme
    builder = client.get("/companies/builder-co").get_data(as_text=True)
    assert "Also observed as" not in builder


def test_recurring_partners_are_merged_and_exclude_the_company_itself(tmp_path):
    """Partners are grouped canonically and the company's own variant is not listed as one."""
    db = Database(tmp_path / "partners.db")
    db.init_schema()
    db.init_app_schema()
    _assemble(db, [
        dict(permit_number="Q1", natural_key="Q1", address="10 ROSS AVE", city="Fort Worth",
             owner="ACME HEALTH LLC", contractor="BUILDER CO", job_value=1_000_000.0),
        dict(permit_number="Q2", natural_key="Q2", address="20 ELM ST", city="Fort Worth",
             owner="ACME HEALTH, L.L.C.", contractor="BUILDER CO", job_value=1_000_000.0),
    ])
    service = CompanyService(db)
    listed = next(c for c in service.list_companies(limit=50) if "acme" in c.name.lower())
    profile = service.get_company_profile(listed.slug)
    partner_names = [p.name for p in profile.recurring_partners]
    assert "BUILDER CO" in partner_names
    assert not any("acme" in n.lower() for n in partner_names), partner_names
    builder = next(p for p in profile.recurring_partners if p.name == "BUILDER CO")
    assert builder.shared_projects == 2
    db.close()
