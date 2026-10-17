"""AI analyst tests.

The evidence pipeline and the verification pass run against a real SQLite database with no
network at all. The model is reached through the `AnalystClient` protocol, so the transport is a
small in-process fake for the logic tests and a real local HTTP server for the transport test.
No test calls the public Gemini endpoint.
"""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest_app import build_database
from oppintel.ai_analyst import (
    BRIEF_SCHEMA,
    CONFIDENCE_LEVELS,
    PROVIDER_GEMINI,
    PROVIDER_XAI,
    STATUS_AVAILABLE,
    STATUS_UNAVAILABLE,
    STATUS_UNVERIFIED,
    AnalystUnavailable,
    CachingClient,
    GeminiClient,
    GrokClient,
    api_key_from_env,
    assemble_dossier,
    build_brief,
    cache_key,
    collect_facts,
    default_client,
    deterministic_narrative,
    gemini_schema_to_json_schema,
    resolve_provider,
    validate_brief,
    xai_key_from_env,
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


def test_a_value_is_rendered_with_its_unit(analyst_db):
    """A figure in the dossier carries its unit, so the model can tell money from area.

    Without this the dossier shows a bare 30,918 and a bare 685, and a model writing about them
    can attach the wrong unit to the wrong number without inventing either figure.
    """
    from oppintel.ai_analyst import _display

    assert _display("estimated_project_value", 2_500_000) == "$2,500,000"
    assert _display("square_footage", 685) == "685 sq ft"
    pid = _project_id(analyst_db, name="spec suite")
    by_field = {f.field_name: f.display for f in collect_facts(analyst_db, pid)}
    assert by_field["estimated_project_value"] == "$2,500,000"


def test_a_unit_is_read_as_labelling_the_figure_beside_it(analyst_db):
    """Association, not mere presence: "$30,918 for 685 sq ft" marks each figure with its own unit.

    A presence test would let the dollar sign leak onto 685 (and vice versa), which is exactly the
    false positive that would reject a correct sentence.
    """
    from oppintel.ai_analyst import _marked_units_in

    marked = _marked_units_in("The declared value is $30,918 for 685 sq ft.")
    assert marked["30918"] == {"$"}
    assert marked["685"] == {"sq ft"}


def test_a_date_does_not_shift_the_unit_association(analyst_db):
    """A date before a figure must not misalign the figure-to-unit pairing.

    Offsets are taken after dates are blanked, so blanking has to preserve length; a date
    collapsing to one character would move every later figure and pair it with the wrong unit.
    """
    from oppintel.ai_analyst import _marked_units_in

    marked = _marked_units_in("Filed 2026-08-12 with a value of $30,918 and 685 sq ft.")
    assert marked["30918"] == {"$"}
    assert marked["685"] == {"sq ft"}


def test_a_scale_shorthand_is_one_figure_not_two(analyst_db):
    """'2.5M' is the stored 2,500,000, not a bare 2 beside an unrelated figure."""
    from oppintel.ai_analyst import _figure_spans

    numbers = [n for n, _start, _end in _figure_spans("The declared value is $2.5M.")]
    assert numbers == ["2500000"]


def test_a_money_figure_marked_as_an_area_is_flagged(analyst_db):
    """The observed failure: a project value presented as square footage.

    Both figures are stored, so the number check alone passes it; the unit is what makes the
    sentence wrong.
    """
    facts = collect_facts(analyst_db, _project_id(analyst_db, name="spec suite"))
    brief = _brief_with(facts, summary="The project covers 2,500,000 square feet.")
    issues = validate_brief(brief, facts)
    assert any("2500000" in i and "sq ft" in i for i in issues), issues


def test_a_figure_marked_with_a_unit_the_evidence_never_states_is_flagged(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db, name="spec suite"))
    brief = _brief_with(facts, summary="The value is 2,500,000% of the budget.")
    issues = validate_brief(brief, facts)
    assert any("2500000" in i and "%" in i for i in issues), issues


def test_a_correctly_marked_figure_passes(analyst_db):
    facts = collect_facts(analyst_db, _project_id(analyst_db, name="spec suite"))
    brief = _brief_with(facts, summary="The declared value is $2.5M.")
    assert validate_brief(brief, facts) == []


