"""Tests for the Structured Natural Language Search Interpreter."""

from __future__ import annotations

from oppintel.nl_search import StructuredSearchInterpreter


def test_parses_city_and_trade_evidence_and_freshness():
    interpreter = StructuredSearchInterpreter()
    query = "Show me commercial projects in Plano with mechanical evidence updated in the last 30 days"
    res = interpreter.parse(query)

    assert res.is_structured is True
    assert res.extracted_filters.get("city") == "Plano"
    assert res.extracted_filters.get("mechanical_only") is True
    assert res.extracted_filters.get("freshness_days") == 30
    assert any(b.label == "City" and b.value == "Plano" for b in res.badges)
    assert any(b.label == "Trade Evidence" for b in res.badges)
    assert any(b.label == "Updated" and "30" in b.value for b in res.badges)


def test_parses_value_and_tier_1_permit():
    interpreter = StructuredSearchInterpreter()
    query = "Dallas healthcare with tier 1 mechanical permit over $1M"
    res = interpreter.parse(query)

    assert res.is_structured is True
    assert res.extracted_filters.get("city") == "Dallas"
    assert res.extracted_filters.get("classification") == "HIGH"
    assert res.extracted_filters.get("min_value") == 1_000_000


def test_plain_address_query_remains_unstructured():
    interpreter = StructuredSearchInterpreter()
    query = "1200 Main St"
    res = interpreter.parse(query)

    assert res.is_structured is False
    assert res.clean_keyword is not None
    assert "1200 main st" in res.clean_keyword.lower()
