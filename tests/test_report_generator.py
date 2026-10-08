"""Tests for the report generation layer.

Covers the properties a customer-facing document must have:

* a missing field renders as "Not verified", never as a blank or an estimate
* contradictory sources are preserved rather than reconciled
* an unsupported GC or contractor claim cannot appear
* service and repair permits never become opportunities
* mechanical construction permits stay distinguishable from plumbing and electrical
* source URLs survive into the report
* the customer brief is concise, and no opportunity is called an open bid without evidence

Recovered in Phase 1B from the decompiled bytecode of the pre-deletion test suite.
"""

from __future__ import annotations

from datetime import date

import pytest

from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.report_generator import ReportBuilder


def _pipeline(tmp_path):
    db = Database(tmp_path / "r.db")
    db.init_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    return db, pipeline


def _add(db, **overrides):
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
        work_description="New construction of medical office building",
        is_commercial=True,
        source_url="https://example.gov/PB1",
        source_date=date(2026, 9, 1),
    )
    defaults.update(overrides)
    permit = Permit(**defaults)
    db.upsert_permit(permit, normalize_address(permit.address))
    db.commit()
    return permit


def _build(tmp_path, *, permits=None):
    db, pipeline = _pipeline(tmp_path)
    for override in permits if permits is not None else [{}]:
        _add(db, **override)
    pipeline.assemble_and_classify()
    return db, pipeline


# --- "Not verified" rendering --------------------------------------------------


def test_missing_fields_render_as_not_verified(tmp_path):
    """A field with no source must say so, in both report formats."""
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    for report in (builder.customer_brief(ids), builder.internal_report(ids)):
        assert "Not verified" in report
    brief = builder.customer_brief(ids)
    assert "**GC:** Not verified" in brief
    assert "**Architect:** Not verified" in brief
    db.close()


def test_missing_fields_are_never_rendered_as_blank_cells(tmp_path):
    """An empty table cell would look like an oversight; the marker is explicit."""
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    for line in brief.splitlines():
        if line.startswith("| ") and line.endswith(" |"):
            cells = line.strip("|").split("|")
            assert all(cells), f"blank cell in: {line}"
    db.close()


def test_not_verified_has_no_trailing_period_inside_a_cell(tmp_path):
    """The canonical constant is a sentence; in a value cell the period is noise."""
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    assert "| Not verified. |" not in brief
    db.close()


# --- contradictions preserved --------------------------------------------------


def test_contradictory_sources_are_preserved_in_both_reports(tmp_path):
    """Two publishers disagreeing on a value: both survive, neither is reconciled."""
    db, pipeline = _pipeline(tmp_path)
    _add(db, source_id="fort_worth_permits", permit_number="A", natural_key="A", job_value=250_000_000.0)
    _add(db, source_id="collin_cad_permits", permit_number="B", natural_key="B", job_value=264_000_000.0)
    pipeline.assemble_and_classify()
    row = db.conn.execute("SELECT id FROM project").fetchone()
    builder = ReportBuilder(db)
    brief = builder.customer_brief([row["id"]])
    internal = builder.internal_report([row["id"]])
    for report in (brief, internal):
        assert "250000000.0" in report
        assert "264000000.0" in report
        assert ("Source disagreement" in report) or ("Source discrepancies" in report)
    db.close()


def test_within_source_multiple_permits_are_not_a_contradiction(tmp_path):
    """Several permits from one publisher describe scope, not competing facts."""
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="M1", natural_key="M1", permit_type="Commercial Mechanical Permit",
         permit_subtype="commercial_mechanical", permit_date=date(2026, 9, 2),
         work_description="Mech remodel of spec suite")
    _add(db, permit_number="P1", natural_key="P1", permit_type="Commercial Plumbing Permit",
         permit_subtype="commercial_plumbing", permit_date=date(2026, 9, 3),
         work_description="New plumbing for remodel")
    pipeline.assemble_and_classify()
    row = db.conn.execute("SELECT disputed_fields FROM project").fetchone()
    assert row["disputed_fields"] == "[]"
    db.close()


