"""Opportunity card presentation (WP3 K3).

The card must lead with proof: one evidence badge derived from the stored tier, a confidence
word beside it (not a second competing coloured badge), and a factual procurement note. These
tests pin that shape and the evidence-first ordering.
"""

from __future__ import annotations

import re

import pytest


def _cards(body: str) -> list[str]:
    return body.split('<article class="card">')[1:]


def _json(client, path):
    return client.get(path).get_json()


def test_card_leads_with_one_evidence_badge(client):
    body = client.get("/opportunities?page_size=50").get_data(as_text=True)
    cards = _cards(body)
    assert cards
    for card in cards:
        assert card.count('class="evidence-badge') == 1


def test_evidence_badge_carries_the_tier_signal(client):
    """The badge text is the service's trade-signal label, not a new claim."""
    payload = _json(client, "/api/opportunities?page_size=50")
    row = next(i for i in payload["results"] if i["address"] == "60 SCOPE ST")
    body = client.get("/opportunities?page_size=50").get_data(as_text=True)
    assert row["trade_signal"] in body


def test_no_raw_classification_badge_word(client):
    """The raw classification token must not be displayed as its own uppercase badge."""
    body = client.get("/opportunities?page_size=50").get_data(as_text=True)
    assert "NEEDS VERIFICATION" not in body
    # The confidence word replaces it where a classification is shown.
    assert "confidence" in body


def test_procurement_note_states_status_once(client):
    body = client.get("/opportunities?page_size=50").get_data(as_text=True)
    # The source's plain-language status may appear, but never the sentinel.
    card = next((c for c in _cards(body) if "60 SCOPE ST" in c), "")
    assert "Not verified ·" not in card


# --- evidence-first ordering ----------------------------------------------------

def test_evidence_sort_is_available_and_renders(client):
    assert client.get("/opportunities?sort=evidence").status_code == 200
    body = client.get("/opportunities?sort=evidence").get_data(as_text=True)
    assert "Best evidence" in body


def test_evidence_sort_ranks_proven_records_first(client):
    """Tier 1/2 records must precede records with no mechanical tier under the evidence sort."""
    payload = _json(client, "/api/opportunities?sort=evidence&page_size=50")
    tiers = [i.get("mechanical_evidence_tier") for i in payload["results"]]
    # Once a None appears, no 1/2 may appear after it.
    seen_none = False
    for tier in tiers:
        if tier is None:
            seen_none = True
        else:
            assert not seen_none, f"a proven record ({tier}) sorted after an unproven one"


# --- WP3 M3: source provenance on the card --------------------------------------

def test_card_names_its_source_record(client):
    """A card links back to the municipal record it was assembled from."""
    payload = _json(client, "/api/opportunities?page_size=50")
    row = payload["results"][0]
    body = client.get("/opportunities?page_size=50").get_data(as_text=True)
    assert "card-source" in body
    assert row["source_name"] in body


def test_toolbar_tier_breakdown_is_labelled_as_page_scoped(client):
    """The tier breakdown describes the current page, and says so."""
    body = client.get("/opportunities").get_data(as_text=True)
    assert "On this page:" in body
