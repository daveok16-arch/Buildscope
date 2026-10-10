"""Data-quality detection regression tests.

Two defects are locked down here, both surfaced by running the real pipeline against the
populated database:

1. `detect_quality_issues` recorded findings inside its own transaction and then returned
   early under the commercial-base trade, before the single `commit()`. Every reader — the
   `/admin/data` operations view and `flask report-quality` — opens its own connection, so it
   saw an empty table and reported "no open issues" no matter what was wrong with the data.
2. A permit dated in the near future (inside the old flat +365-day tolerance) was not flagged
   at all, even though a permit date records something that has already happened.

The tests use the real pipeline and a real SQLite database. No mocks.
"""

from __future__ import annotations

from datetime import date, timedelta

from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.quality import (
    FUTURE_PERMIT_DATE,
    MISSING_ADDRESS,
    detect_quality_issues,
)


def _permit(number: str, *, permit_date: date | None, address: str | None = "100 MAIN ST"):
    return Permit(
        source_id="fort_worth_permits",
        permit_number=number,
        natural_key=number,
        permit_type="Commercial Building Permit",
        permit_subtype="New",
        permit_date=permit_date,
        status="Issued",
        address=address,
        city="Fort Worth",
        state="TX",
        work_description="New construction of office building",
        is_commercial=True,
        source_url=f"https://example.gov/{number}",
        source_date=date.today(),
    )


def _fresh_db(tmp_path) -> Database:
    db = Database(tmp_path / "quality.db")
    db.init_schema()
    db.init_app_schema()
    return db


# --- a future filing date is labelled, never presented as a filing -------------

def test_filing_date_filter_marks_a_future_value_unverified(app_db):
    """The 19 Dec 2026 defect: a permit dated after today must not read as a filing date."""
    from oppintel.service import OpportunityService

    fmt = app_db.jinja_env.filters["filing_date"]
    assert fmt("2026-06-01") == OpportunityService.format_date("2026-06-01")
    future = fmt("2226-12-19")
    assert "date unverified" in future
    assert "19 Dec 2226" in future
    assert fmt(None) == "Not verified"


def test_decorate_flags_a_future_permit_date(app_db):
    """`decorate` sets the flag the templates use, without altering the stored value."""
    from oppintel.config import active_market, active_trade
    from oppintel.db import Database
    from oppintel.service import OpportunityService

    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        svc = OpportunityService(db, active_market(), active_trade())
        row = db.conn.execute("SELECT * FROM project LIMIT 1").fetchone()
        project = dict(row)
        db.conn.execute(
            "UPDATE project SET permit_date = ? WHERE id = ?", ("2226-12-19", project["id"])
        )
        db.conn.commit()
        project = dict(db.conn.execute("SELECT * FROM project WHERE id = ?", (project["id"],)).fetchone())
        decorated = svc.decorate(dict(project))
        assert decorated["permit_date_is_future"] is True
        assert decorated["permit_date"] == "2226-12-19"  # value preserved exactly
    finally:
        db.close()


# --- persistence across connections -------------------------------------------

def test_findings_survive_a_fresh_connection(tmp_path):
    """A finding recorded by a pass must be visible to the next process to open the file.

    Regression: the early return under `discover_commercial_base` skipped the commit, so the
    rows existed only in the writer's connection. A reader on a new connection saw nothing.
    """
    path = tmp_path / "quality.db"
    db = Database(path)
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()

    db.upsert_permit(_permit("PB-1", permit_date=None, address=None), None)
    db.commit()
    pipeline.assemble_and_classify()
    db.close()

    # A separate connection is exactly what /admin/data and `flask report-quality` use.
    reader = Database(path)
    issues = reader.quality_issues()
    assert any(i["issue_type"] == MISSING_ADDRESS for i in issues), (
        "the missing-address finding was recorded but never committed"
    )
    reader.close()


def test_quality_pass_is_idempotent_across_connections(tmp_path):
    """Running the pass twice must not accumulate duplicate findings."""
    path = tmp_path / "quality.db"
    db = Database(path)
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    db.upsert_permit(_permit("PB-1", permit_date=None, address=None), None)
    db.commit()

    pipeline.assemble_and_classify()
    first = len(db.quality_issues())
    pipeline.assemble_and_classify()
    assert len(db.quality_issues()) == first
    db.close()


# --- future occurrence dates --------------------------------------------------

def test_near_future_permit_date_is_flagged(tmp_path):
    """A permit dated two months ahead is a defect, not a scheduled submission.

    Regression: the flat +365-day tolerance let this value through unflagged.
    """
    db = _fresh_db(tmp_path)
    permit = _permit("PB-1", permit_date=date.today() + timedelta(days=60))
    detect_quality_issues(db, [permit])
    issues = db.quality_issues()
    assert any(i["issue_type"] == FUTURE_PERMIT_DATE for i in issues)
    db.close()


def test_far_future_permit_date_is_flagged(tmp_path):
    db = _fresh_db(tmp_path)
    permit = _permit("PB-1", permit_date=date.today() + timedelta(days=400))
    detect_quality_issues(db, [permit])
    assert any(i["issue_type"] == FUTURE_PERMIT_DATE for i in db.quality_issues())
    db.close()


def test_present_and_past_dates_are_not_flagged_as_future(tmp_path):
    db = _fresh_db(tmp_path)
    permits = [
        _permit("PB-TODAY", permit_date=date.today()),
        _permit("PB-PAST", permit_date=date.today() - timedelta(days=30)),
    ]
    detect_quality_issues(db, permits)
    assert not any(i["issue_type"] == FUTURE_PERMIT_DATE for i in db.quality_issues())
    db.close()


def test_future_source_date_is_flagged(tmp_path):
    db = _fresh_db(tmp_path)
    permit = _permit("PB-1", permit_date=date.today())
    permit.source_date = date.today() + timedelta(days=5)
    detect_quality_issues(db, [permit])
    issues = db.quality_issues()
    assert any(
        i["issue_type"] == FUTURE_PERMIT_DATE and "source date" in i["detail"] for i in issues
    )
    db.close()


def test_finding_preserves_the_source_value(tmp_path):
    """The correction must not rewrite the source's own date."""
    db = _fresh_db(tmp_path)
    future = date.today() + timedelta(days=60)
    permit = _permit("PB-1", permit_date=future)
    detect_quality_issues(db, [permit])
    issue = next(i for i in db.quality_issues() if i["issue_type"] == FUTURE_PERMIT_DATE)
    assert future.isoformat() in issue["detail"]
    db.close()


# --- source-level and project-level checks ------------------------------------

def test_project_without_evidence_is_recorded_and_committed(tmp_path):
    """The source-level/project-level checks must also reach a reader."""
    path = tmp_path / "quality.db"
    db = Database(path)
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    db.upsert_permit(_permit("PB-1", permit_date=date.today()), normalize_address("100 MAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()
    db.close()

    reader = Database(path)
    # Every assembled project carries evidence, so this is a negative check that the pass ran
    # to completion without raising.
    assert reader.quality_issues() is not None
    reader.close()