def test_a_bare_figure_without_a_unit_claim_passes(analyst_db):
    """A model that names the number without asserting a unit has not mis-stated anything."""
    facts = collect_facts(analyst_db, _project_id(analyst_db, name="spec suite"))
    brief = _brief_with(facts, summary="The declared value figure is 2,500,000.")
    assert validate_brief(brief, facts) == []


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
    last_body: dict = {}
    last_headers: dict = {}
    last_path: str = ""

    def do_POST(self):  # noqa: N802 - the handler API is fixed
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            type(self).last_body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):  # pragma: no cover - defensive
            type(self).last_body = {}
        type(self).last_headers = dict(self.headers)
        type(self).last_path = self.path
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
    server.RequestHandlerClass.last_body = {}
    server.RequestHandlerClass.last_headers = {}
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


def test_gemini_client_sends_the_key_in_a_header_not_the_url(stub_server):
    """The credential must not travel in the query string.

    A URL is written to proxy access logs, retained by intermediaries and quoted in error traces.
    The key belongs in a header, which those layers do not record by default.
    """
    stub_server.RequestHandlerClass.payload = {
        "candidates": [
            {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(_passing_payload([]))}]}}
        ]
    }
    client = GeminiClient(
        api_key="secret-key-value",
        model="gemini-2.5-flash",
        endpoint=f"http://127.0.0.1:{stub_server.server_port}",
    )
    client.analyse("system", "dossier")

    headers = {k.lower(): v for k, v in stub_server.RequestHandlerClass.last_headers.items()}
    assert headers.get("x-goog-api-key") == "secret-key-value"
    # The handler records the request path; the key must not appear in it.
    assert "secret-key-value" not in stub_server.RequestHandlerClass.last_path


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


# --- the xAI / Grok provider -----------------------------------------------------

