"""AI analyst: a grounded narrative over the evidence BuildScope already holds.

The platform's rule is that nothing is invented. An LLM is the one component that can break that
rule at scale, so this module inverts the usual flow: the model is never the source of a fact,
only of prose about facts that already exist and are already sourced.

Four mechanisms make that structural rather than aspirational:

1. **Evidence-first retrieval** (:func:`collect_facts`). A ``Fact`` is emitted for a field only
   when a stored evidence row supports the value (``reporting.field_verdict``). A field with no
   support is emitted as an explicit *unknown* — the model is told the record does not say it,
   rather than being left to fill the gap.
2. **A dossier, not a prompt trick** (:func:`assemble_dossier`). The facts are serialised into a
   numbered dossier, and the model is asked to write about the dossier. It is never asked to
   recall anything, so it has nothing to hallucinate from.
3. **Structured output** (:data:`BRIEF_SCHEMA`). Gemini is constrained to a JSON schema, so the
   response is a fixed set of fields and a hallucinated extra section cannot exist.
4. **Verification before publication** (:func:`validate_brief`). The model's free text is
   re-checked against the fact list: a number that is not a stored figure, a date that is not a
   stored date, a restated not-verified marker, bid language the permit-evidence notice forbids,
   or a field verdict the model re-graded, are all violations. A violation demotes the brief to
   ``UNVERIFIED`` and replaces the narrative with a deterministic, evidence-only summary. The
   model's reading is then shown only as an explicitly labelled proposal.

With no ``GEMINI_API_KEY``, or on any transport or decoding error, the analyst returns an
``UNAVAILABLE`` brief built entirely from the evidence. It never blocks a page and never pretends
an analysis happened.

The transport is a :class:`Protocol`, so the evidence pipeline and the verification pass are
tested against a real database with no network at all.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol

from .constants import NOT_VERIFIED as NOT_VERIFIED_SENTENCE
from .db import Database
from .procurement import procurement_explanation, procurement_status
from .report_generator import BRIEF_FIELDS, PERMIT_EVIDENCE_NOTICE
from .reporting import (
    CONFIRMED,
    NOT_VERIFIED as VERDICT_NOT_VERIFIED,
    PARTIALLY_VERIFIED,
    field_verdict,
)

#: The prompt/schema contract version. A stored or quoted analysis carries it, so a brief built
#: under an older contract is recognisable rather than silently compared with a newer one.
PROMPT_VERSION = "1"

#: Default model. Flash is fast and cheap and, with a constrained schema and temperature 0,
#: accurate enough for prose over a dossier the caller has already assembled.
DEFAULT_MODEL = "gemini-2.5-flash"

#: The public Gemini endpoint. Overridable so a test can point at a local stub.
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

#: Brief statuses.
STATUS_AVAILABLE = "AVAILABLE"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_UNAVAILABLE = "UNAVAILABLE"

#: Confidence values the model is allowed to return. Anything else is a validation failure.
CONFIDENCE_LEVELS = ("high", "medium", "low")

#: Fact kinds.
KIND_VERIFIED = "verified"          # a value a source states
KIND_UNVERIFIED = "unverified"      # a field the record does not state
KIND_DERIVED = "derived"            # an observation computed from stored rows (a count, a diff)

#: Phrases that assert a bid is available. The permit-evidence notice exists precisely to stop
#: this claim, so its presence in generated prose is a violation, not a stylistic choice.
FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "open for bid",
    "open for bids",
    "out for bid",
    "accepting bids",
    "accepting bid",
    "accepting proposals",
    "invites bids",
    "inviting bids",
    "bid is open",
    "bids are open",
    "request for proposal",
    "request for proposals",
    "solicitation",
    "available for bid",
    "ready to bid",
)

#: Free-text fields of the brief that are checked for unsupported figures and dates.
_FREETEXT_KEYS = ("summary", "why_it_matters")


class AnalystError(RuntimeError):
    """Raised when an analysis cannot be produced. Callers degrade rather than fail."""


class AnalystUnavailable(AnalystError):
    """Raised when no model is configured or the model could not be reached."""


# --- transport -----------------------------------------------------------------

class AnalystClient(Protocol):
    """The one method the analyst needs from a model provider."""

    @property
    def available(self) -> bool:
        """Whether a model is configured and could be called."""

    def analyse(self, system: str, dossier: str) -> dict[str, Any]:
        """Return the structured brief payload for a dossier."""


def api_key_from_env() -> str:
    """The Gemini key, read from the environment under either documented name.

    ``GEMINI_API_KEY`` is the name ``.env.example`` documents; the sandbox secret is exposed as
    ``Gemini_API_Token``. Reading both means the analyst works in either environment without a
    key ever being written into the repository.
    """
    return (
        (os.environ.get("GEMINI_API_KEY") or "").strip()
        or (os.environ.get("Gemini_API_Token") or "").strip()
    )


def model_from_env() -> str:
    return (os.environ.get("GEMINI_MODEL") or "").strip() or DEFAULT_MODEL


class GeminiClient:
    """A minimal, dependency-free Gemini ``generateContent`` client.

    Deliberately not the ``google-genai`` SDK: the repository ships four dependencies and the
    request here is a single JSON POST. Keeping it to the standard library means the analyst adds
    no install step to a deployment, which is the same reason the rest of the platform is
    hand-rolled.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str = GEMINI_ENDPOINT,
        timeout: int = 45,
    ):
        self.api_key = (api_key if api_key is not None else api_key_from_env()).strip()
        self.model = (model or model_from_env()).strip()
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.model)

    def analyse(self, system: str, dossier: str) -> dict[str, Any]:
        if not self.available:
            raise AnalystUnavailable(
                "No Gemini model is configured (set GEMINI_API_KEY)."
            )
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": dossier}]}],
            "generationConfig": {
                # Temperature 0: the same dossier should yield the same prose, so a brief is
                # reproducible and a diff between two runs is meaningful.
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": BRIEF_SCHEMA,
            },
        }
        request = urllib.request.Request(
            f"{self.endpoint}/{self.model}:generateContent?key={self.api_key}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AnalystUnavailable(_http_error_message(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AnalystUnavailable(f"Gemini could not be reached: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise AnalystUnavailable(f"Gemini returned malformed JSON: {exc}") from exc

        return _extract_payload(payload)


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """A short, operator-readable reason for an HTTP failure.

    The raw Gemini body is a JSON document that repeats the status, embeds a documentation URL and
    can run to hundreds of characters. It is reduced to the status and the error's first sentence,
    so a limitation reads as a sentence and no URL leaks into a brief.
    """
    code = getattr(exc, "code", "?")
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
        message = str((body.get("error") or {}).get("message") or "").strip()
    except Exception:  # pragma: no cover - a non-JSON error body
        message = ""
    # Keep the first sentence only; the remainder is usually a link or a billing aside.
    message = re.split(r"\.\s|https?://", message, maxsplit=1)[0].strip().rstrip(".")
    if message:
        return f"Gemini returned HTTP {code}: {message}."
    return f"Gemini returned HTTP {code}."


def _extract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the structured brief out of a ``generateContent`` response.

    A blocked or truncated response is an error, not an empty brief: an empty brief would read as
    "the model found nothing", which is a different and false statement.
    """
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason")
        raise AnalystUnavailable(
            f"Gemini returned no candidate{f' ({reason})' if reason else ''}."
        )
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish not in (None, "STOP"):
        raise AnalystUnavailable(f"Gemini stopped early ({finish}).")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts)
    if not text.strip():
        raise AnalystUnavailable("Gemini returned an empty response.")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalystUnavailable(f"Gemini response was not the expected JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise AnalystUnavailable("Gemini response was not a JSON object.")
    return decoded


#: The schema the model is constrained to. Every field is required, so a response that omits the
#: limitations section cannot be produced and pass as complete.
BRIEF_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {
            "type": "STRING",
            "description": "Two sentences on what the record supports, citing only dossier facts.",
        },
        "why_it_matters": {
            "type": "STRING",
            "description": "One or two sentences on relevance to the configured trade. No bid claim.",
        },
        "field_assessments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "field": {
                        "type": "STRING",
                        "enum": [name for name, _label in BRIEF_FIELDS],
                        "description": "The field id from the dossier, e.g. project_name.",
                    },
                    "verdict": {"type": "STRING", "enum": list((CONFIRMED, PARTIALLY_VERIFIED, VERDICT_NOT_VERIFIED))},
                    "reason": {"type": "STRING"},
                },
                "required": ["field", "verdict", "reason"],
            },
        },
        "questions_to_resolve": {"type": "ARRAY", "items": {"type": "STRING"}},
        "confidence": {"type": "STRING", "enum": list(CONFIDENCE_LEVELS)},
        "limitations": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "summary",
        "why_it_matters",
        "field_assessments",
        "questions_to_resolve",
        "confidence",
        "limitations",
    ],
}


# --- facts ---------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    """One grounded statement about a project.

    ``display`` is the exact string the model may quote. ``numeric`` and ``value`` exist so the
    validator can rebuild the set of figures a brief is allowed to contain.
    """

    fact_id: str
    label: str
    value: Any
    display: str
    verdict: str
    kind: str = KIND_VERIFIED
    field_name: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    numeric: float | None = None


@dataclass
class Dossier:
    """The numbered evidence the model writes about, plus the facts behind it."""

    project_id: int
    facts: list[Fact]
    text: str

    @property
    def supported(self) -> list[Fact]:
        return [f for f in self.facts if f.kind in (KIND_VERIFIED, KIND_DERIVED)]

    @property
    def unsupported(self) -> list[Fact]:
        return [f for f in self.facts if f.kind == KIND_UNVERIFIED]


def _display(field_name: str, value: Any) -> str:
    """Render a field the way the rest of the platform renders it.

    Reimplemented rather than imported from ``report_generator`` (whose helper is private) but
    deliberately identical in output, so a brief and a report describe a value the same way.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return NOT_VERIFIED_SENTENCE.rstrip(".")
    if field_name in ("estimated_project_value", "square_footage"):
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)
    if field_name == "permit_date":
        return str(value)[:10]
    return str(value)


