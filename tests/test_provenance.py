"""Tests for the critical data rule: never invent, infer, or hallucinate.

These are the most important tests in the suite. If any of them fails, the platform is
claiming facts it cannot support, which destroys the product's only real asset.

Recovered in Phase 1B from the decompiled bytecode of the pre-deletion test suite; the test
names, literals and assertions match the recorded run in `.pytest_cache`.
"""

from __future__ import annotations

import pytest

from oppintel.constants import NOT_VERIFIED
from oppintel.models import Project
from oppintel.provenance import (
    ProvenanceError,
    assert_field,
    render,
    record_permit_evidence,
    unverified_fields,
)
from conftest import make_permit


def test_missing_field_renders_as_not_verified():
    project = Project(project_key="x")
    assert project.display("architect") == NOT_VERIFIED
    assert project.display("developer") == NOT_VERIFIED
    assert project.display("owner") == NOT_VERIFIED


def test_blank_string_renders_as_not_verified():
    project = Project(project_key="x", architect="   ")
    assert project.display("architect") == NOT_VERIFIED


def test_render_handles_sentinels_and_none():
    assert render(None) == NOT_VERIFIED
    assert render("") == NOT_VERIFIED
    assert render("  ") == NOT_VERIFIED
    assert render("Smith & Co") == "Smith & Co"


def test_known_sentinel_strings_are_not_stored():
    """Source feeds emit literal 'NULL'. It must never become a published fact."""
    project = Project(project_key="x")
    for sentinel in ("NULL", "null", "N/A", "NA", "None", "-", ""):
        assert (
            assert_field(
                project,
                "owner",
                sentinel,
                source_id="s",
                source_name="Source",
                source_url="https://example.gov",
            )
            is False
        )
        assert project.owner is None
        assert project.display("owner") == NOT_VERIFIED


def test_cannot_assert_without_a_source():
    """A fact with no identified source is a bug, not a data gap."""
    project = Project(project_key="x")
    with pytest.raises(ProvenanceError):
        assert_field(project, "owner", "ACME LLC", source_id="", source_name="", source_url=None)


def test_asserting_a_field_records_evidence():
    project = Project(project_key="x")
    assert_field(
        project,
        "owner",
        "ACME LLC",
        source_id="fort_worth_permits",
        source_name="City of Fort Worth Development Permits",
        source_url="https://example.gov/1",
        excerpt="Owner_Full_Name = ACME LLC",
    )
    assert project.owner == "ACME LLC"
    owner_evidence = [e for e in project.evidence if e.field_name == "owner"]
    assert len(owner_evidence) == 1
    assert owner_evidence[0].source_url == "https://example.gov/1"
    assert owner_evidence[0].excerpt == "Owner_Full_Name = ACME LLC"


def test_every_populated_field_has_evidence():
    """After assembly, no factual field may be set without a matching evidence row."""
    project = Project(project_key="x", city="Fort Worth")
    permit = make_permit()
    record_permit_evidence(project, permit, "City of Fort Worth Development Permits")

    fields = (
        "address",
        "city",
        "state",
        "permit_number",
        "permit_date",
        "project_status",
        "estimated_project_value",
        "square_footage",
        "project_name",
    )
    populated = {name for name in fields if getattr(project, name, None) is not None}
    with_evidence = {e.field_name for e in project.evidence}
    missing = populated - with_evidence
    assert not missing, f"fields populated with no evidence: {sorted(missing)}"


def test_first_writer_wins_so_primary_permit_value_survives():
    """A later trade permit must not overwrite the building permit's declared value."""
    project = Project(project_key="x")
    building = make_permit(permit_number="PB1", job_value=5_000_000.0)
    mechanical = make_permit(
        permit_number="PM1",
        permit_type="Mechanical",
        job_value=1_500.0,
        natural_key="PM1::Mechanical",
    )
    record_permit_evidence(project, building, "City of Fort Worth Development Permits")
    record_permit_evidence(project, mechanical, "City of Fort Worth Development Permits")
    assert project.estimated_project_value == 5_000_000.0


def test_short_work_description_is_not_a_project_name():
    """'RENOVATION' is not a project name and must not be asserted as one."""
    project = Project(project_key="x")
    permit = make_permit(work_description="RENOVATION")
    record_permit_evidence(project, permit, "City of Fort Worth Development Permits")
    assert project.project_name is None
    assert project.display("project_name") == NOT_VERIFIED


def test_all_contributing_permit_numbers_are_retained():
    """A project legitimately has several permits; each one is traceable."""
    project = Project(project_key="x")
    building = make_permit(permit_number="PB1")
    mechanical = make_permit(
        permit_number="PM1", permit_type="Mechanical", natural_key="PM1::Mechanical"
    )
    record_permit_evidence(project, building, "City of Fort Worth Development Permits")
    record_permit_evidence(project, mechanical, "City of Fort Worth Development Permits")
    numbers = {e.value for e in project.evidence if e.field_name == "permit_number"}
    assert numbers == {"PB1", "PM1"}


def test_unverified_fields_lists_the_gaps():
    project = Project(project_key="x")
    missing = unverified_fields(project)
    assert "architect" in missing
    assert "developer" in missing
    assert "owner" in missing
    assert len(set(missing)) == len(missing)
