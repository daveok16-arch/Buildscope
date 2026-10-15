"""AI analyst tests.

The evidence pipeline and the verification pass run against a real SQLite database with no
network at all. The model is reached through the `AnalystClient` protocol, so the transport is a
small in-process fake for the logic tests and a real local HTTP server for the transport test.
No test calls the public Gemini endpoint.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest_app import build_database
from oppintel.ai_analyst import (
    CONFIDENCE_LEVELS,
    STATUS_AVAILABLE,
    STATUS_UNAVAILABLE,
    STATUS_UNVERIFIED,
    AnalystUnavailable,
    GeminiClient,
    api_key_from_env,
    assemble_dossier,
    build_brief,
    collect_facts,
    deterministic_narrative,
    validate_brief,
)
from oppintel.report_generator import PERMIT_EVIDENCE_NOTICE
from oppintel.reporting import CONFIRMED, NOT_VERIFIED as VERDICT_NOT_VERIFIED


# --- fixtures and helpers --------------------------------------------------------

@pytest.fixture
def analyst_db(tmp_path):
    db = build_database(tmp_path / "analyst.db")
    yield db
    db.close()


def _project_id(db, *, name: str | None = None) -> int:
    if name:
        row = db.conn.execute(
            "SELECT id FROM project WHERE project_name LIKE ? ORDER BY id LIMIT 1",
            (f"%{name}%",),
        ).fetchone()
        assert row, f"no fixture project matching {name!r}"
        return int(row["id"])
    row = db.conn.execute("SELECT id FROM project ORDER BY id LIMIT 1").fetchone()
    return int(row["id"])


class FakeAnalystClient:
    """An in-process model transport. Returns a fixed payload or raises."""

    def __init__(self, payload=None, *, available=True, error=None, model="fake-model"):
        self._payload = payload
        self._available = available
        self._error = error
        self.model = model
        self.calls: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        return self._available

    def analyse(self, system: str, dossier: str):
        self.calls.append((system, dossier))
        if self._error is not None:
            raise self._error
        return dict(self._payload or {})


def _passing_payload(facts) -> dict:
    """A model payload that should pass verification, derived from the facts themselves."""
    return {
        "summary": "The record documents a mechanical permit for this project.",
        "why_it_matters": "Mechanical permit evidence is on file for the configured trade.",
        "field_assessments": [
            {"field": f.field_name, "verdict": f.verdict, "reason": "Matches the record."}
            for f in facts
            if f.field_name
        ],
        "questions_to_resolve": ["Confirm the scope with the issuing jurisdiction."],
        "confidence": "medium",
        "limitations": ["No source states how the work will be procured."],
    }


# --- fact collection: grounding --------------------------------------------------

def test_facts_are_emitted_only_with_support(analyst_db):
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    assert facts
    for fact in facts:
        if fact.kind == "verified":
            assert fact.display, "a verified fact must carry a displayable value"
            assert fact.verdict in (CONFIRMED, "PARTIALLY VERIFIED")


def test_unsupported_fields_become_explicit_unknowns(analyst_db):
    """A field no source supports is named as unstated, never given a value."""
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    unsupported = [f for f in facts if f.kind == "unverified"]
    assert unsupported, "the fixture dataset has fields with no evidence"
    for fact in unsupported:
        assert fact.value is None, "an unsupported field must not carry a value"
        assert fact.display == ""
        assert fact.verdict == VERDICT_NOT_VERIFIED


def test_no_fact_carries_the_not_verified_marker_as_a_value(analyst_db):
    pid = _project_id(analyst_db)
    for fact in collect_facts(analyst_db, pid):
        assert "not verified" not in str(fact.value or "").lower()
        assert "not verified" not in fact.display.lower()


def test_permits_and_sources_are_grounded(analyst_db):
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    permits = [f for f in facts if f.fact_id.startswith("permit:")]
    sources = [f for f in facts if f.fact_id.startswith("source:")]
    assert permits, "a project is built from permits"
    assert sources, "a project must have at least one source record"


def test_missing_project_raises(analyst_db):
    from oppintel.ai_analyst import AnalystError

    with pytest.raises(AnalystError):
        collect_facts(analyst_db, 10_000_000)


# --- dossier --------------------------------------------------------------------

def test_dossier_labels_unsupported_fields_and_carries_the_notice(analyst_db):
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    dossier = assemble_dossier(pid, facts, market="DFW", trade="Commercial HVAC")
    assert "SUPPORTED FACTS" in dossier.text
    assert "NOT STATED BY ANY SOURCE" in dossier.text
    assert "not stated in the record" in dossier.text
    # The system instruction, not the dossier, carries the caveat; the dossier must not present an
    # unsupported field with a value.
    assert "prompt_version: 1" in dossier.text


def test_dossier_never_prints_a_value_for_an_unsupported_field(analyst_db):
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    dossier = assemble_dossier(pid, facts, market="DFW", trade="Commercial HVAC")
    tail = dossier.text.split("NOT STATED BY ANY SOURCE", 1)[1]
    for line in tail.splitlines():
        if "not stated in the record" in line:
            assert ":" in line
            # "field: not stated in the record" — nothing after the colon but the marker.
            assert line.strip().endswith("not stated in the record")


# --- verification ----------------------------------------------------------------

def _brief_with(facts, **overrides):
    from oppintel.ai_analyst import AnalystBrief, FieldAssessment

    payload = _passing_payload(facts)
    payload.update(overrides)
    brief = AnalystBrief(
        project_id=0,
        slug=None,
        status=STATUS_AVAILABLE,
        model="fake",
        generated_at="2026-01-01 00:00 UTC",
        narrative=payload["summary"],
        why_it_matters=payload["why_it_matters"],
        field_assessments=[FieldAssessment(**a) for a in payload["field_assessments"]],
        questions_to_resolve=payload["questions_to_resolve"],
        confidence=payload["confidence"],
        limitations=payload["limitations"],
        facts=facts,
    )
    return brief


def test_a_grounded_payload_passes_verification(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    assert validate_brief(_brief_with(facts), facts) == []


def test_an_unsupported_figure_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts, summary="The declared value is 9,999,999.")
    issues = validate_brief(brief, facts)
    assert any("9999999" in i for i in issues)


def test_a_scale_shorthand_of_a_stored_value_is_accepted(analyst_db):
    """2.5M is a legitimate paraphrase of a stored 2,500,000, so it must not be flagged."""
    facts = collect_facts(analyst_db, _project_id(analyst_db, name="spec suite"))
    brief = _brief_with(facts, summary="The declared value is $2.5M.")
    assert validate_brief(brief, facts) == []


def test_an_unsupported_date_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts, summary="The permit was filed on 1999-01-01.")
    issues = validate_brief(brief, facts)
    assert any("1999-01-01" in i for i in issues)


def test_restating_the_not_verified_marker_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts, summary="Square footage is not verified.")
    issues = validate_brief(brief, facts)
    assert any("not-verified marker" in i for i in issues)


def test_bid_language_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts, why_it_matters="This package is out for bid now.")
    issues = validate_brief(brief, facts)
    assert any("bid" in i for i in issues)


def test_restating_the_caveat_is_not_flagged(analyst_db):
    """The caveat is itself a negation, so a model restating it is being honest, not claiming."""
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(
        facts,
        limitations=[
            "Public permit evidence does not by itself confirm that the mechanical package is "
            "currently available for bid."
        ],
    )
    assert validate_brief(brief, facts) == []


def test_a_claim_beside_a_negation_is_still_flagged(analyst_db):
    """One negated occurrence must not excuse a second, unnegated one."""
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(
        facts,
        why_it_matters="The record does not confirm a bid. The package is out for bid now.",
    )
    issues = validate_brief(brief, facts)
    assert any("out for bid" in i for i in issues)


def test_a_negation_far_from_the_phrase_does_not_excuse_it(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(
        facts,
        why_it_matters=(
            "No source states how the work will be procured, and on a separate point the "
            "mechanical package is out for bid right now."
        ),
    )
    issues = validate_brief(brief, facts)
    assert any("out for bid" in i for i in issues)


def test_a_regraded_field_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts)
    # Upgrade an unsupported field to CONFIRMED, which the record does not support.
    unsupported = next(a for a in brief.field_assessments if a.verdict == VERDICT_NOT_VERIFIED)
    unsupported.verdict = CONFIRMED
    issues = validate_brief(brief, facts)
    assert any(unsupported.field in i and "supports" in i for i in issues)


def test_an_unknown_field_assessment_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts)
    brief.field_assessments.append(
        type(brief.field_assessments[0])(field="not_a_field", verdict=CONFIRMED, reason="x")
    )
    issues = validate_brief(brief, facts)
    assert any("not_a_field" in i for i in issues)


def test_a_url_in_prose_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts, summary="See https://example.gov/record for details.")
    issues = validate_brief(brief, facts)
    assert any("URL" in i for i in issues)


def test_a_missing_caveat_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db))
    brief = _brief_with(facts)
    brief.caveat = ""
    issues = validate_brief(brief, facts)
    assert any("caveat" in i for i in issues)


# --- build_brief: statuses -------------------------------------------------------

def test_brief_is_unavailable_without_a_model(analyst_db):
    brief = build_brief(analyst_db, _project_id(analyst_db), client=GeminiClient(api_key=""))
    assert brief.status == STATUS_UNAVAILABLE
    assert brief.is_verified is False
    assert brief.narrative, "the evidence summary is still produced"
    assert brief.limitations
    assert brief.caveat == PERMIT_EVIDENCE_NOTICE


def test_brief_is_unavailable_when_the_transport_fails(analyst_db):
    client = FakeAnalystClient(error=AnalystUnavailable("Gemini returned HTTP 500."))
    brief = build_brief(analyst_db, _project_id(analyst_db), client=client)
    assert brief.status == STATUS_UNAVAILABLE
    assert any("500" in l for l in brief.limitations)


def test_brief_is_available_when_the_model_is_grounded(analyst_db):
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    client = FakeAnalystClient(_passing_payload(facts))
    brief = build_brief(analyst_db, pid, client=client)
    assert brief.status == STATUS_AVAILABLE
    assert brief.is_verified is True
    assert brief.verification_issues == []
    assert brief.narrative == "The record documents a mechanical permit for this project."
    assert client.calls, "the model must actually be called"


def test_a_fabricating_model_is_demoted_not_published(analyst_db):
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    payload = _passing_payload(facts)
    payload["summary"] = "The project is worth $9,999,999 and is out for bid."
    payload["confidence"] = "high"
    payload["limitations"] = ["The square footage is not verified.", "The owner is not stated."]
    brief = build_brief(analyst_db, pid, client=FakeAnalystClient(payload))
    assert brief.status == STATUS_UNVERIFIED
    assert brief.is_verified is False
    assert brief.verification_issues
    # The narrative is replaced with the deterministic, evidence-only summary.
    assert brief.narrative == deterministic_narrative(brief)
    assert "assembled directly from the stored evidence" in brief.narrative
    assert brief.confidence == CONFIDENCE_LEVELS[-1]
    # No model prose survives at all: the offending limitation is gone, not merely annotated.
    assert brief.why_it_matters == ""
    assert brief.field_assessments == []
    assert brief.questions_to_resolve == []
    assert all("not verified" not in l.lower() for l in brief.limitations)
    # The published fields are clean. `verification_issues` is a separate operator diagnostic and
    # is allowed to quote the offending token, so it is excluded from this check on purpose.
    published = brief.to_dict()
    for key in ("summary", "why_it_matters", "field_assessments", "limitations",
                "questions_to_resolve"):
        encoded = json.dumps(published[key]).lower()
        assert "not verified" not in encoded
        assert "9999999" not in encoded
        assert "out for bid" not in encoded


def test_brief_json_uses_the_canonical_verdict_not_a_value_marker(analyst_db):
    """The JSON may carry a field verdict, but never the marker as a field's value.

    The API already exposes `field_status` with the canonical `NOT VERIFIED` verdict, so the
    analyst uses the same vocabulary. What must never appear is a *value* of "Not verified".
    """
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    brief = build_brief(analyst_db, pid, client=FakeAnalystClient(_passing_payload(facts)))
    payload = brief.to_dict()
    assert "Not verified" not in json.dumps(payload)
    for assessment in payload["field_assessments"]:
        assert assessment["verdict"] in (
            "CONFIRMED", "PARTIALLY VERIFIED", "NOT VERIFIED",
        ), "a verdict must use the canonical vocabulary, not a display string"


def test_brief_json_carries_the_caveat_and_unsupported_fields(analyst_db):
    pid = _project_id(analyst_db)
    brief = build_brief(analyst_db, pid, client=GeminiClient(api_key=""))
    payload = brief.to_dict()
    assert payload["caveat"] == PERMIT_EVIDENCE_NOTICE
    assert payload["unsupported_fields"], "the record has unsupported fields"
    assert payload["status"] == STATUS_UNAVAILABLE


# --- the transport, against a real local HTTP server ------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    payload: dict = {}
    status: int = 200

    def do_POST(self):  # noqa: N802 - the handler API is fixed
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the test server
        return


@pytest.fixture
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    server.RequestHandlerClass.status = 200
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_gemini_client_parses_a_structured_response(stub_server):
    stub_server.RequestHandlerClass.payload = {
        "candidates": [
            {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(_passing_payload([]))}]}}
        ]
    }
    client = GeminiClient(
        api_key="test-key",
        model="gemini-2.5-flash",
        endpoint=f"http://127.0.0.1:{stub_server.server_port}",
    )
    assert client.available is True
    payload = client.analyse("system", "dossier")
    assert payload["confidence"] == "medium"


def test_gemini_client_rejects_a_blocked_response(stub_server):
    stub_server.RequestHandlerClass.payload = {"promptFeedback": {"blockReason": "SAFETY"}}
    client = GeminiClient(
        api_key="test-key",
        model="gemini-2.5-flash",
        endpoint=f"http://127.0.0.1:{stub_server.server_port}",
    )
    with pytest.raises(AnalystUnavailable):
        client.analyse("system", "dossier")


def test_gemini_client_condenses_an_http_error(stub_server):
    """A transport failure must read as a sentence, with no URL and no raw JSON body."""
    stub_server.RequestHandlerClass.payload = {
        "error": {
            "code": 429,
            "message": (
                "You exceeded your current quota, please check your plan and billing details. "
                "For more information, head to: https://ai.google.dev/gemini-api/docs/rate-limits"
            ),
        }
    }
    stub_server.RequestHandlerClass.status = 429
    client = GeminiClient(
        api_key="test-key",
        model="gemini-2.5-flash",
        endpoint=f"http://127.0.0.1:{stub_server.server_port}",
    )
    with pytest.raises(AnalystUnavailable) as excinfo:
        client.analyse("system", "dossier")
    message = str(excinfo.value)
    assert "429" in message
    assert "https://" not in message
    assert "{" not in message


def test_gemini_client_is_unavailable_without_a_key():
    assert GeminiClient(api_key="").available is False
    assert GeminiClient(api_key="   ").available is False


def test_api_key_reads_either_documented_name(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("Gemini_API_Token", raising=False)
    assert api_key_from_env() == ""
    monkeypatch.setenv("Gemini_API_Token", "sandbox-key")
    assert api_key_from_env() == "sandbox-key"
    monkeypatch.setenv("GEMINI_API_KEY", "documented-key")
    assert api_key_from_env() == "documented-key"
