"""LinkedIn Content Intelligence tests.

Real SQLite databases built from synthetic permits — no mocks, and no external model in the
loop. The tests assert the properties that matter for honesty: a fact-shaped line appears only
when a source supports it, the validator catches an unsupported figure, and a closed project is
never proposed.
"""

from __future__ import annotations

from datetime import date

import pytest

from oppintel.db import Database
from oppintel.linkedin import (
    FORMAT_IDS,
    FactLine,
    build_data_quality,
    build_draft,
    build_market_pulse,
    build_pattern,
    build_spotlight,
    discover_candidates,
    validate_post,
)
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.search_index import rebuild_index


def _permit(number: str, **overrides) -> Permit:
    defaults = dict(
        source_id="fort_worth_permits",
        permit_number=number,
        natural_key=number,
        permit_type="Commercial Mechanical Permit",
        permit_subtype="commercial_mechanical",
        permit_date=date(2026, 9, 1),
        status="Issued",
        address=f"{abs(hash(number)) % 900 + 100} ROSS AVE",
        city="Fort Worth",
        state="TX",
        work_description="Mechanical remodel of a spec suite",
        land_use="OFFICE BUILDING",
        is_commercial=True,
        job_value=5_000_000.0,
        square_footage=40_000.0,
        owner="ACME HEALTH LLC",
        source_url=f"https://example.gov/{number}",
        source_date=date(2026, 9, 1),
    )
    defaults.update(overrides)
    return Permit(**defaults)