def collect_facts(db: Database, project_id: int) -> list[Fact]:
    """Every grounded fact about a project, and every field the record does not state.

    Reads only through ``Database`` helpers and ``reporting.field_verdict``, so a fact exists
    here exactly when a stored evidence row supports it. Nothing is inferred: a missing field
    becomes an ``unverified`` fact naming the field, never a value.
    """
    row = db.conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise AnalystError(f"No project with id {project_id}.")
    row = dict(row)

    facts: list[Fact] = []

    # 1. The client-brief fields, each carrying its own support verdict.
    for field_name, label in BRIEF_FIELDS:
        value = row.get(field_name)
        verdict, evidence = field_verdict(db, project_id, field_name, value)
        primary = evidence[0] if evidence else {}
        supported = verdict != VERDICT_NOT_VERIFIED
        facts.append(
            Fact(
                fact_id=field_name,
                label=label,
                value=value if supported else None,
                display=_display(field_name, value) if supported else "",
                verdict=verdict,
                kind=KIND_VERIFIED if supported else KIND_UNVERIFIED,
                field_name=field_name,
                source_name=primary.get("source_name"),
                source_url=primary.get("source_url"),
                numeric=_as_number(value) if supported else None,
            )
        )

    # 2. Contributing permits. Each is a directly observed record, so each is supported.
    permits = db.permits_for_project(project_id)
    for permit in permits:
        number = permit.get("permit_number") or permit.get("natural_key") or "unknown"
        pieces = [
            f"permit {number}",
            permit.get("permit_type") or "type not stated",
            f"filed {str(permit.get('permit_date'))[:10]}" if permit.get("permit_date") else "filing date not stated",
            permit.get("status") or "status not stated",
        ]
        value = permit.get("job_value")
        if value is not None:
            pieces.append(f"declared value {_display('estimated_project_value', value)}")
        facts.append(
            Fact(
                fact_id=f"permit:{number}",
                label="Contributing permit",
                value=permit.get("permit_number"),
                display=" · ".join(pieces),
                verdict=CONFIRMED,
                kind=KIND_VERIFIED,
                field_name="permit_number",
                source_name=permit.get("source_display_name"),
                source_url=permit.get("source_url"),
                numeric=_as_number(value),
            )
        )

    # 3. Source records behind the project, each with a resolvable URL where one exists.
    for source in db_sources(db, project_id):
        record_key = source.get("source_record_key") or "no record key"
        facts.append(
            Fact(
                fact_id=f"source:{record_key}",
                label="Source record",
                value=source.get("source_name"),
                display=(
                    f"{source.get('source_name')} · record {record_key} · "
                    f"{source.get('fields_supported')} field(s) supported"
                ),
                verdict=CONFIRMED,
                kind=KIND_VERIFIED,
                source_name=source.get("source_name"),
                source_url=source.get("source_url"),
            )
        )

    # 4. Recorded changes. An observation of movement, not of new work.
    changes = db.changes_for_project(project_id, limit=20)
    if changes:
        for change in changes:
            facts.append(
                Fact(
                    fact_id=f"change:{change['id']}",
                    label="Recorded change",
                    value=change.get("current_value"),
                    display=(
                        f"{change.get('field_name')}: {change.get('previous_value')} "
                        f"→ {change.get('current_value')}"
                    ),
                    verdict=CONFIRMED,
                    kind=KIND_DERIVED,
                )
            )
    else:
        facts.append(
            Fact(
                fact_id="changes",
                label="Recorded changes",
                value=None,
                display="No change has been recorded for this project.",
                verdict=CONFIRMED,
                kind=KIND_DERIVED,
            )
        )

    # 5. Resolved parties, which the record observed in a role.
    for entity in db.entities_for_project(project_id):
        role = (entity.get("role") or "party").replace("_", " ")
        facts.append(
            Fact(
                fact_id=f"entity:{entity['id']}",
                label=f"{role.title()} on record",
                value=entity.get("display_name"),
                display=f"{entity.get('display_name')} ({role})",
                verdict=CONFIRMED,
                kind=KIND_VERIFIED,
                field_name=role,
                source_url=entity.get("source_url"),
            )
        )

    # 6. Procurement status, explained in the platform's own fixed wording. The status is only as
    #    supported as the recorded permit status it derives from.
    status = procurement_status(_ProjectView(row))
    status_verdict, _ = field_verdict(db, project_id, "project_status", row.get("project_status"))
    facts.append(
        Fact(
            fact_id="procurement",
            label="Procurement",
            value=status,
            display=f"{status} — {procurement_explanation(status, _ProjectView(row))}",
            verdict=status_verdict if status_verdict != VERDICT_NOT_VERIFIED else PARTIALLY_VERIFIED,
            kind=KIND_DERIVED,
        )
    )

    # 7. Classification and its recorded reasons, as stored.
    reasons = _json_list(row.get("classification_reasons"))
    facts.append(
        Fact(
            fact_id="classification",
            label="Classification",
            value=row.get("classification"),
            display=(
                f"{row.get('classification') or 'not classified'} "
                f"(score {row.get('classification_score')}, "
                f"mechanical evidence tier {row.get('mechanical_evidence_tier')})"
            ),
            verdict=CONFIRMED,
            kind=KIND_DERIVED,
            numeric=_as_number(row.get("classification_score")),
        )
    )
    for index, reason in enumerate(reasons, start=1):
        facts.append(
            Fact(
                fact_id=f"classification-reason:{index}",
                label="Classification reason",
                value=reason,
                display=reason,
                verdict=CONFIRMED,
                kind=KIND_DERIVED,
            )
        )

    # 8. Preserved contradictions, never resolved by the analyst.
    discrepancies = _json_list(row.get("discrepancies"))
    if discrepancies:
        for index, item in enumerate(discrepancies, start=1):
            values = " vs ".join(
                f"{v.get('value')} ({v.get('source_name')})"
                for v in (item.get("values") or [])
            )
            facts.append(
                Fact(
                    fact_id=f"discrepancy:{index}",
                    label="Source disagreement",
                    value=item.get("field_name"),
                    display=f"{item.get('field_name')}: {values}",
                    verdict=CONFIRMED,
                    kind=KIND_DERIVED,
                )
            )
    else:
        facts.append(
            Fact(
                fact_id="discrepancies",
                label="Source disagreements",
                value=None,
                display="No cross-source disagreement was detected.",
                verdict=CONFIRMED,
                kind=KIND_DERIVED,
            )
        )

    # 9. How much observation history the archive holds. A derived count, stated as such.
    observations = db.evidence_history_count(project_id)
    facts.append(
        Fact(
            fact_id="observations",
            label="Archived observations",
            value=observations,
            display=f"{observations} archived evidence observations",
            verdict=CONFIRMED,
            kind=KIND_DERIVED,
            numeric=float(observations),
        )
    )

    return facts