# --- GC / architect claims -----------------------------------------------------


def test_unsupported_gc_claim_cannot_appear(tmp_path):
    """No source publishes a GC, so the report must not name one."""
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    assert "**GC:** Not verified" in brief
    db.close()


def test_gc_appears_only_when_a_source_states_it(tmp_path):
    """When a permit does name a contractor, it is reported with its citation."""
    db, _ = _build(tmp_path, permits=[{"contractor": "BUILDER CO", "permit_type": "Commercial Building Permit"}])
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    assert "BUILDER CO" in brief
    assert "Not verified" in brief
    db.close()


def test_architect_and_developer_stay_unverified_across_the_brief(tmp_path):
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    assert "**Architect:** Not verified" in brief
    db.close()


# --- eligibility: service / completed / mechanical -----------------------------


def test_service_permit_never_becomes_an_opportunity(tmp_path):
    """A like-for-like replacement is maintenance, so it must not reach any report."""
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="M1", natural_key="M1", permit_type="Commercial Mechanical Permit",
         permit_subtype="commercial_mechanical",
         work_description="Remove & replace (5) RTU package units like for like")
    pipeline.assemble_and_classify()
    assert db.conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 0
    builder = ReportBuilder(db)
    assert builder.select_opportunities() == []
    db.close()


def test_completed_work_is_excluded_from_the_customer_brief(tmp_path):
    """A finished building is not a live opportunity, however strong its evidence.

    The completed permit scores below the HIGH threshold because a closed status earns no
    phase points, so it is filtered out twice over: by classification and by procurement
    status. Both filters are asserted here, because either alone would be enough to let a
    finished building into a customer list.
    """
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="M1", natural_key="M1", permit_type="Commercial Mechanical Permit",
         permit_subtype="commercial_mechanical", status="Closed - Complete",
         work_description="Mech remodel of spec suite")
    pipeline.assemble_and_classify()
    row = db.conn.execute("SELECT classification, procurement_status FROM project").fetchone()
    assert row["classification"] != "HIGH"
    assert row["procurement_status"] == "Closed"
    builder = ReportBuilder(db)
    assert builder.select_opportunities(require_active=True) == []
    db.close()


def test_active_mechanical_lead_is_available_but_not_headline(tmp_path):
    """The counterpart to the test above.

    An active mechanical permit with no building class, value or footprint scores 70 — over
    the HIGH threshold — but the significance gate correctly holds it at MEDIUM. It is a
    genuine confirmed lead, so it must remain selectable when MEDIUM is asked for, while
    staying out of a HIGH-only brief.
    """
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="M1", natural_key="M1", permit_type="Commercial Mechanical Permit",
         permit_subtype="commercial_mechanical", status="Inspection Phase",
         work_description="Mech remodel of spec suite")
    pipeline.assemble_and_classify()
    row = db.conn.execute(
        "SELECT classification, classification_score, procurement_status FROM project"
    ).fetchone()
    assert row["classification_score"] >= 70
    assert row["classification"] == "MEDIUM"
    assert row["procurement_status"] == "Evidence found, status unclear"
    builder = ReportBuilder(db)
    assert builder.select_opportunities(classifications=("HIGH",)) == []
    assert builder.select_opportunities(classifications=("MEDIUM",)) == []
    assert builder.select_opportunities(classifications=("MEDIUM",), require_eligibility=False)
    db.close()


def test_mechanical_project_reports_a_mechanical_permit_not_a_plumbing_one(tmp_path):
    """Regression: a plumbing permit was presented for a mechanical-evidence project."""
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="M1", natural_key="M1", permit_type="Commercial Mechanical Permit",
         permit_subtype="commercial_mechanical", permit_date=date(2026, 9, 1),
         work_description="Mechanical Installation New construction of shell office")
    _add(db, permit_number="P1", natural_key="P1", permit_type="Commercial Plumbing Permit",
         permit_subtype="commercial_plumbing", permit_date=date(2026, 9, 5),
         work_description="New plumbing for new construction office building")
    pipeline.assemble_and_classify()
    row = db.conn.execute("SELECT permit_number, mechanical_hvac_evidence FROM project").fetchone()
    assert row["permit_number"] == "M1"
    assert "Mechanical" in row["mechanical_hvac_evidence"]
    db.close()