def _db(tmp_path, permits) -> Database:
    db = Database(tmp_path / "linkedin.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    for p in permits:
        db.upsert_permit(p, normalize_address(p.address))
    db.commit()
    pipeline.assemble_and_classify()
    rebuild_index(db)
    return db


# --- candidate discovery -------------------------------------------------------

def test_candidates_are_proposed_with_checkable_reasons(tmp_path):
    db = _db(tmp_path, [_permit("A"), _permit("B")])
    candidates = discover_candidates(db)
    assert candidates
    for c in candidates:
        assert c.reasons, "a proposal without a reason would be an unexplained judgement"
    db.close()


def test_closed_projects_are_not_proposed(tmp_path):
    db = _db(tmp_path, [
        _permit("OPEN"),
        _permit("CLOSED", status="Closed - Complete"),
    ])
    candidates = discover_candidates(db)
    names = {c.headline for c in candidates}
    assert all("CLOSED" not in n for n in names)
    db.close()


def test_candidate_ranking_is_deterministic(tmp_path):
    permits = [_permit(f"P{i}") for i in range(5)]
    db1 = _db(tmp_path, permits)
    first = [c.project_id for c in discover_candidates(db1)]
    second = [c.project_id for c in discover_candidates(db1)]
    assert first == second
    db1.close()


# --- draft construction --------------------------------------------------------

def test_spotlight_separates_verified_from_unknown(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    post = build_spotlight(db, pid)
    assert post.verified, "supported fields must produce verified lines"
    assert post.unverified, "absent fields must be named, not omitted silently"
    assert all("not stated" in line or "not yet supported" in line for line in post.unverified)
    db.close()


def test_spotlight_never_asserts_an_unsupported_value(tmp_path):
    """A value with no supporting evidence row must not become a verified claim."""
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    db.conn.execute("DELETE FROM evidence WHERE project_id = ?", (pid,))
    db.conn.commit()
    post = build_spotlight(db, pid)
    assert post.verified == []
    assert post.unverified
    db.close()


def test_every_draft_carries_the_permit_evidence_caveat(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    for post in (build_spotlight(db, pid), build_data_quality(db, pid)):
        assert "available for bid" in post.caveat
        assert post.caveat in post.render()
    db.close()


def test_market_pulse_reuses_defined_trend_metrics(tmp_path):
    db = _db(tmp_path, [_permit("A"), _permit("B")])
    post = build_market_pulse(db, window="30d")
    assert post.format == "market_pulse"
    assert any("Distinct" in line for line in post.verified)
    db.close()


def test_pattern_states_the_set_it_counts(tmp_path):
    db = _db(tmp_path, [_permit("A"), _permit("B"), _permit("C")])
    ids = [c.project_id for c in discover_candidates(db)][:3]
    post = build_pattern(db, project_ids=ids)
    assert any("Set:" in line for line in post.verified)
    assert any("not a market total" in line for line in post.interpretation)
    db.close()


def test_pattern_refuses_an_empty_set(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    with pytest.raises(ValueError):
        build_pattern(db, project_ids=[])
    db.close()


def test_unknown_format_is_an_error_not_a_fallback(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    with pytest.raises(ValueError):
        build_draft(db, format="not_a_format")
    db.close()


def test_all_declared_formats_have_builders(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    # Create a real detected change (not a first observation) so the change-alert format has
    # something factual to quote, rather than fabricating a change row.
    db.upsert_permit(_permit("A", job_value=9_000_000.0), normalize_address("100 ROSS AVE"))
    db.commit()
    import oppintel.pipeline as _p
    _p.Pipeline(db).assemble_and_classify()
    for fmt in FORMAT_IDS:
        post = build_draft(db, format=fmt, project_id=pid, project_ids=[pid])
        assert post.format == fmt
        assert post.render()
    db.close()


# --- deterministic validation --------------------------------------------------

def test_validator_passes_a_correctly_built_spotlight(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    post = build_spotlight(db, pid)
    assert validate_post(db, post) == []
    db.close()


def test_validator_flags_a_figure_the_project_does_not_support(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    post = build_spotlight(db, pid)
    post.lines.insert(0, FactLine(text="Declared value: $999M", kind="verified"))
    problems = validate_post(db, post)
    assert any("$999M" in p for p in problems)
    db.close()


def test_validator_flags_an_unsupported_date(tmp_path):
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    post = build_spotlight(db, pid)
    post.lines.insert(0, FactLine(text="Permit dated 1999-01-01", kind="verified"))
    assert any("1999-01-01" in p for p in validate_post(db, post))
    db.close()


def test_validator_ignores_interpretation_lines(tmp_path):
    """The guard only judges verified lines; interpretation is allowed to be prose."""
    db = _db(tmp_path, [_permit("A")])
    pid = discover_candidates(db)[0].project_id
    post = build_spotlight(db, pid)
    post.lines.append(FactLine(text="Roughly $5M of work, we think.", kind="interpretation"))
    assert validate_post(db, post) == []
    db.close()


# --- HTTP ---------------------------------------------------------------------

def _grant_admin(app_db, email: str) -> None:
    from oppintel.db import Database as D

    db = D(app_db.config["APP_CONFIG"].database_path)
    try:
        db.conn.execute("UPDATE app_user SET access_level = 'ADMIN' WHERE email = ?",
                        (email.lower(),))
        db.conn.commit()
    finally:
        db.close()


@pytest.fixture
def operator_client(app_db):
    c = app_db.test_client()
    c.post("/signup", data={
        "email": "op@example.com", "password": "correct-horse-battery",
        "password_confirm": "correct-horse-battery", "display_name": "Op",
    }, follow_redirects=True)
    _grant_admin(app_db, "op@example.com")
    return c


def test_linkedin_page_requires_signin(client):
    resp = client.get("/content/linkedin")
    assert resp.status_code in (302, 401)


def test_linkedin_page_forbidden_for_non_admin(session_client):
    resp = session_client.get("/content/linkedin")
    assert resp.status_code == 403


def test_linkedin_page_renders_for_operator(operator_client):
    resp = operator_client.get("/content/linkedin")
    assert resp.status_code == 200
    assert "LinkedIn Content Intelligence" in resp.get_data(as_text=True)


def test_linkedin_page_builds_a_draft(operator_client):
    resp = operator_client.get("/content/linkedin?format=market_pulse")
    assert resp.status_code == 200
    assert "Post body" in resp.get_data(as_text=True)


def test_linkedin_page_handles_unknown_format(operator_client):
    resp = operator_client.get("/content/linkedin?format=bogus")
    assert resp.status_code == 200
    assert "Unknown format" in resp.get_data(as_text=True)
