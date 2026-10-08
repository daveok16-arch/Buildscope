"""Configuration-driven search vocabulary tests.

Phase 5: search must understand the trade's own shorthand. A contractor types "ahu"; the
permit text says "air handling unit". These tests cover three layers against real code and a
real SQLite/FTS5 database — no mocks:

* the vocabulary is loaded from configuration and is internally consistent;
* a query is expanded to whole-phrase synonyms without leaking loose words or breaking the
  FTS5 syntax that keeps user input safe;
* an end-to-end search through the service finds a project by a synonym it does not literally
  contain.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from oppintel.config import (
    MarketConfig,
    SearchVocabulary,
    active_market,
    active_trade,
    load_search_vocabulary,
)
from oppintel.db import Database
from oppintel.models import Permit, normalize_address
from oppintel.pipeline import Pipeline
from oppintel.search_index import expand_terms, quote_for_fts, rebuild_index, tokenize_query
from oppintel.service import OpportunityFilters, OpportunityService

TRADE_ID = "commercial_hvac"


def _vocab():
    return load_search_vocabulary()


# --- configuration integrity --------------------------------------------------

def test_vocabulary_loads_and_covers_the_trade_shorthand():
    vocab = _vocab()
    assert isinstance(vocab, SearchVocabulary)
    assert vocab.version >= 1
    terms = {t for g in vocab.for_trade(TRADE_ID) for t in g.terms}
    # The shorthand contractors actually type must be present.
    for shorthand in ("ahu", "rtu", "vav", "hvac", "split system", "chiller"):
        assert shorthand in terms, f"{shorthand!r} missing from the search vocabulary"


def test_terms_are_lower_case_and_groups_are_meaningful():
    for group in _vocab().for_trade(TRADE_ID):
        assert group.id, "every group needs an id"
        assert group.label, "every group needs a human label"
        assert len(group.terms) >= 2, f"group {group.id!r} is not an equivalence set"
        for term in group.terms:
            assert term == term.lower(), f"{term!r} must be lower-case to match query tokens"


def test_no_term_is_claimed_by_two_groups():
    """An ambiguous term would expand non-deterministically, so the config forbids it."""
    assert _vocab().conflicts(TRADE_ID) == {}


def test_vocabulary_is_configuration_not_code(tmp_path):
    """A different vocabulary file produces different expansion, with no code change."""
    custom = tmp_path / "vocab.yaml"
    custom.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "groups": [
                    {"id": "custom", "label": "Custom", "terms": ["widget", "gadget"]}
                ],
            }
        )
    )
    vocab = load_search_vocabulary(custom)
    assert "widget" in vocab.term_index()
    assert "ahu" not in vocab.term_index()
    query = quote_for_fts("widget", vocab.for_trade(TRADE_ID))
    assert "gadget" in query


# --- expansion -----------------------------------------------------------------

def test_synonym_expands_to_whole_phrases():
    query = quote_for_fts("ahu", _vocab().for_trade(TRADE_ID))
    assert '"ahu"*' in query
    assert '"air handling unit"*' in query


def test_multi_word_synonym_does_not_leak_loose_words():
    """Expanding 'hvac' to 'mechanical scope' must not also search the bare word 'scope'."""
    query = quote_for_fts("hvac", _vocab().for_trade(TRADE_ID))
    assert '"scope"*' not in query
    assert '"mechanical scope"*' in query


def test_multi_word_synonym_in_the_query_matches_as_one_phrase():
    groups = expand_terms("air handling unit", _vocab().for_trade(TRADE_ID))
    assert len(groups) == 1, "the whole synonym should collapse to a single alternative-set"


def test_unknown_terms_pass_through_unchanged():
    assert quote_for_fts("ross ave", _vocab().for_trade(TRADE_ID)) == '"ross"* AND "ave"*'


def test_expansion_only_widens_the_query():
    """The literal term is always retained in its own OR-group."""
    query = quote_for_fts("rtu", _vocab().for_trade(TRADE_ID))
    assert '"rtu"*' in query


def test_empty_and_whitespace_input_yield_no_query():
    vocab = _vocab().for_trade(TRADE_ID)
    assert quote_for_fts("", vocab) == ""
    assert quote_for_fts("   ", vocab) == ""


def test_hostile_input_produces_a_well_formed_query():
    """FTS5 operators and quotes in user input must not raise or alter the query shape."""
    vocab = _vocab().for_trade(TRADE_ID)
    for hostile in ['a " b', "AND OR NOT", '") (', "ahu OR rtu", "*", "'"]:
        query = quote_for_fts(hostile, vocab)
        # Every operator we emit is uppercase; the input's own text is always quoted.
        assert '"' in query or query == ""


def test_tokenizer_matches_the_fts_tokenizer():
    assert tokenize_query("AHU-1 (2)") == ["ahu", "1", "2"]


# --- end-to-end search ---------------------------------------------------------

def _permit(number: str, description: str, address: str) -> Permit:
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


#: A reproducible search-quality set: a query, the work description of the one project it must
#: find, and a fragment unique to that project. Each pair is a real synonym gap — the query
#: word never appears in the indexed text, so a match can only come from the configured
#: vocabulary. Each description names construction scope and each address carries a street
#: number, so the permit assembles into a project the directory lists.
SEARCH_QUALITY_CASES = [
    ("ahu", "New construction of a building with an air handling unit on the roof", "air handling unit"),
    ("rtu", "Renovation including a new rooftop unit and associated ductwork", "rooftop unit"),
    ("split system", "Tenant improvement installing a new mini split", "mini split"),
    ("chilled water", "New building with a chiller installation", "chiller"),
]


def _searchable_db(tmp_path) -> Database:
    path = Path(tmp_path) / "search.db"
    db = Database(path)
    db.init_schema()
    db.init_app_schema()
    pipeline = Pipeline(db)
    pipeline._register_sources()
    for i, (_query, description, _fragment) in enumerate(SEARCH_QUALITY_CASES):
        permit = _permit(f"Q{i}", description, f"{100 + i} MAIN ST")
        db.upsert_permit(permit, normalize_address(permit.address))
    # A project with none of the equipment, to prove search is actually discriminating.
    plain = _permit("QNONE", "New construction of a plain office building", "200 PLAIN ST")
    db.upsert_permit(plain, normalize_address(plain.address))
    db.commit()
    pipeline.assemble_and_classify()
    rebuild_index(db)
    return db


def test_search_finds_a_project_by_a_synonym_it_does_not_contain(tmp_path):
    """Each query must find its own project, and not the unrelated one.

    The query word never appears in the project's text (asserted first), so a match can only
    come from the configured vocabulary.
    """
    db = _searchable_db(tmp_path)
    service = OpportunityService(db, active_market(), active_trade())
    for i, (query, description, _fragment) in enumerate(SEARCH_QUALITY_CASES):
        assert query not in description.lower(), "the query word must not appear literally"
        page = service.list_opportunities(OpportunityFilters(q=query))
        assert page.total == 1, f"{query!r} matched {page.total} projects, expected 1"
        returned = page.items[0]
        assert returned.get("address") == f"{100 + i} MAIN ST", (
            f"{query!r} returned the wrong project: {returned.get('address')!r}"
        )
    db.close()


def test_search_for_unknown_term_returns_nothing(tmp_path):
    db = _searchable_db(tmp_path)
    service = OpportunityService(db, active_market(), active_trade())
    page = service.list_opportunities(OpportunityFilters(q="zzzznotathing"))
    assert page.total == 0
    db.close()