def db_sources(db: Database, project_id: int) -> list[dict[str, Any]]:
    """Distinct source records behind a project. A thin wrapper so `collect_facts` stays linear."""
    rows = db.conn.execute(
        """
        SELECT source_name, source_url, source_record_key,
               COUNT(DISTINCT field_name) AS fields_supported
          FROM evidence
         WHERE project_id = ?
         GROUP BY source_name, source_url, source_record_key
         ORDER BY source_name, source_record_key
        """,
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


class _ProjectView:
    """Adapts a project row to the attribute surface `procurement.py` expects.

    The same shim the report layer uses, kept local so the analyst depends on the stored row and
    not on a second query.
    """

    def __init__(self, row: dict[str, Any]):
        self._row = row

    def __getattr__(self, name: str) -> Any:
        return self._row.get(name)


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


# --- dossier -------------------------------------------------------------------

def assemble_dossier(project_id: int, facts: list[Fact], *, market: str, trade: str) -> Dossier:
    """Serialise the facts into the numbered text the model writes about.

    The unsupported fields are grouped at the end and labelled "the record does not state", so the
    model is told what is absent. Leaving them out would invite the model to assume, which is the
    failure this whole module exists to prevent.
    """
    supported = [f for f in facts if f.kind in (KIND_VERIFIED, KIND_DERIVED)]
    unsupported = [f for f in facts if f.kind == KIND_UNVERIFIED]

    lines: list[str] = []
    lines.append("DOSSIER (the only facts you may use)")
    lines.append(f"market: {market} | trade: {trade} | prompt_version: {PROMPT_VERSION}")
    lines.append("")
    lines.append("SUPPORTED FACTS")
    for index, fact in enumerate(supported, start=1):
        origin = fact.verdict
        if fact.source_name:
            origin += f", source: {fact.source_name}"
        lines.append(f"[{index}] {fact.label}: {fact.display}  ({origin})")

    lines.append("")
    lines.append("NOT STATED BY ANY SOURCE (say 'not stated', never guess a value)")
    if unsupported:
        for fact in unsupported:
            lines.append(f"- {fact.field_name}: not stated in the record")
    else:
        lines.append("- (every presented field has a source)")

    return Dossier(project_id=project_id, facts=facts, text="\n".join(lines))


SYSTEM_INSTRUCTION = f"""You are the BuildScope analyst. You write about public permit evidence for a
commercial mechanical (HVAC) contractor.

Absolute rules, in priority order:
1. Use only the facts in the DOSSIER. Never add a fact, a figure, a name, a date or an address that
   is not in the dossier. Never estimate, extrapolate or round to a value that is not written there.
2. A field listed as NOT STATED is unknown. Write "not stated" for it — for example "the project
   status is not stated". Never write "Not verified", "not verified", "unverified" or any variant
   of them; that phrase is the platform's display marker and must not appear in your prose, in a
   limitation, or anywhere else. Absence is stated, never labelled.
3. A permit is evidence that work was filed. It is NOT evidence that a bid is available. Never say
   or imply that work is open for bid, out for bid, accepting bids or proposals, or available.
   {PERMIT_EVIDENCE_NOTICE}
4. Do not re-grade a field. The verdict shown in the dossier for a field is the verdict you must
   report for that field in field_assessments.
5. In field_assessments, `field` must be the field id used in the dossier (for example
   project_name, estimated_project_value), never the display label.
6. Keep every number you write identical to a number in the dossier.
7. Your interpretation is a reading, not a fact. State the comparison it rests on.

Write plainly and briefly. Prefer what the record shows over adjectives."""


# --- brief ---------------------------------------------------------------------

@dataclass
class FieldAssessment:
    field: str
    verdict: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "verdict": self.verdict, "reason": self.reason}


