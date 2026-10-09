"""Search Intelligence: match explanations, query normalization, and no-result analytics.

Extends the existing search tests (`test_search_vocabulary.py`, `test_search_analytics.py`).
Everything here runs against real code and a real SQLite database — no mocks. The app-layer
tests use the shared `client`/`fixture_db` fixtures from `conftest_app.py`; the unit tests use
the pure functions directly.
"""

from __future__ import annotations

from datetime import date

from oppintel.config import active_market, active_trade, load_search_vocabulary
from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.search_index import (
    explain_matches,
    normalize_query_text,
    quote_for_fts,
    rebuild_index,
)
from oppintel.service import OpportunityFilters, OpportunityService

TRADE_ID = "commercial_hvac"


# --- query normalization -------------------------------------------------------

def test_normalize_collapses_whitespace_and_trims():
    assert normalize_query_text("  hvac   mechanical  ") == "hvac mechanical"


def test_normalize_preserves_case_for_storage():
    assert normalize_query_text("Ross Ave") == "Ross Ave"


def test_normalize_blank_is_none():
    assert normalize_query_text("") is None
    assert normalize_query_text("   ") is None
    assert normalize_query_text(None) is None


# --- match explanations --------------------------------------------------------

def test_explain_names_the_field_that_contains_the_term():
    reasons = explain_matches(
        "plano",
        {"project_name": "Plano Office", "address": "1 Plano Pkwy",
         "city": "Plano", "work_description": "", "permit_number": ""},
    )
    joined = " | ".join(reasons)
    assert "Project name" in joined
    assert "Address" in joined
    assert "City" in joined


def test_explain_uses_permit_description_wording_for_scope():
    reasons = explain_matches("mechanical", {"work_description": "mechanical remodel of suite"})
    assert reasons == ['Permit description mentions "mechanical"']


def test_explain_returns_nothing_when_no_field_contains_the_term():
    """No reason is invented for a record that only matched via a prefix or synonym widening."""
    assert explain_matches("mechanical", {"project_name": "Plaza Tower", "work_description": ""}) == []


def test_explain_matches_whole_words_only():
    """'mechanical' must not be reported because the text says 'mechanically'."""
    assert explain_matches("mechanical", {"work_description": "mechanically fastened panels"}) == []


def test_explain_is_case_insensitive():
    assert explain_matches("HVAC", {"project_name": "hvac services llc"})


# --- vocabulary additions ------------------------------------------------------

def test_new_project_type_groups_are_present_and_distinct():
    vocab = load_search_vocabulary()
    index = vocab.term_index(TRADE_ID)
    assert "build-out" in index
    assert "tenant improvement" in index
    # The two are the same group (worth widening together) ...
    assert index["build-out"].id == index["tenant improvement"].id
    # ... but renovation is deliberately a different group.
    assert index["renovation"].id != index["build-out"].id


def test_electrical_and_plumbing_do_not_expand_into_each_other():
    vocab = load_search_vocabulary().for_trade(TRADE_ID)
    electrical = quote_for_fts("switchgear", vocab)
    plumbing = quote_for_fts("switchgear", vocab)
    assert electrical == plumbing
    # A plumbing term must not surface an electrical term.
    assert "low voltage" not in quote_for_fts("backflow preventer", vocab)


def test_added_groups_are_internally_consistent():
    assert load_search_vocabulary().conflicts(TRADE_ID) == {}


# --- end-to-end search reasons through the service -----------------------------

def _permit(number: str, description: str, address: str, *, name: str | None = None) -> Permit:
    return Permit(
        source_id="fort_worth_permits",
        permit_number=number,
        natural_key=number,
        permit_type="Commercial Mechanical Permit",
        permit_subtype="commercial_mechanical",
        permit_date=date(2026, 9, 1),
        status="Issued",
        address=address,
        city="Fort Worth",
        state="TX",
        work_description=description,
        land_use="OFFICE BUILDING",
        is_commercial=True,
        job_value=1_000_000.0,
        square_footage=20_000.0,
        source_url=f"https://example.gov/{number}",
        source_date=date(2026, 9, 1),
    )


def _db(tmp_path) -> Database:
    db = Database(tmp_path / "search_intel.db")
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    db.upsert_permit(_permit("A1", "Interior tenant improvement with mechanical HVAC scope",
                             "10 ROSS AVE"), normalize_address("10 ROSS AVE"))
    db.upsert_permit(_permit("B1", "New construction of a plain warehouse",
                             "20 PLAIN ST"), normalize_address("20 PLAIN ST"))
    db.commit()
    pipeline.assemble_and_classify()
    rebuild_index(db)
    return db


def test_list_opportunities_attaches_search_reasons(tmp_path):
    db = _db(tmp_path)
    service = OpportunityService(db, active_market(), active_trade())
    page = service.list_opportunities(OpportunityFilters(q="mechanical"))
    assert page.total >= 1
    reasons = [r for item in page.items for r in item.get("search_reasons", [])]
    assert reasons, "a matching query must produce at least one grounded reason"
    assert all("mechanical" in r.lower() for r in reasons)
    db.close()


def test_search_reasons_absent_without_a_query(tmp_path):
    db = _db(tmp_path)
    service = OpportunityService(db, active_market(), active_trade())
    page = service.list_opportunities(OpportunityFilters())
    assert all("search_reasons" not in item for item in page.items)
    db.close()


# --- analytics: no-results event ----------------------------------------------

def _event_count(app_db, name: str) -> int:
    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        return int(
            db.conn.execute(
                "SELECT COUNT(*) FROM analytics_event WHERE event_name = ?", (name,)
            ).fetchone()[0]
        )
    finally:
        db.close()


def test_zero_result_search_records_a_no_results_event(client, app_db):
    resp = client.get("/opportunities?q=zzzznotathing")
    assert resp.status_code == 200
    assert _event_count(app_db, "search_no_results") == 1


def test_successful_search_does_not_record_a_no_results_event(client, app_db):
    client.get("/opportunities?q=mechanical")
    assert _event_count(app_db, "search_no_results") == 0


def test_bare_directory_view_records_no_no_results_event(client, app_db):
    """A directory view with no query is not a zero-result search.

    The existing funnel records a `search_performed` landing event for the directory; what must
    not happen is a `search_no_results` event, which would misreport a blank view as a failed
    search.
    """
    client.get("/opportunities")
    assert _event_count(app_db, "search_no_results") == 0


def test_no_results_page_renders_and_stays_200(client):
    resp = client.get("/opportunities?q=zzzznotathing")
    assert resp.status_code == 200