def _clear_provider_env(monkeypatch):
    for name in (
        "GEMINI_API_KEY", "Gemini_API_Token", "XAI_API_KEY", "Grok_API_Token",
        "AI_ANALYST_PROVIDER", "GEMINI_MODEL", "XAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_xai_key_reads_either_documented_name(monkeypatch):
    _clear_provider_env(monkeypatch)
    assert xai_key_from_env() == ""
    monkeypatch.setenv("Grok_API_Token", "sandbox-xai")
    assert xai_key_from_env() == "sandbox-xai"
    monkeypatch.setenv("XAI_API_KEY", "documented-xai")
    assert xai_key_from_env() == "documented-xai"


def test_provider_is_empty_with_no_keys(monkeypatch):
    _clear_provider_env(monkeypatch)
    assert resolve_provider() == ""
    assert default_client().available is False, "no key means an unavailable sentinel"


def test_provider_prefers_gemini_then_xai(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    assert resolve_provider() == PROVIDER_XAI
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    assert resolve_provider() == PROVIDER_GEMINI, "Gemini wins by default when both are present"


def test_an_explicit_provider_choice_wins(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.setenv("AI_ANALYST_PROVIDER", "xai")
    assert resolve_provider() == PROVIDER_XAI
    monkeypatch.setenv("AI_ANALYST_PROVIDER", "gemini")
    assert resolve_provider() == PROVIDER_GEMINI


def test_a_named_but_unconfigured_provider_falls_back_to_a_present_key(monkeypatch):
    """An explicit choice must not force UNAVAILABLE while a usable key sits in the environment."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.setenv("AI_ANALYST_PROVIDER", "gemini")
    assert resolve_provider() == PROVIDER_XAI


def test_default_client_selects_the_matching_transport(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    assert isinstance(default_client(), GrokClient)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    assert isinstance(default_client(), GeminiClient)
    assert isinstance(default_client(provider=PROVIDER_XAI), GrokClient)


def test_schema_conversion_matches_the_gemini_contract():
    """The two providers must be constrained to the same contract, only the type spelling differs."""
    converted = gemini_schema_to_json_schema(BRIEF_SCHEMA)
    assert converted["type"] == "object"
    assert converted["properties"]["summary"]["type"] == "string"
    items = converted["properties"]["field_assessments"]["items"]
    assert items["type"] == "object"
    assert sorted(items["properties"]) == ["field", "reason", "verdict"]
    # Field names, required list and enum values are carried across unchanged.
    assert set(converted["properties"]) == set(BRIEF_SCHEMA["properties"])
    assert converted["required"] == BRIEF_SCHEMA["required"]
    assert converted["properties"]["confidence"]["enum"] == list(CONFIDENCE_LEVELS)
    assert items["properties"]["verdict"]["enum"] == list(
        BRIEF_SCHEMA["properties"]["field_assessments"]["items"]["properties"]["verdict"]["enum"]
    )


def test_schema_conversion_emits_only_lower_case_types():
    encoded = json.dumps(gemini_schema_to_json_schema(BRIEF_SCHEMA))
    for upper in ("OBJECT", "ARRAY", "STRING", "NUMBER", "INTEGER", "BOOLEAN"):
        assert upper not in encoded


def test_grok_client_sends_a_bearer_token_and_a_json_schema(stub_server):
    """The request must carry the key as a bearer token and constrain the model to the schema."""
    stub_server.RequestHandlerClass.payload = {
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(_passing_payload([]))}}
        ]
    }
    client = GrokClient(
        api_key="test-xai-key",
        model="grok-3-mini",
        endpoint=f"http://127.0.0.1:{stub_server.server_port}",
    )
    assert client.available is True
    payload = client.analyse("system", "dossier")
    assert payload["confidence"] == "medium"

    sent = stub_server.RequestHandlerClass.last_body
    assert sent["model"] == "grok-3-mini"
    assert sent["temperature"] == 0
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][1]["content"] == "dossier"
    fmt = sent["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["type"] == "object"
    headers = stub_server.RequestHandlerClass.last_headers
    assert headers.get("Authorization") == "Bearer test-xai-key"


def test_grok_client_rejects_a_truncated_response(stub_server):
    stub_server.RequestHandlerClass.payload = {
        "choices": [{"finish_reason": "length", "message": {"content": "{}"}}]
    }
    client = GrokClient(
        api_key="k", model="grok-3-mini", endpoint=f"http://127.0.0.1:{stub_server.server_port}"
    )
    with pytest.raises(AnalystUnavailable) as excinfo:
        client.analyse("system", "dossier")
    assert "length" in str(excinfo.value)


def test_grok_client_rejects_an_empty_choice_list(stub_server):
    stub_server.RequestHandlerClass.payload = {"choices": []}
    client = GrokClient(
        api_key="k", model="grok-3-mini", endpoint=f"http://127.0.0.1:{stub_server.server_port}"
    )
    with pytest.raises(AnalystUnavailable):
        client.analyse("system", "dossier")


def test_grok_client_condenses_an_http_error(stub_server):
    """The Grok path condenses an HTTP failure the same way the Gemini path does."""
    stub_server.RequestHandlerClass.payload = {
        "error": {
            "code": 401,
            "message": (
                "Incorrect API key provided. See https://console.x.ai for details."
            ),
        }
    }
    stub_server.RequestHandlerClass.status = 401
    client = GrokClient(
        api_key="bad", model="grok-3-mini", endpoint=f"http://127.0.0.1:{stub_server.server_port}"
    )
    with pytest.raises(AnalystUnavailable) as excinfo:
        client.analyse("system", "dossier")
    message = str(excinfo.value)
    assert "Grok" in message
    assert "401" in message
    assert "https://" not in message
    assert "{" not in message


def test_grok_client_is_unavailable_without_a_key():
    assert GrokClient(api_key="").available is False
    assert GrokClient(api_key="   ").available is False


def test_a_grok_brief_goes_through_the_same_verification(analyst_db):
    """A Grok payload is validated exactly like a Gemini payload: the transport is not trusted."""
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    payload = _passing_payload(facts)
    payload["summary"] = "The project is worth $9,999,999 and is out for bid."
    client = FakeAnalystClient(payload, model="grok-3-mini")
    brief = build_brief(analyst_db, pid, client=client)
    assert brief.status == STATUS_UNVERIFIED
    assert brief.is_verified is False
    assert brief.model == "grok-3-mini"
    assert brief.narrative == deterministic_narrative(brief)


def test_a_grounded_grok_payload_is_published(analyst_db):
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    client = FakeAnalystClient(_passing_payload(facts), model="grok-3-mini")
    brief = build_brief(analyst_db, pid, client=client)
    assert brief.status == STATUS_AVAILABLE
    assert brief.is_verified is True
    assert brief.model == "grok-3-mini"


def test_build_brief_with_no_key_names_both_options(analyst_db, monkeypatch):
    """The unavailable message tells the operator both keys that would enable the narrative."""
    _clear_provider_env(monkeypatch)
    brief = build_brief(analyst_db, _project_id(analyst_db))
    assert brief.status == STATUS_UNAVAILABLE
    assert any("GEMINI_API_KEY" in l and "XAI_API_KEY" in l for l in brief.limitations)


def test_build_brief_uses_the_grok_provider_when_only_xai_is_configured(analyst_db, monkeypatch):
    """With only an xAI key set, the default client is Grok and its model id is recorded."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.setattr(GrokClient, "analyse", lambda self, system, dossier: _passing_payload(
        collect_facts(analyst_db, _project_id(analyst_db))
    ))
    brief = build_brief(analyst_db, _project_id(analyst_db))
    assert brief.status == STATUS_AVAILABLE
    assert brief.model == "grok-3-mini"


# --- response cache --------------------------------------------------------------
#
# The cache stores the model's raw response, addressed by a digest of exactly what the model was
# shown. These tests pin the two properties that make that safe: a hit saves the call, and a
# changed fact is a miss. A third pins the honesty rule at the cache boundary - a response that
# fails verification must fail it identically when it comes back from storage.

class _CountingClient:
    """A transport that records how many times it was asked, and returns a fixed payload."""

    provider = PROVIDER_GEMINI

    def __init__(self, payload):
        self._payload = payload
        self.model = "counting-model"
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    def analyse(self, system: str, dossier: str):
        self.calls += 1
        return dict(self._payload)


def _caching(analyst_db, payload):
    """A CachingClient over a counting transport, plus the transport for assertions."""
    inner = _CountingClient(payload)
    return CachingClient(inner, analyst_db), inner


def _cache_count(db) -> int:
    return db.conn.execute("SELECT COUNT(*) FROM analyst_response_cache").fetchone()[0]


def test_cache_hit_skips_the_second_call(analyst_db):
    """The same dossier is analysed once; the second request is served from storage."""
    pid = _project_id(analyst_db)
    payload = _passing_payload(collect_facts(analyst_db, pid))
    client, inner = _caching(analyst_db, payload)

    first = build_brief(analyst_db, pid, client=client)
    second = build_brief(analyst_db, pid, client=client)

    assert inner.calls == 1, "the model must be asked exactly once for one dossier"
    assert first.status == STATUS_AVAILABLE
    assert second.status == STATUS_AVAILABLE
    assert first.narrative == second.narrative


def test_cache_key_changes_when_the_dossier_changes(analyst_db):
    """A changed fact produces a different address, so a stale entry cannot be reached.

    This is the whole invalidation story: the key is a digest of the dossier, and the dossier is
    built from the stored facts, so evidence is the invalidator. No TTL to tune, no hook to
    forget.
    """
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    dossier = assemble_dossier(pid, facts, market="DFW", trade="HVAC").text

    same = cache_key(PROVIDER_GEMINI, "m", "1", "sys", dossier)
    assert same == cache_key(PROVIDER_GEMINI, "m", "1", "sys", dossier)
    assert same != cache_key(PROVIDER_GEMINI, "m", "1", "sys", dossier + " ")
    assert same != cache_key(PROVIDER_GEMINI, "m", "2", "sys", dossier)
    assert same != cache_key(PROVIDER_GEMINI, "other-model", "1", "sys", dossier)
    # A different prompt is a different response, so it must not share an address.
    assert same != cache_key(PROVIDER_GEMINI, "m", "1", "other system", dossier)


def test_cache_key_does_not_collide_on_field_boundaries():
    """The separator matters: ("ab", "c") must not address the same row as ("a", "bc")."""
    assert cache_key("p", "m", "1", "ab", "c") != cache_key("p", "m", "1", "a", "bc")


def test_cache_key_changes_when_the_response_schema_changes():
    """The response schema is part of the prompt contract, so it is part of the address.

    The schema is not sent as prose - it is a separate field of the request - so a key built only
    from (system, dossier) would let a schema edit reuse a payload produced under the old shape.
    The fix is structural rather than a convention: nobody has to remember to bump
    PROMPT_VERSION when they add a field, because the digest moves on its own.
    """
    from oppintel.ai_analyst import prompt_contract_digest

    base = prompt_contract_digest("sys")
    assert base == prompt_contract_digest("sys", BRIEF_SCHEMA), "default must be the real schema"

    widened = copy.deepcopy(BRIEF_SCHEMA)
    widened["properties"]["a_new_field"] = {"type": "STRING"}
    assert prompt_contract_digest("sys", widened) != base, "a schema edit must move the digest"


def test_a_schema_edit_makes_a_stored_payload_unreachable(analyst_db, monkeypatch):
    """A row written under one schema must not answer a request built from another.

    This is the failure the digest prevents: the schema is not part of the dossier, so without it
    in the key a shape change would be served the old payload as though it were current. The
    schema global is swapped for a widened copy - a different *input constant*, not a stub of the
    code under test - so the assertion is about real ``cache_key`` behaviour.
    """
    import oppintel.ai_analyst as ai

    dossier = "dossier text"
    key_old = cache_key("gemini", "m", "1", "sys", dossier)
    analyst_db.store_analyst_payload(
        key_old,
        {"summary": "written under the old schema"},
        provider="gemini",
        model="m",
        prompt_version="1",
    )
    assert analyst_db.cached_analyst_payload(key_old) is not None

    widened = copy.deepcopy(BRIEF_SCHEMA)
    widened["properties"]["a_new_field"] = {"type": "STRING"}
    monkeypatch.setattr(ai, "BRIEF_SCHEMA", widened)

    key_new = cache_key("gemini", "m", "1", "sys", dossier)
    assert key_new != key_old, "a changed schema must not address the old payload"
    assert analyst_db.cached_analyst_payload(key_new) is None, "the new contract is a miss"


def test_generated_at_is_stamped_on_every_brief(analyst_db):
    """The brief always carries an assembly timestamp, even when the prose came from cache.

    It means "when this page was built", not "when the model ran" - a cached payload is older
    than the timestamp that reports it. Pinned so the field is not later read as evidence.
    """
    pid = _project_id(analyst_db)
    payload = _passing_payload(collect_facts(analyst_db, pid))
    counter = _CountingClient(payload)
    first = build_brief(analyst_db, pid, client=counter)
    second = build_brief(analyst_db, pid, client=counter)
    assert first.generated_at
    assert second.generated_at
    assert first.generated_at <= second.generated_at


def test_a_changed_value_invalidates_the_cached_response(analyst_db):
    """A corrected value changes the dossier, so the next analysis is a miss, not a stale hit."""
    pid = _project_id(analyst_db)
    payload = _passing_payload(collect_facts(analyst_db, pid))
    client, inner = _caching(analyst_db, payload)

    build_brief(analyst_db, pid, client=client)
    assert inner.calls == 1

    # The record's declared value is corrected at the source. The dossier quotes it, so the
    # address changes and the old entry can no longer be reached.
    analyst_db.conn.execute(
        "UPDATE project SET estimated_project_value = estimated_project_value + 111 WHERE id = ?",
        (pid,),
    )
    analyst_db.commit()

    build_brief(analyst_db, pid, client=client)
    assert inner.calls == 2, "a changed dossier must not be served from the old entry"


def test_a_cached_response_is_still_verified_on_read(analyst_db):
    """A cached payload goes through the same verification, so the cache cannot launder prose.

    The stored bytes are the provider's raw response, never a finished brief. A response that
    violates the grounding rules is demoted exactly as it would be on the first pass, whether it
    arrives from the network or from storage.
    """
    pid = _project_id(analyst_db)
    facts = collect_facts(analyst_db, pid)
    bad = _passing_payload(facts)
    bad["summary"] = "The work is out for bid at 9999999 dollars."

    client, inner = _caching(analyst_db, bad)

    first = build_brief(analyst_db, pid, client=client)
    second = build_brief(analyst_db, pid, client=client)

    assert inner.calls == 1, "the second read must come from the cache"
    for brief in (first, second):
        assert brief.status == STATUS_UNVERIFIED
        assert brief.verification_issues
        assert "9999999" not in brief.narrative
        assert "out for bid" not in brief.narrative.lower()


def test_cache_stores_the_payload_not_a_brief(analyst_db):
    """What lands in the table is the provider's response, not a rendered brief.

    A stored brief would freeze the verification result; storing the raw payload keeps every
    derived field - facts, verdicts, status - on the live code path.
    """
    pid = _project_id(analyst_db)
    payload = _passing_payload(collect_facts(analyst_db, pid))
    client, _ = _caching(analyst_db, payload)
    build_brief(analyst_db, pid, client=client)

    row = analyst_db.conn.execute("SELECT payload FROM analyst_response_cache").fetchone()
    assert row is not None
    stored = json.loads(row["payload"])
    assert "summary" in stored, "the raw provider response is what is cached"
    assert "verification_issues" not in stored, "a finished brief must never be cached"


def test_cache_is_not_used_for_a_caller_supplied_client(analyst_db):
    """An explicitly-passed client is never wrapped, so a test double behaves as it always did."""
    pid = _project_id(analyst_db)
    client = _CountingClient(_passing_payload(collect_facts(analyst_db, pid)))

    build_brief(analyst_db, pid, client=client)
    build_brief(analyst_db, pid, client=client)

    assert client.calls == 2, "a passed-in client is not cached"
    count = analyst_db.conn.execute(
        "SELECT COUNT(*) FROM analyst_response_cache"
    ).fetchone()[0]
    assert count == 0


def test_a_corrupt_cache_row_costs_a_call_not_an_error(analyst_db):
    """A payload that no longer parses is treated as absent, so a bad row cannot break a page."""
    pid = _project_id(analyst_db)
    payload = _passing_payload(collect_facts(analyst_db, pid))
    client, inner = _caching(analyst_db, payload)

    build_brief(analyst_db, pid, client=client)
    analyst_db.conn.execute("UPDATE analyst_response_cache SET payload = 'not json'")
    analyst_db.commit()

    brief = build_brief(analyst_db, pid, client=client)
    assert inner.calls == 2, "a corrupt row must fall through to a fresh call"
    assert brief.status == STATUS_AVAILABLE


def test_the_cache_is_bounded_and_prunes_the_oldest(analyst_db):
    """The cache must not grow without limit, and pruning must drop the oldest entries.

    Evidence-keyed caching means a corrected value writes a new row and orphans the old one, so
    the table would otherwise gain a row per distinct dossier forever. Pruning is safe because the
    key is content-addressed: a dropped entry costs a call, it cannot serve a stale answer.
    """
    from oppintel.db import ANALYST_CACHE_MAX_ROWS

    for i in range(8):
        analyst_db.store_analyst_payload(
            f"key-{i}",
            {"summary": f"payload {i}"},
            provider="gemini",
            model="m",
            prompt_version="1",
        )
    analyst_db.commit()

    # Below the hysteresis threshold nothing is dropped, so the write path stays cheap.
    assert analyst_db.prune_analyst_cache(max_rows=10) == 0
    assert _cache_count(analyst_db) == 8

    # Past it, the table is trimmed to the cap and the newest rows survive.
    removed = analyst_db.prune_analyst_cache(max_rows=5)
    assert removed == 3
    assert _cache_count(analyst_db) == 5
    assert analyst_db.cached_analyst_payload("key-7") is not None, "newest kept"
    assert analyst_db.cached_analyst_payload("key-0") is None, "oldest dropped"
    assert ANALYST_CACHE_MAX_ROWS >= 100, "the production cap must be a real bound"


def test_a_cached_row_records_its_project(analyst_db, monkeypatch):
    """The row carries the project id, so an operator can see which record a payload belongs to.

    This exercises the production path - no client is passed, so ``build_brief`` builds the
    transport and wraps it. The client *factory* is patched to hand back the counting transport,
    which is the network boundary rather than the code under test.
    """
    import oppintel.ai_analyst as ai

    pid = _project_id(analyst_db)
    inner = _CountingClient(_passing_payload(collect_facts(analyst_db, pid)))
    monkeypatch.setattr(ai, "default_client", lambda **kwargs: inner)

    build_brief(analyst_db, pid)

    row = analyst_db.conn.execute(
        "SELECT project_id, provider, model, prompt_version FROM analyst_response_cache"
    ).fetchone()
    assert row["project_id"] == pid
    assert row["provider"] == PROVIDER_GEMINI
    assert row["model"], "the model that produced the payload is recorded"

