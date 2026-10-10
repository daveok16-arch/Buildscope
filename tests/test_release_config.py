"""Release configuration (WP3 K1c/K1e).

Two release-readiness contracts:

* **K1c preview mode** — a preview deployment with no disk shows a banner saying the data may
  reset on redeploy. The flag is config-driven (`PREVIEW_MODE`), never hardcoded, and the
  banner must not appear on a real deployment.
* **K1e collection window** — the permit-date window the site states is derived from the same
  `config/sources.yaml` the pipeline ingests with, so the disclosure cannot drift from reality.
  A prior copy hardcoded "175"/"12" style figures; that class of staleness is what this forbids.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest_app import build_database

from oppintel.app.config import AppConfig
from oppintel.app.main import create_app
from oppintel.config import SourceConfig, ingestion_window_since


# --- K1e: the window comes from configuration ----------------------------------

def test_ingestion_window_is_the_earliest_enabled_bound():
    sources = {
        "a": SourceConfig(id="a", name="A", publisher="P", kind="arcgis", since_months=24),
        "b": SourceConfig(id="b", name="B", publisher="P", kind="socrata", since_months=6),
    }
    result = ingestion_window_since(sources, today=date(2026, 10, 9))
    assert result == date(2024, 10, 9)


def test_ingestion_window_ignores_disabled_sources():
    sources = {
        "a": SourceConfig(id="a", name="A", publisher="P", kind="arcgis", since_months=24),
        "b": SourceConfig(id="b", name="B", publisher="P", kind="socrata", since_months=120, enabled=False),
    }
    assert ingestion_window_since(sources, today=date(2026, 10, 9)) == date(2024, 10, 9)


def test_ingestion_window_is_none_when_a_source_keeps_all_history():
    """A source with no bound means the dataset has no single lower bound to state."""
    sources = {
        "a": SourceConfig(id="a", name="A", publisher="P", kind="arcgis", since_months=24),
        "b": SourceConfig(id="b", name="B", publisher="P", kind="socrata", since_months=None, since=None),
    }
    assert ingestion_window_since(sources, today=date(2026, 10, 9)) is None


def test_explicit_since_beats_since_months():
    sources = {"a": SourceConfig(id="a", name="A", publisher="P", kind="arcgis", since="2023-01-01", since_months=24)}
    assert ingestion_window_since(sources, today=date(2026, 10, 9)) == date(2023, 1, 1)


def test_shipped_sources_resolve_a_window():
    """The shipped config must declare a bound, so the disclosure line is populated."""
    assert ingestion_window_since() is not None


# --- K1e: the disclosure renders and is not hardcoded ---------------------------

@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "release.db"
    db = build_database(db_path)
    db.close()
    cfg = AppConfig(database_path=db_path, secret_key="release-secret", debug=True)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app.test_client()


def test_collection_window_appears_on_key_surfaces(client):
    for path in ("/", "/markets", "/how-it-works"):
        body = client.get(path).get_data(as_text=True)
        assert "Permits filed since" in body, f"{path} is missing the collection window"
        # It is a real date, never the sentinel or a placeholder.
        assert "1900" not in body


def test_collection_window_appears_in_the_footer_on_every_route(client):
    for path in ("/", "/opportunities", "/trends", "/companies", "/signin"):
        body = client.get(path).get_data(as_text=True)
        assert "Permits filed since" in body, f"{path} footer is missing the collection window"


# --- K1c: preview mode ----------------------------------------------------------

def test_preview_mode_off_by_default(monkeypatch):
    monkeypatch.delenv("PREVIEW_MODE", raising=False)
    assert AppConfig(debug=False).preview_mode is False


def test_preview_mode_env_override(monkeypatch):
    monkeypatch.setenv("PREVIEW_MODE", "1")
    assert AppConfig(debug=True).preview_mode is True


def test_preview_banner_hidden_when_not_in_preview(client):
    assert "Preview build:" not in client.get("/").get_data(as_text=True)


def test_preview_banner_shown_in_preview(tmp_path):
    db_path = tmp_path / "preview.db"
    db = build_database(db_path)
    db.close()
    cfg = AppConfig(database_path=db_path, secret_key="preview-secret", debug=True)
    cfg.preview_mode = True
    app = create_app(cfg)
    app.config["TESTING"] = True
    client = app.test_client()
    body = client.get("/").get_data(as_text=True)
    assert "Preview build:" in body
    assert "data may reset on redeploy" in body
    # And on a non-home route too, since it lives in the base template.
    assert "Preview build:" in client.get("/opportunities").get_data(as_text=True)


# --- K1c: the deployment assets themselves (text/parse only, no Docker) ----------

def _yaml(path):
    import yaml

    return yaml.safe_load((_repo() / path).read_text())


def _repo():
    from pathlib import Path

    return Path(__file__).resolve().parents[1]


def test_render_preview_blueprint_has_no_disk_and_sets_preview_mode():
    """A Blueprint with a disk on a Free instance does not apply at all, so it must not have one."""
    blueprint = _yaml("render.yaml")
    service = blueprint["services"][0]
    assert "disk" not in service
    assert service["plan"] == "free"
    env = {item["key"]: item.get("value") for item in service["envVars"]}
    assert env.get("PREVIEW_MODE") == "1"
    # No persistent-disk paths: they would point at a mount that does not exist on Free.
    assert "OPPINTEL_DB" not in env
    assert "OPPINTEL_DATA_DIR" not in env


def test_render_production_example_is_the_paid_disk_variant():
    blueprint = _yaml("render.production.example.yaml")
    service = blueprint["services"][0]
    assert service["plan"] != "free"
    assert service["disk"]["mountPath"] == "/var/data"
    env = {item["key"]: item.get("value") for item in service["envVars"]}
    assert env.get("OPPINTEL_DATA_DIR") == "/var/data"
    assert env.get("OPPINTEL_DB") == "/var/data/oppintel.db"
    # A production deploy must not advertise itself as a preview.
    assert "PREVIEW_MODE" not in env


def test_dockerfile_is_the_foreground_container_entrypoint():
    dockerfile = (_repo() / "Dockerfile").read_text()
    # The container host path must stay in the foreground (a backgrounded process looks dead).
    assert 'CMD ["bash", "ops/start.sh"]' in dockerfile
    assert "FOREGROUND=1" in dockerfile
    assert "PYTHONPATH=src" in dockerfile


def test_release_notes_portable_documents_the_env_contract():
    notes = (_repo() / "audit" / "RELEASE_NOTES_PORTABLE.md").read_text()
    for token in ("PREVIEW_MODE", "OPPINTEL_DATA_DIR", "GZIP_ENABLED", "Dockerfile", "render.yaml"):
        assert token in notes, f"RELEASE_NOTES_PORTABLE.md is missing {token}"
