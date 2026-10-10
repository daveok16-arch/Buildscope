"""New filings view (WP3 K4).

A fresh sample of filings the trade is not verified for. The page must never claim trade
evidence, must be reachable from the nav, and must not be indexed (it is a moving subset).
"""

from __future__ import annotations


def test_new_filings_route_renders(client):
    response = client.get("/opportunities/new")
    assert response.status_code == 200


def test_new_filings_page_names_itself_as_unverified(client):
    body = client.get("/opportunities/new").get_data(as_text=True)
    assert "not yet trade-verified" in body.lower()


def test_new_filings_never_claims_mechanical_evidence(client):
    """No card on this page may claim verified mechanical scope."""
    body = client.get("/opportunities/new?page_size=50").get_data(as_text=True)
    for card in body.split('<article class="card">')[1:]:
        assert "Mechanical permit on file" not in card
        assert "Confirmed mechanical permit" not in card


def test_new_filings_is_noindex(client):
    body = client.get("/opportunities/new").get_data(as_text=True)
    assert "noindex" in body


def test_new_filings_linked_from_navigation(client):
    body = client.get("/").get_data(as_text=True)
    assert 'href="/opportunities/new"' in body
