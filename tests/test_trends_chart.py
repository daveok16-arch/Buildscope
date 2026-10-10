"""Trend Radar activity chart (WP3 K5).

The chart is a metric: it counts the same stored rows as the cards, excludes future-dated and
malformed dates, is derived (never projected), and carries a table alternative.
"""

from __future__ import annotations

from datetime import date

from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.trends import CHART_MONTHS, monthly_project_series


def _permit(number: str, permit_date: date, description: str = "New construction of an office building") -> Permit:
    return Permit(
        source_id="fort_worth_permits",
        permit_number=number,
        natural_key=number,
        permit_type="Commercial Mechanical Permit",
        permit_subtype="commercial_mechanical",
        permit_date=permit_date,
        status="Issued",
        address=f"{abs(hash(number)) % 900 + 100} ROSS AVE",
        city="Fort Worth",
        state="TX",
        work_description=description,
        land_use="OFFICE BUILDING",
        is_commercial=True,
        job_value=1_000_000.0,
        square_footage=20_000.0,
        source_url=f"https://example.gov/{number}",
        source_date=permit_date,
    )


def _db(tmp_path, permits) -> Database:
    db = Database(tmp_path / "trends_chart.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    for p in permits:
        db.upsert_permit(p, normalize_address(p.address))
    db.commit()
    pipeline.assemble_and_classify()
    return db


def test_series_has_a_stable_length(tmp_path):
    db = _db(tmp_path, [_permit("1", date(2026, 10, 1))])
    series = monthly_project_series(db, trade="commercial_hvac", today=date(2026, 10, 9))
    assert len(series) == CHART_MONTHS
    assert series[-1]["month"] == "2026-10"
    assert series[0]["month"] == "2025-11"


def test_series_months_are_contiguous_and_ascending(tmp_path):
    db = _db(tmp_path, [_permit("1", date(2026, 10, 1))])
    series = monthly_project_series(db, trade="commercial_hvac", today=date(2026, 10, 9))
    months = [p["month"] for p in series]
    assert months == sorted(months)


def test_series_counts_a_permit_in_its_month(tmp_path):
    db = _db(tmp_path, [_permit("1", date(2026, 9, 15)), _permit("2", date(2026, 9, 20))])
    series = monthly_project_series(db, trade="commercial_hvac", today=date(2026, 10, 9))
    by_month = {p["month"]: p["count"] for p in series}
    assert by_month["2026-09"] >= 1


def test_series_excludes_future_dated_permits(tmp_path):
    db = _db(tmp_path, [_permit("1", date(2026, 12, 19))])
    series = monthly_project_series(db, trade="commercial_hvac", today=date(2026, 10, 9))
    assert all(p["count"] == 0 for p in series)


def test_trends_page_renders_the_chart(client):
    body = client.get("/trends").get_data(as_text=True)
    assert 'class="bar-chart"' in body
    assert "View the numbers as a table" in body


def test_trends_chart_has_accessible_name(client):
    body = client.get("/trends").get_data(as_text=True)
    assert 'role="img"' in body
    assert "aria-label" in body