@dataclass
class AnalystBrief:
    """A project analysis, its grounding, and whether it passed verification."""

    project_id: int
    slug: str | None
    status: str
    model: str | None
    generated_at: str
    prompt_version: str = PROMPT_VERSION
    narrative: str = ""
    why_it_matters: str = ""
    field_assessments: list[FieldAssessment] = field(default_factory=list)
    questions_to_resolve: list[str] = field(default_factory=list)
    confidence: str | None = None
    limitations: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    verification_issues: list[str] = field(default_factory=list)
    caveat: str = PERMIT_EVIDENCE_NOTICE

    @property
    def is_verified(self) -> bool:
        """True only when the model's prose passed every grounding check."""
        return self.status == STATUS_AVAILABLE and not self.verification_issues

    @property
    def unsupported_fields(self) -> list[str]:
        return [f.label for f in self.facts if f.kind == KIND_UNVERIFIED]

    @property
    def supported_count(self) -> int:
        return sum(1 for f in self.facts if f.kind in (KIND_VERIFIED, KIND_DERIVED))

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form. A missing value stays null; a status carries its own meaning."""
        return {
            "project_id": self.project_id,
            "slug": self.slug,
            "status": self.status,
            "verified": self.is_verified,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "generated_at": self.generated_at,
            "summary": self.narrative,
            "why_it_matters": self.why_it_matters,
            "field_assessments": [a.to_dict() for a in self.field_assessments],
            "questions_to_resolve": list(self.questions_to_resolve),
            "confidence": self.confidence,
            "limitations": list(self.limitations),
            "unsupported_fields": self.unsupported_fields,
            "supported_fact_count": self.supported_count,
            "verification_issues": list(self.verification_issues),
            "caveat": self.caveat,
        }


# --- verification ---------------------------------------------------------------

def _supported_numbers(facts: list[Fact]) -> set[str]:
    """Every number a brief is allowed to contain, from the facts themselves.

    A figure is supported when it literally appears in the dossier, or is a derived aggregate of
    stored values (the count of facts, the count of permits, the sum of stored values). This makes
    the check "did the model stay inside the evidence" rather than "is this number plausible".
    """
    allowed: set[str] = set()
    for fact in facts:
        if fact.kind == KIND_UNVERIFIED:
            continue
        for token in _NUMBER_RE.findall(_strip_dates(str(fact.display))):
            allowed.add(_norm_number(token))
        if fact.numeric is not None:
            allowed.add(_norm_number(fact.numeric))
    allowed.add(_norm_number(len([f for f in facts if f.kind != KIND_UNVERIFIED])))
    values = [f.numeric for f in facts if f.kind != KIND_UNVERIFIED and f.numeric is not None]
    if values:
        allowed.add(_norm_number(sum(values)))
    allowed.add(_norm_number(len([f for f in facts if f.fact_id.startswith("permit:")])))
    return {a for a in allowed if a}


def _supported_dates(facts: list[Fact]) -> set[str]:
    allowed: set[str] = set()
    for fact in facts:
        if fact.kind == KIND_UNVERIFIED:
            continue
        allowed.update(_DATE_RE.findall(str(fact.display)))
    return allowed


def _norm_number(token: Any) -> str:
    """Canonical form of a number token, so '2,500,000', '2500000' and '2500000.0' agree."""
    text = str(token).strip().replace(",", "").rstrip(".")
    if not text:
        return ""
    try:
        value = float(text)
    except ValueError:
        return text
    if value == int(value):
        return str(int(value))
    return repr(value).rstrip("0").rstrip(".")


_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SCALE_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|bn|b|thousand|million|billion)\b", re.IGNORECASE
)
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_NV_MARKER_RE = re.compile(r"\bnot[\s-]?verified\b", re.IGNORECASE)
_SCALES = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "million": 1_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
    "billion": 1_000_000_000,
}


def _strip_dates(text: str) -> str:
    return _DATE_RE.sub(" ", text)


def _numbers_in(text: str) -> set[str]:
    """Normalised numbers in a piece of prose, with scale words expanded.

    "2.5M" becomes "2500000" so a legitimate paraphrase of a stored value is not flagged, while a
    value that appears nowhere in the dossier still is.
    """
    found: set[str] = set()
    scrubbed = _strip_dates(text)

    def expand(match: re.Match[str]) -> str:
        value = float(match.group(1).replace(",", "")) * _SCALES[match.group(2).lower()]
        found.add(_norm_number(value))
        return " "

    remaining = _SCALE_RE.sub(expand, scrubbed)
    for token in _NUMBER_RE.findall(remaining):
        found.add(_norm_number(token))
    return {f for f in found if f}


def _dates_in(text: str) -> set[str]:
    return set(_DATE_RE.findall(text))


#: A whole word that denies a claim it sits beside. Contractions are included as-is; a token with
#: sentence-ending punctuation deliberately does not match, so a bare "No." cannot excuse a claim.
_NEGATIONS = frozenset(
    {"not", "no", "never", "cannot", "can't", "without", "n't", "isn't", "doesn't", "don't",
     "aren't", "wasn't", "won't"}
)
#: Clause boundaries. A negation is only read as denying a phrase in its own clause, so a
#: separate clause ("... does not confirm a bid. The package is out for bid now.") still flags.
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;,\n]+")


def _clause_of(text: str, index: int) -> str:
    """The clause around ``index``, from the previous boundary to the next."""
    start = 0
    end = len(text)
    for match in _CLAUSE_SPLIT_RE.finditer(text):
        if match.end() <= index:
            start = match.end()
        elif match.start() >= index:
            end = match.start()
            break
    return text[start:end]


def _negated(text: str, phrase: str) -> bool:
    """True when every occurrence of ``phrase`` is denied within its own clause.

    The caveat is itself a negation ("does not by itself confirm that the package is currently
    available for bid"). A model that restates it is being honest, so the check must not read a
    denial as a claim. It stays conservative: the claim is excused only when *every* occurrence
    sits in a clause that denies it.
    """
    start = 0
    seen = False
    while True:
        index = text.find(phrase, start)
        if index < 0:
            break
        seen = True
        clause = _clause_of(text, index)
        words = re.findall(r"[\w']+", clause)
        if not any(word in _NEGATIONS for word in words):
            return False
        start = index + len(phrase)
    return seen


def validate_brief(
    brief: AnalystBrief, facts: list[Fact], *, notice: str = PERMIT_EVIDENCE_NOTICE
) -> list[str]:
    """Re-check the model's prose against the facts. Returns a list of violations.

    A violation means the model said something the evidence does not license, so the brief must not
    be published as verified. The checks are deliberately literal: a number, a date, a marker or a
    phrase is either in the evidence or it is not.
    """
    issues: list[str] = []
    allowed_numbers = _supported_numbers(facts)
    allowed_dates = _supported_dates(facts)
    # A field is assessable when it is a presented field of the record, whether or not a source
    # supports it. An unsupported field has an expected verdict of NOT VERIFIED, so a model that
    # correctly reports it as unverified passes, and one that upgrades it does not.
    verdict_by_field = {
        f.field_name: f.verdict for f in facts if f.field_name
    }

    free_text = [("summary", brief.narrative), ("why_it_matters", brief.why_it_matters)]
    free_text.extend(
        (f"field_assessments[{a.field}].reason", a.reason) for a in brief.field_assessments
    )
    free_text.extend((f"questions_to_resolve[{i}]", q) for i, q in enumerate(brief.questions_to_resolve))
    free_text.extend((f"limitations[{i}]", l) for i, l in enumerate(brief.limitations))

    for where, text in free_text:
        if not text:
            continue
        lowered = text.lower()

        for number in sorted(_numbers_in(text)):
            if number not in allowed_numbers:
                issues.append(f"{where}: figure {number} is not supported by any stored value.")

        for found_date in sorted(_dates_in(text)):
            if found_date not in allowed_dates:
                issues.append(f"{where}: date {found_date} is not supported by any stored date.")

        if _NV_MARKER_RE.search(text):
            issues.append(
                f"{where}: restates the not-verified marker; an absent field must be omitted."
            )
        if _URL_RE.search(text):
            issues.append(f"{where}: contains a URL, which must come from the sources list.")

        for phrase in FORBIDDEN_CLAIMS:
            if phrase in lowered and not _negated(lowered, phrase):
                issues.append(f"{where}: asserts a bid is available ('{phrase}').")

    for assessment in brief.field_assessments:
        expected = verdict_by_field.get(assessment.field)
        if expected is None:
            issues.append(
                f"field_assessments: '{assessment.field}' is not a supported field of this record."
            )
        elif assessment.verdict != expected:
            issues.append(
                f"field_assessments: '{assessment.field}' reported {assessment.verdict} "
                f"but the record supports {expected}."
            )
        if assessment.verdict not in (CONFIRMED, PARTIALLY_VERIFIED, VERDICT_NOT_VERIFIED):
            issues.append(
                f"field_assessments: '{assessment.field}' has an unknown verdict "
                f"'{assessment.verdict}'."
            )

    if brief.confidence is not None and brief.confidence not in CONFIDENCE_LEVELS:
        issues.append(f"confidence: '{brief.confidence}' is not one of {list(CONFIDENCE_LEVELS)}.")

    if notice and notice.lower() not in (brief.caveat or "").lower():
        issues.append("caveat: the permit-evidence notice is missing.")

    return issues


def deterministic_narrative(brief: AnalystBrief) -> str:
    """An evidence-only summary, used when the model's prose cannot be published.

    It is assembled from the facts, so it is true by construction. It states what is supported and
    what the record does not say, and makes no reading of its own. It deliberately does not say
    *why* it was used: the status and the limitations carry that, and a sentence here would be
    wrong in one of the two cases (no model configured, or a narrative that failed its check).
    """
    supported = [f for f in brief.facts if f.kind == KIND_VERIFIED]
    unsupported = [f.label for f in brief.facts if f.kind == KIND_UNVERIFIED]
    lead = " · ".join(
        f"{f.label}: {f.display}" for f in supported[:6]
    ) or "No field of this record is supported by a stored source."
    parts = [lead]
    if unsupported:
        parts.append("Not stated in the record: " + ", ".join(unsupported) + ".")
    parts.append("This summary is assembled directly from the stored evidence.")
    return " ".join(parts)


def deterministic_limitations(facts: list[Fact]) -> list[str]:
    """Limitations derived from the facts alone, for a brief that cannot publish model prose."""
    return [
        f"{f.label} is not stated in the record." for f in facts if f.kind == KIND_UNVERIFIED
    ]


# --- build ---------------------------------------------------------------------

def _brief_from_payload(
    payload: dict[str, Any],
    *,
    project_id: int,
    slug: str | None,
    model: str,
    generated_at: str,
    notice: str,
) -> AnalystBrief:
    assessments: list[FieldAssessment] = []
    for item in payload.get("field_assessments") or []:
        if not isinstance(item, dict):
            continue
        assessments.append(
            FieldAssessment(
                field=str(item.get("field") or "").strip(),
                verdict=str(item.get("verdict") or "").strip().upper(),
                reason=str(item.get("reason") or "").strip(),
            )
        )
    return AnalystBrief(
        project_id=project_id,
        slug=slug,
        status=STATUS_AVAILABLE,
        model=model,
        generated_at=generated_at,
        narrative=str(payload.get("summary") or "").strip(),
        why_it_matters=str(payload.get("why_it_matters") or "").strip(),
        field_assessments=assessments,
        questions_to_resolve=[str(q) for q in (payload.get("questions_to_resolve") or [])],
        confidence=str(payload.get("confidence") or "").strip().lower() or None,
        limitations=[str(l) for l in (payload.get("limitations") or [])],
        caveat=notice,
    )


def _slug_for(db: Database, project_id: int) -> str | None:
    row = db.conn.execute(
        "SELECT slug FROM project_slug WHERE project_id = ?", (project_id,)
    ).fetchone()
    return row["slug"] if row else None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_brief(
    db: Database,
    project_id: int,
    *,
    client: AnalystClient | None = None,
    model: str | None = None,
    notice: str = PERMIT_EVIDENCE_NOTICE,
    market: str | None = None,
    trade: str | None = None,
) -> AnalystBrief:
    """Build a grounded analysis of one project.

    Always returns a brief. When the model is absent or its prose fails verification, the brief
    still carries the evidence and says so in ``status`` — an operator is never shown a silent
    failure and never shown an unverified claim dressed as a verified one.
    """
    facts = collect_facts(db, project_id)
    slug = _slug_for(db, project_id)

    from .config import active_market, active_trade

    dossier = assemble_dossier(
        project_id,
        facts,
        market=market or active_market().short_name,
        trade=trade or active_trade().short_label,
    )

    brief = AnalystBrief(
        project_id=project_id,
        slug=slug,
        status=STATUS_UNAVAILABLE,
        model=None,
        generated_at=_now(),
        facts=facts,
        caveat=notice,
    )

    if not dossier.supported:
        brief.limitations.append(
            "No field of this record is supported by a stored source, so there is nothing to "
            "analyse."
        )
        brief.narrative = deterministic_narrative(brief)
        return brief

    client = client if client is not None else GeminiClient(model=model)
    if not client.available:
        brief.limitations.append(
            "No Gemini model is configured (set GEMINI_API_KEY). The analysis is unavailable; "
            "the evidence below is unaffected."
        )
        brief.narrative = deterministic_narrative(brief)
        return brief

    try:
        payload = client.analyse(SYSTEM_INSTRUCTION, dossier.text)
    except AnalystError as exc:
        brief.limitations.append(str(exc))
        brief.narrative = deterministic_narrative(brief)
        return brief

    proposed = _brief_from_payload(
        payload,
        project_id=project_id,
        slug=slug,
        model=client.model if isinstance(client, GeminiClient) else (model or DEFAULT_MODEL),
        generated_at=brief.generated_at,
        notice=notice,
    )
    proposed.facts = facts
    issues = validate_brief(proposed, facts, notice=notice)
    proposed.verification_issues = issues

    if issues:
        # The model answered but said something the evidence does not license. The brief is not
        # published as verified; every piece of model prose is replaced with evidence-derived
        # text, and confidence drops to the floor, because a reading that failed its grounding
        # check is not a reading an operator should lean on.
        proposed.status = STATUS_UNVERIFIED
        proposed.narrative = deterministic_narrative(proposed)
        proposed.why_it_matters = ""
        proposed.field_assessments = []
        proposed.questions_to_resolve = []
        proposed.limitations = deterministic_limitations(facts)
        proposed.confidence = CONFIDENCE_LEVELS[-1]
    return proposed
