"""Tests for Company & Stakeholder Intelligence."""

from __future__ import annotations

from oppintel.companies import CompanyService, clean_company_name


def test_clean_company_name():
    assert clean_company_name("  DPR Construction  ") == "DPR Construction"
    assert clean_company_name('"Turner Construction"') == "Turner Construction"
    assert clean_company_name("N/A") is None
    assert clean_company_name("UNKNOWN") is None
    assert clean_company_name("none") is None


def test_list_companies_from_app_db(app_db):
    service = CompanyService(app_db)
    companies = service.list_companies(limit=10)
    assert isinstance(companies, list)
    for c in companies:
        assert c.name
        assert c.slug
        assert c.project_count >= 1
