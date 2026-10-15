"""Application tests for the AI analyst surfaces.

The analyst's grounding logic is covered by `test_ai_analyst.py`; these tests cover the surfaces:
the operator page, the JSON endpoint, the CLI command, and the crawler rules. They run against a
real database and a real application, and — deliberately — against a debug configuration with no
`GEMINI_API_KEY` exported, so no test reaches the public Gemini endpoint.
"""

from __future__ import annotations

import json

import pytest

from conftest_app import build_database, grant_admin
from oppintel.app.config import AppConfig
from oppintel.app.main import create_app


@pytest.fixture
def project_id(app_db):
    """The id of a fixture project, read from the same database the client serves."""
    from oppintel.db import Database

    db = Database(app_db.config["APP_CONFIG"].database_path)
    try:
        row = db.conn.execute("SELECT id FROM project ORDER BY id LIMIT 1").fetchone()
        return int(row["id"])
    finally:
        db.close()


# --- the operator page -----------------------------------------------------------

def test_ai_analyst_page_requires_sign_in(client):
    response = client.get("/ai-analyst")
    assert response.status_code in (302, 401)
    if response.status_code == 302:
        assert "/signin" in response.headers["Location"]


def test_ai_analyst_page_forbidden_for_a_signed_in_non_admin(session_client):
    assert session_client.get("/ai-analyst").status_code == 403


def test_ai_analyst_page_renders_for_an_operator(admin_client, project_id):
    response = admin_client.get(f"/ai-analyst?project_id={project_id}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "AI Analyst" in body
    # The evidence-only path is taken because no model is configured in the test environment.
    assert "No model is configured" in body or "UNAVAILABLE" in body


def test_ai_analyst_page_is_noindex(admin_client, project_id):
    body = admin_client.get(f"/ai-analyst?project_id={project_id}").get_data(as_text=True)
    assert "noindex" in body


def test_ai_analyst_page_names_unsupported_fields_without_a_value(admin_client, project_id):
    """The page lists what the record does not say, and never presents a value in its place."""
    body = admin_client.get(f"/ai-analyst?project_id={project_id}").get_data(as_text=True)
    assert "Not stated in the record" in body
    # A field listed as unstated is named on its own line, with no value beside it.
    section = body.split("Not stated in the record", 1)[1]
    for line in section.splitlines():
        if "Square footage" in line or "Owner / developer" in line:
            assert ">" in line and "Not verified" not in line


def test_ai_analyst_page_survives_a_missing_project(admin_client):
    response = admin_client.get("/ai-analyst?project_id=999999")
    assert response.status_code == 200
    assert "Could not analyse" in response.get_data(as_text=True)


# --- the JSON endpoint -----------------------------------------------------------

def test_analyst_api_requires_authentication(client, project_id):
    response = client.get(f"/api/project/{project_id}/analyst")
    assert response.status_code == 401
    assert response.get_json()["error"]


def test_analyst_api_forbidden_for_a_non_admin(session_client, project_id):
    assert session_client.get(f"/api/project/{project_id}/analyst").status_code == 403


def test_analyst_api_returns_the_brief_for_an_operator(admin_client, project_id):
    response = admin_client.get(f"/api/project/{project_id}/analyst")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["project_id"] == project_id
    assert payload["status"] in ("AVAILABLE", "UNVERIFIED", "UNAVAILABLE")
    assert payload["caveat"]
    assert "verified" in payload


def test_analyst_api_404s_for_an_unknown_project(admin_client):
    assert admin_client.get("/api/project/999999/analyst").status_code == 404


# --- crawler rules ---------------------------------------------------------------

def test_robots_disallows_the_analyst_route(client):
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /ai-analyst" in body


def test_analyst_route_is_absent_from_the_sitemap(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "ai-analyst" not in body


def test_analyst_route_is_not_linked_from_a_public_page(client):
    for path in ("/", "/opportunities", "/companies"):
        body = client.get(path).get_data(as_text=True)
        assert "/ai-analyst" not in body, f"{path} links the operator analyst route"


# --- the CLI command -------------------------------------------------------------

def test_analyst_cli_command_is_registered(tmp_path):
    db = build_database(tmp_path / "cli.db")
    row = db.conn.execute("SELECT id FROM project ORDER BY id LIMIT 1").fetchone()
    project_id = int(row["id"])
    db.close()

    cfg = AppConfig(database_path=tmp_path / "cli.db", secret_key="test", debug=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    assert "analyst-project" in app.cli.commands

    result = app.test_cli_runner().invoke(args=["analyst-project", str(project_id), "--no-model"])
    assert result.exit_code == 0, result.output
    assert "AI analyst" in result.output
    assert "UNAVAILABLE" in result.output
    assert "permit evidence" in result.output.lower()

    as_json = app.test_cli_runner().invoke(
        args=["analyst-project", str(project_id), "--no-model", "--json"]
    )
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.output)
    assert payload["status"] == "UNAVAILABLE"
    assert payload["unsupported_fields"]


def test_analyst_cli_reports_a_missing_project(tmp_path):
    cfg = AppConfig(database_path=tmp_path / "empty.db", secret_key="test", debug=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    result = app.test_cli_runner().invoke(args=["analyst-project", "999999", "--no-model"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower()