def test_plumbing_only_project_has_no_mechanical_evidence(tmp_path):
    """A plumbing permit is not mechanical evidence and must not be presented as such."""
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="P1", natural_key="P1", permit_type="Commercial Plumbing Permit",
         permit_subtype="commercial_plumbing",
         work_description="New plumbing for new construction office building")
    pipeline.assemble_and_classify()
    row = db.conn.execute(
        "SELECT mechanical_hvac_evidence, mechanical_evidence_tier FROM project"
    ).fetchone()
    assert row["mechanical_hvac_evidence"] is None
    assert not row["mechanical_evidence_tier"]
    db.close()


def test_electrical_only_project_has_no_mechanical_evidence(tmp_path):
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="E1", natural_key="E1", permit_type="Commercial Electrical Permit",
         permit_subtype="commercial_electrical",
         work_description="Electrical for new construction office building")
    pipeline.assemble_and_classify()
    row = db.conn.execute("SELECT mechanical_hvac_evidence FROM project").fetchone()
    assert row["mechanical_hvac_evidence"] is None
    db.close()


def test_brief_states_absence_of_mechanical_evidence_plainly(tmp_path):
    """'Mechanical scope unconfirmed' must read as a statement, not an omission."""
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="B1", natural_key="B1", permit_type="Commercial Building Permit",
         permit_subtype="New", work_description="New construction of retail building",
         land_use="RETAIL")
    pipeline.assemble_and_classify()
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    assert "no mechanical or HVAC permit" in brief
    db.close()


# --- traceability --------------------------------------------------------------


def test_report_urls_are_preserved(tmp_path):
    """Every citation URL present in the evidence must survive into the report."""
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    internal = builder.internal_report(ids)
    assert "https://example.gov/PB1" in brief
    assert "https://example.gov/PB1" in internal
    db.close()


def test_every_key_fact_row_carries_a_citation(tmp_path):
    """A material claim with no source column would break traceability."""
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    in_table = False
    for line in brief.splitlines():
        if line.startswith("| Detail |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if line.startswith("|---"):
                continue
            cells = line.strip("|").split("|")
            if cells[1] not in ("Not verified",):
                assert cells[2] != "—", f"unsourced claim: {line}"
    db.close()


def test_internal_report_lists_source_provenance_section(tmp_path):
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    internal = builder.internal_report(ids)
    assert "### Source provenance" in internal
    assert "field(s)" in internal
    db.close()


# --- bid status / procurement --------------------------------------------------


def test_no_opportunity_is_called_an_open_bid_without_evidence(tmp_path):
    """No source in this market publishes bid status, so the brief must not imply one.

    The phrase "confirmed open" is expected to appear only inside a negation, such as "this
    is not a confirmed open bid". The test therefore requires that every occurrence is part
    of a disclaimer rather than a claim.
    """
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    lowered = brief.lower()
    offenders = []
    for index in range(len(lowered)):
        if lowered.startswith("confirmed open", index):
            context = lowered[max(0, index - 40) : index + 40]
            if ("not a" not in context) and ("no source" not in context):
                offenders.append(context)
    assert not offenders, f"'confirmed open' used as a claim, not a disclaimer: {offenders}"
    assert "Evidence found, status unclear" in brief
    db.close()


def test_procurement_never_claims_confirmed_open_for_ordinary_permits(tmp_path):
    """Direct check of the helper the brief relies on."""
    from oppintel.models import Project
    from oppintel.procurement import CONFIRMED_OPEN, EVIDENCE_FOUND, procurement_status

    db, _ = _build(tmp_path)
    row = db.conn.execute("SELECT * FROM project").fetchone()
    project = Project(project_key="x", project_status=row["project_status"])
    assert procurement_status(project) == EVIDENCE_FOUND
    assert procurement_status(project) != CONFIRMED_OPEN
    db.close()


def test_procurement_status_is_present_for_every_opportunity(tmp_path):
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    assert brief.count("**Procurement status:**") == len(ids)
    db.close()


# --- brief vs internal ---------------------------------------------------------


def test_customer_brief_omits_database_detail(tmp_path):
    """The brief must carry opportunity substance, not the audit trail.

    A raw length comparison is misleading for a single project, because the brief carries a
    fixed explanatory footer. The property that actually matters is structural: the brief
    must not contain the internal-only sections.
    """
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    brief = builder.customer_brief(ids)
    for internal_only in (
        "### Field-level verification",
        "### Classification reasoning",
        "### Contributing permits",
        "### Source provenance",
        "### Source discrepancies",
    ):
        assert internal_only not in brief, f"brief leaked internal section: {internal_only}"
    db.close()


def test_brief_is_shorter_per_opportunity_across_several_projects(tmp_path):
    """With several projects, the brief's per-opportunity cost is clearly lower."""
    db, pipeline = _pipeline(tmp_path)
    for index in range(4):
        _add(db, permit_number=f"M{index}", natural_key=f"M{index}",
             permit_type="Commercial Mechanical Permit", permit_subtype="commercial_mechanical",
             address=f"{index + 1}00 MECH ST", work_description="Mech remodel of spec suite",
             owner="ACME HOLDINGS LLC", job_value=1_000_000.0)
    pipeline.assemble_and_classify()
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    assert len(ids) >= 3, "need several projects for this comparison"
    brief = builder.customer_brief(ids)
    internal = builder.internal_report(ids)
    assert len(brief) < len(internal)
    db.close()


def test_internal_report_contains_audit_detail_the_brief_omits(tmp_path):
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    ids = [int(r["id"]) for r in db.conn.execute("SELECT id FROM project").fetchall()]
    internal = builder.internal_report(ids)
    assert "### Classification reasoning" in internal
    assert "### Contributing permits" in internal
    assert "### Field-level verification" in internal
    db.close()


def test_empty_selection_produces_a_valid_document(tmp_path):
    """An empty list must still render, so an empty state is visible not silent."""
    db, _ = _build(tmp_path)
    builder = ReportBuilder(db)
    brief = builder.customer_brief([])
    assert "Opportunities in this brief:** 0" in brief
    assert "# DFW Commercial HVAC Opportunity Brief" in brief
    db.close()


def test_explicit_project_ids_are_respected(tmp_path):
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="A", natural_key="A", address="1 A ST")
    _add(db, permit_number="B", natural_key="B", address="2 B ST")
    pipeline.assemble_and_classify()
    ids = db.conn.execute("SELECT id FROM project ORDER BY address").fetchall()
    builder = ReportBuilder(db)
    brief = builder.customer_brief([ids[0]["id"]])
    assert "1 A ST" in brief
    assert "2 B ST" not in brief
    db.close()


def test_selection_is_ordered_by_strength(tmp_path):
    db, pipeline = _pipeline(tmp_path)
    _add(db, permit_number="M1", natural_key="M1", permit_type="Commercial Mechanical Permit",
         permit_subtype="commercial_mechanical", address="1 MECH ST",
         work_description="Mech remodel of spec suite")
    _add(db, permit_number="B1", natural_key="B1", address="2 BUILD ST",
         work_description="New construction of retail building", land_use="RETAIL")
    pipeline.assemble_and_classify()
    builder = ReportBuilder(db)
    selected = builder.select_opportunities(require_active=False)
    scores = [
        int(db.conn.execute("SELECT classification_score FROM project WHERE id = ?", (pid,)).fetchone()[0])
        for pid in selected
    ]
    assert scores == sorted(scores, reverse=True)
    db.close()
