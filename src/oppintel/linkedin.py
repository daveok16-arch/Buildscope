"""LinkedIn Content Intelligence: evidence-backed drafts, ready for a human to edit and post.

The product's honesty rule does not stop at the public site. A social post derived from permit
data can mislead just as easily as a stale dashboard, and a misleading post is harder to retract.
So a draft here is built from three separately-labelled blocks, and the split is the whole point:

* **Verified facts** — a line is emitted only for a field whose value is actually supported by
  a stored evidence row (``reporting.field_verdict``). A field with no support is never turned
  into a claim; it is listed as unknown instead.
* **Unverified** — fields that are absent or unsupported. These are named so the writer knows
  what the record does not say, and must not fill in from imagination.
* **Interpretation** — a single, explicitly-labelled reading (e.g. that the declared value is the
  largest in a comparable set). It is separated so it can be cut without touching the facts, and
  it always states the comparison it rests on.

Every post carries the fixed permit-evidence caveat: permit evidence shows a project was filed,
not that a package is out for bid. Nothing is fetched from an external model; a draft is a
deterministic transformation of stored rows, so it is reproducible and reviewable.

A deterministic ``validate`` pass then re-reads the draft and flags any fact-shaped line whose
figures or dates are not traceable to the project. It is a guard against a template drifting out
of sync with the data — a check, not a generation step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .report_generator import PERMIT_EVIDENCE_NOTICE

#: The distinct post formats. Each is a different shape of post, not a synonym for another.
FORMAT_PROJECT_SPOTLIGHT = "project_spotlight"
FORMAT_PATTERN = "pattern"
FORMAT_MARKET_PULSE = "market_pulse"
FORMAT_DATA_QUALITY = "data_quality"
FORMAT_CHANGE_ALERT = "change_alert"

FORMATS: tuple[tuple[str, str, str], ...] = (
    (FORMAT_PROJECT_SPOTLIGHT, "Project spotlight",
     "One documented project, told only through its verified fields."),
    (FORMAT_PATTERN, "Pattern",
     "A pattern across a set of projects, stated as a count with the set defined."),
    (FORMAT_MARKET_PULSE, "Market pulse",
     "Observed volume in a period, using the Trend Radar metrics and their definitions."),
    (FORMAT_DATA_QUALITY, "Data quality",
     "What the record does not say, which is itself a useful, honest post."),
    (FORMAT_CHANGE_ALERT, "Change alert",
     "A project whose stored value changed, quoting the before and after."),
)

FORMAT_IDS = tuple(f[0] for f in FORMATS)

#: Minimum scale for a spotlight candidate. The declared value *moves* the candidate up the
#: ranking but never *admits* it: this floor is about worth-posting-in-public, not about
#: asserting the value. The value itself is still only quoted when a source evidences it.
MIN_SPOTLIGHT_VALUE = 1_000_000.0


@dataclass
class FactLine:
    """One line of a draft, tagged with where the claim comes from."""

    text: str
    kind: str          # verified | unverified | interpretation | editorial
    field_name: str | None = None


@dataclass
class GraphicBrief:
    """A text description of an image to accompany the post. No image is generated here."""

    headline: str
    stat_lines: list[str] = field(default_factory=list)
    caption: str = ""
    alt_text: str = ""
    palette_hint: str = ""


@dataclass
class LinkedInPost:
    """A draft post, its facts, and the brief for its graphic."""

    format: str
    format_label: str
    working_title: str
    project_id: int | None
    slug: str | None
    lines: list[FactLine] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    caveat: str = PERMIT_EVIDENCE_NOTICE
    graphic: GraphicBrief | None = None
    sources: list[dict] = field(default_factory=list)
    generated_at: str = ""

    @property
    def verified(self) -> list[str]:
        return [l.text for l in self.lines if l.kind == "verified"]

    @property
    def unverified(self) -> list[str]:
        return [l.text for l in self.lines if l.kind == "unverified"]

    @property
    def interpretation(self) -> list[str]:
        return [l.text for l in self.lines if l.kind == "interpretation"]

    def render(self) -> str:
        """The post body a human edits, with facts and interpretation clearly separated."""
        parts: list[str] = [self.working_title, ""]
        if self.verified:
            parts.append("Verified from the public record:")
            parts.extend(f"• {t}" for t in self.verified)
            parts.append("")
        if self.interpretation:
            parts.append("Our read (interpretation, not fact):")
            parts.extend(f"• {t}" for t in self.interpretation)
            parts.append("")
        if self.unverified:
            parts.append("Not stated in the record:")
            parts.extend(f"• {t}" for t in self.unverified)
            parts.append("")
        parts.append(self.caveat)
        if self.hashtags:
            parts.append("")
            parts.append(" ".join(self.hashtags))
        return "\n".join(parts)

    @property
    def char_count(self) -> int:
        return len(self.render())


# --- fact assembly ------------------------------------------------------------

#: Fields a spotlight quotes, with the label a post uses, and whether they are required enough
#: to be named as unknown when absent.
_SPOTLIGHT_FIELDS: tuple[tuple[str, str], ...] = (
    ("project_name", "Project"),
    ("address", "Address"),
    ("city", "City"),
    ("project_type", "Project type"),
    ("permit_date", "Permit date"),
    ("project_status", "Status"),
    ("mechanical_hvac_evidence", "Documented scope"),
    ("estimated_project_value", "Declared value"),
    ("square_footage", "Building scale"),
    ("owner", "Owner / developer"),
    ("general_contractor", "General contractor"),
)


def _fmt_money(value: Any) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M".replace(".0M", "M")
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount:.0f}"


def _fmt_scale(value: Any) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return f"{amount:,.0f} sq ft"


def _fmt_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _field_line(label: str, field_name: str, value: Any) -> str | None:
    """Render one project field as a post line, or None when there is nothing to say."""
    if field_name == "estimated_project_value":
        rendered = _fmt_money(value)
    elif field_name == "square_footage":
        rendered = _fmt_scale(value)
    else:
        rendered = _fmt_label(value)
    if not rendered:
        return None
    return f"{label}: {rendered}"


def _collect_field_lines(
    db: Database, project: dict[str, Any],
) -> tuple[list[FactLine], list[FactLine]]:
    """Split a project's fields into verified lines and unverified (unknown) lines.

    A field is "verified" only when ``field_verdict`` confirms a stored evidence row supports
    the exact value. Any other outcome — absent value, or a value with no supporting evidence —
    becomes an explicit "not stated" line, so the writer can see the gap rather than guess.
    """
    from .reporting import NOT_VERIFIED, field_verdict

    verified: list[FactLine] = []
    unknown: list[FactLine] = []
    for field_name, label in _SPOTLIGHT_FIELDS:
        value = project.get(field_name)
        verdict, _evidence = field_verdict(db, project["id"], field_name, value)
        line = _field_line(label, field_name, value)
        if verdict != NOT_VERIFIED and line:
            verified.append(FactLine(text=line, kind="verified", field_name=field_name))
        elif line:
            # A value is present but no stored evidence supports it; do not present it.
            unknown.append(
                FactLine(text=f"{label}: present in the record but not yet supported by a "
                              f"source citation", kind="unverified", field_name=field_name)
            )
        else:
            unknown.append(
                FactLine(text=f"{label}: not stated in the source", kind="unverified",
                         field_name=field_name)
            )
    return verified, unknown


def _sources_for(db: Database, project: dict[str, Any]) -> list[dict]:
    rows = db.conn.execute(
        "SELECT DISTINCT source_name, source_url FROM evidence WHERE project_id = ? "
        "ORDER BY source_name",
        (project["id"],),
    ).fetchall()
    return [{"name": r["source_name"], "url": r["source_url"]} for r in rows]


# --- candidate discovery ------------------------------------------------------

@dataclass
class Candidate:
    """A project proposed for a post, with the reason it was proposed (all checkable)."""

    project_id: int
    slug: str | None
    headline: str
    reasons: list[str]
    signals: dict[str, Any] = field(default_factory=dict)


def discover_candidates(
    db: Database, *, trade: str = "commercial_hvac", limit: int = 20,
) -> list[Candidate]:
    """Propose spotlight candidates from stored, checkable signals.

    Ranking is deterministic and every signal that raised a project is listed as a reason, so a
    proposal is auditable. Nothing here asserts an opportunity: the signals are scale, evidence
    tier, a detected change, and recency of the permit date — each a stored fact.
    """
    rows = db.conn.execute(
        """
        SELECT p.id, p.project_name, p.address, p.city, p.permit_date, p.estimated_project_value,
               p.square_footage, p.mechanical_evidence_tier, p.classification, p.procurement_status,
               s.slug,
               (SELECT COUNT(*) FROM project_change pc
                 WHERE pc.project_id = p.id AND pc.change_kind <> 'new_project') AS change_count,
               (SELECT COUNT(*) FROM evidence e WHERE e.project_id = p.id) AS evidence_count
          FROM project p
          LEFT JOIN project_slug s ON s.project_id = p.id
         WHERE p.trade = ?
           AND p.classification IN ('HIGH', 'MEDIUM')
           AND p.procurement_status <> 'Closed'
         ORDER BY p.permit_date DESC, p.id
         LIMIT 400
        """,
        (trade,),
    ).fetchall()

    candidates: list[Candidate] = []
    for row in rows:
        value = row["estimated_project_value"] or 0
        tier = row["mechanical_evidence_tier"]
        change_count = int(row["change_count"] or 0)
        evidence_count = int(row["evidence_count"] or 0)
        reasons: list[str] = []
        score = 0.0
        if value and float(value) >= MIN_SPOTLIGHT_VALUE:
            reasons.append(f"Declared value at or above ${MIN_SPOTLIGHT_VALUE/1_000_000:.0f}M")
            score += min(float(value) / 1_000_000.0, 20.0)
        if tier in (1, 2):
            reasons.append(f"Strong mechanical evidence (tier {tier})")
            score += 6
        if change_count:
            reasons.append(f"{change_count} documented change(s) since first observation")
            score += min(change_count, 5)
        if row["permit_date"]:
            reasons.append(f"Permit dated {row['permit_date']}")
        if evidence_count:
            reasons.append(f"{evidence_count} supporting evidence row(s)")
        if not reasons:
            continue
        name = _fmt_label(row["project_name"]) or _fmt_label(row["address"]) or "Commercial project"
        candidates.append(
            Candidate(
                project_id=int(row["id"]),
                slug=row["slug"],
                headline=name,
                reasons=reasons,
                signals={
                    "value": float(value) if value else None,
                    "tier": tier,
                    "change_count": change_count,
                    "permit_date": row["permit_date"],
                    "city": row["city"],
                    "score": round(score, 2),
                },
            )
        )
    candidates.sort(key=lambda c: (-c.signals.get("score", 0), c.project_id))
    return candidates[:limit]


# --- draft builders -----------------------------------------------------------

def _project_row(db: Database, project_id: int) -> dict[str, Any] | None:
    row = db.conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def _slug_for(db: Database, project_id: int) -> str | None:
    row = db.conn.execute(
        "SELECT slug FROM project_slug WHERE project_id = ?", (project_id,)
    ).fetchone()
    return row["slug"] if row else None


def _base_hashtags(city: str | None) -> list[str]:
    tags = ["#CommercialConstruction", "#ConstructionLeads"]
    if city:
        tags.append("#" + re.sub(r"[^A-Za-z]", "", city))
    return tags


def build_spotlight(db: Database, project_id: int) -> LinkedInPost:
    """A single documented project, told only through its verified fields."""
    project = _project_row(db, project_id)
    if project is None:
        raise ValueError(f"no project {project_id}")
    verified, unknown = _collect_field_lines(db, project)
    name = _fmt_label(project.get("project_name")) or _fmt_label(project.get("address")) \
        or "Commercial project"
    interpretation: list[str] = []
    value = project.get("estimated_project_value")
    if value:
        interpretation.append(
            "The declared value places this among the larger records in the dataset; size is "
            "not a signal of procurement, only of scale."
        )
    return LinkedInPost(
        format=FORMAT_PROJECT_SPOTLIGHT,
        format_label="Project spotlight",
        working_title=name,
        project_id=project_id,
        slug=_slug_for(db, project_id),
        lines=verified + [FactLine(text=t, kind="interpretation") for t in interpretation] + unknown,
        hashtags=_base_hashtags(project.get("city")),
        graphic=GraphicBrief(
            headline=name,
            stat_lines=_graphic_stats(project),
            caption=f"{name} — documented commercial construction record.",
            alt_text=(
                f"Text graphic. {name}, {_fmt_label(project.get('city')) or 'unknown city'}. "
                f"Fields shown: " + "; ".join(_graphic_stats(project))
            ),
            palette_hint="Navy background, single gold accent. No stock imagery, no render.",
        ),
        sources=_sources_for(db, project),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _graphic_stats(project: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if project.get("city"):
        out.append(str(project["city"]))
    if project.get("project_type"):
        out.append(str(project["project_type"]))
    money = _fmt_money(project.get("estimated_project_value"))
    if money:
        out.append(money)
    scale = _fmt_scale(project.get("square_footage"))
    if scale:
        out.append(scale)
    return out


def build_pattern(db: Database, *, project_ids: list[int], trade: str = "commercial_hvac") -> LinkedInPost:
    """A count across a defined set. The set is stated so the count cannot be read as a total."""
    ids = list(dict.fromkeys(int(i) for i in project_ids))
    if not ids:
        raise ValueError("a pattern needs at least one project")
    placeholders = ",".join("?" for _ in ids)
    row = db.conn.execute(
        f"""
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN estimated_project_value IS NOT NULL THEN 1 ELSE 0 END) AS with_value,
               SUM(CASE WHEN mechanical_evidence_tier IN (1,2) THEN 1 ELSE 0 END) AS strong,
               AVG(CASE WHEN estimated_project_value IS NOT NULL THEN estimated_project_value END) AS avg_value
          FROM project WHERE id IN ({placeholders})
        """,
        ids,
    ).fetchone()
    n = int(row["n"] or 0)
    verified = [
        FactLine(text=f"Set: the {n} project(s) listed below, drawn from this dataset.", kind="verified"),
        FactLine(text=f"{int(row['strong'] or 0)} of {n} carry strong (tier 1–2) mechanical evidence.",
                 kind="verified"),
    ]
    if row["with_value"]:
        avg = _fmt_money(row["avg_value"])
        verified.append(
            FactLine(text=f"{int(row['with_value'])} of {n} declare a value; their mean declared "
                          f"value is {avg}. The mean is over declared records only.",
                     kind="verified")
        )
    else:
        verified.append(FactLine(text="None of these records declare a value; no average is shown.",
                                 kind="verified"))
    return LinkedInPost(
        format=FORMAT_PATTERN,
        format_label="Pattern",
        working_title="A pattern in the local commercial pipeline",
        project_id=None,
        slug=None,
        lines=verified + [
            FactLine(text="This is a count in the records BuildScope holds, not a market total.",
                     kind="interpretation")
        ],
        hashtags=_base_hashtags(_city_of(db, ids)),
        graphic=GraphicBrief(
            headline="By the numbers",
            stat_lines=[f"{n} projects", f"{int(row['strong'] or 0)} with strong evidence"],
            caption="Counts across a stated set of records.",
            alt_text=f"Text graphic listing {n} projects and {int(row['strong'] or 0)} with strong evidence.",
            palette_hint="Navy background, gold numerals.",
        ),
        sources=_sources_many(db, ids),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def build_market_pulse(db: Database, *, window: str = "30d", trade: str = "commercial_hvac") -> LinkedInPost:
    """Observed volume for a period, built from the Trend Radar metrics and their definitions."""
    from .trends import build_trend_report, format_period

    report = build_trend_report(db, window=window, trade=trade)
    lines: list[FactLine] = [
        FactLine(text=f"Observed window: {format_period(report.period)}.", kind="verified"),
    ]
    for m in report.metrics:
        lines.append(FactLine(text=f"{m.label}: {m.value}. {m.definition}", kind="verified"))
    if not report.metric_index["projects_observed"].comparison_available:
        lines.append(
            FactLine(text="No prior period exists in this dataset, so no change is stated.",
                     kind="unverified")
        )
    if report.insufficient:
        lines.append(
            FactLine(text="Fewer than the confidence floor of new projects appeared; these are "
                          "raw observations, not a trend.", kind="interpretation")
        )
    lines.append(
        FactLine(text="These counts describe the records BuildScope holds and when it observed "
                      "them, not any city's total permit volume.", kind="interpretation")
    )
    return LinkedInPost(
        format=FORMAT_MARKET_PULSE,
        format_label="Market pulse",
        working_title=f"Observed construction activity, last {window}",
        project_id=None,
        slug=None,
        lines=lines,
        hashtags=_base_hashtags(None),
        graphic=GraphicBrief(
            headline=f"Observed activity — last {window}",
            stat_lines=[f"{m.label}: {m.value}" for m in report.metrics],
            caption="Counts from the records BuildScope holds.",
            alt_text="Text graphic of observed construction counts with their definitions.",
            palette_hint="Navy background, gold numerals, definitions in small print.",
        ),
        sources=[],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def build_data_quality(db: Database, project_id: int) -> LinkedInPost:
    """A post about what a record does *not* say — an honest post in its own right."""
    project = _project_row(db, project_id)
    if project is None:
        raise ValueError(f"no project {project_id}")
    verified, unknown = _collect_field_lines(db, project)
    name = _fmt_label(project.get("project_name")) or _fmt_label(project.get("address")) \
        or "Commercial project"
    lines = [
        FactLine(text=f"Record: {name}.", kind="verified"),
        FactLine(text=f"{len(verified)} field(s) are supported by a stored source citation.",
                 kind="verified"),
    ] + unknown
    lines.append(
        FactLine(text="Publishing the gaps is the point: a field we cannot evidence is shown as "
                      "unknown, never filled in.", kind="interpretation")
    )
    return LinkedInPost(
        format=FORMAT_DATA_QUALITY,
        format_label="Data quality",
        working_title=f"What the record for {name} does not tell us",
        project_id=project_id,
        slug=_slug_for(db, project_id),
        lines=lines,
        hashtags=_base_hashtags(project.get("city")),
        graphic=GraphicBrief(
            headline="Verified vs unknown",
            stat_lines=[f"{len(verified)} verified", f"{len(unknown)} not stated"],
            caption="The gap between what a record states and what a citation supports.",
            alt_text=f"Text graphic: {len(verified)} verified fields, {len(unknown)} not stated.",
            palette_hint="Navy background, gold for verified, slate for unknown.",
        ),
        sources=_sources_for(db, project),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def build_change_alert(db: Database, project_id: int) -> LinkedInPost:
    """A post about a stored change, quoting the before and after from the change rows."""
    project = _project_row(db, project_id)
    if project is None:
        raise ValueError(f"no project {project_id}")
    changes = db.conn.execute(
        "SELECT field_name, previous_value, current_value, change_kind, summary, detected_at "
        "FROM project_change WHERE project_id = ? AND change_kind <> 'new_project' "
        "ORDER BY detected_at DESC LIMIT 5",
        (project_id,),
    ).fetchall()
    if not changes:
        raise ValueError(f"project {project_id} has no recorded change")
    name = _fmt_label(project.get("project_name")) or _fmt_label(project.get("address")) \
        or "Commercial project"
    lines = [FactLine(text=f"Record: {name}.", kind="verified")]
    for c in changes:
        prev = _fmt_label(c["previous_value"]) or "(none)"
        curr = _fmt_label(c["current_value"]) or "(cleared)"
        lines.append(
            FactLine(text=f"{c['summary']} Changed {c['field_name']}: {prev} -> {curr} "
                          f"(observed {str(c['detected_at'])[:10]}).", kind="verified")
        )
    lines.append(
        FactLine(text="The change is a movement in the stored record, not necessarily a change "
                      "on the ground.", kind="interpretation")
    )
    return LinkedInPost(
        format=FORMAT_CHANGE_ALERT,
        format_label="Change alert",
        working_title=f"{name}: what moved in the record",
        project_id=project_id,
        slug=_slug_for(db, project_id),
        lines=lines,
        hashtags=_base_hashtags(project.get("city")),
        graphic=GraphicBrief(
            headline="Before -> after",
            stat_lines=[f"{c['field_name']}: {_fmt_label(c['previous_value']) or '(none)'} -> "
                        f"{_fmt_label(c['current_value']) or '(cleared)'}" for c in changes],
            caption="A documented change in the stored record.",
            alt_text=f"Text graphic showing {len(changes)} changed field(s).",
            palette_hint="Navy background, gold arrows.",
        ),
        sources=_sources_for(db, project),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def build_draft(db: Database, *, format: str, project_id: int | None = None,
                project_ids: list[int] | None = None, window: str = "30d",
                trade: str = "commercial_hvac") -> LinkedInPost:
    """Dispatch to the requested format. An unknown format is a caller error, not a fallback."""
    if format == FORMAT_PROJECT_SPOTLIGHT:
        return build_spotlight(db, project_id)  # type: ignore[arg-type]
    if format == FORMAT_DATA_QUALITY:
        return build_data_quality(db, project_id)  # type: ignore[arg-type]
    if format == FORMAT_CHANGE_ALERT:
        return build_change_alert(db, project_id)  # type: ignore[arg-type]
    if format == FORMAT_PATTERN:
        return build_pattern(db, project_ids=project_ids or [], trade=trade)
    if format == FORMAT_MARKET_PULSE:
        return build_market_pulse(db, window=window, trade=trade)
    raise ValueError(f"unknown format {format!r}")


def _city_of(db: Database, ids: list[int]) -> str | None:
    if not ids:
        return None
    placeholders = ",".join("?" for _ in ids)
    row = db.conn.execute(
        f"SELECT city FROM project WHERE id IN ({placeholders}) AND city IS NOT NULL "
        "GROUP BY city ORDER BY COUNT(*) DESC LIMIT 1",
        ids,
    ).fetchone()
    return row["city"] if row else None


def _sources_many(db: Database, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = db.conn.execute(
        f"SELECT DISTINCT source_name, source_url FROM evidence WHERE project_id IN ({placeholders}) "
        "ORDER BY source_name",
        ids,
    ).fetchall()
    return [{"name": r["source_name"], "url": r["source_url"]} for r in rows]


# --- deterministic validation -------------------------------------------------

#: A dollar figure, and an ISO date. Used to find fact-shaped tokens in a draft.
_MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:[MK])?")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def validate_post(db: Database, post: LinkedInPost) -> list[str]:
    """Flag any figure or date in the *verified* lines that the project does not support.

    This is a guard, not a generator: it re-derives the project's supported values and checks
    that each fact-shaped token in the draft is one of them. Any flag means a template has
    drifted from the data and the draft must not be posted until it is reconciled.
    """
    from .reporting import NOT_VERIFIED, field_verdict

    problems: list[str] = []
    if post.project_id is None:
        return problems

    project = _project_row(db, post.project_id)
    if project is None:
        return [f"project {post.project_id} no longer exists"]

    supported_values: set[str] = set()
    supported_dates: set[str] = set()
    for field_name, _label in _SPOTLIGHT_FIELDS:
        value = project.get(field_name)
        verdict, _evidence = field_verdict(db, post.project_id, field_name, value)
        if verdict == NOT_VERIFIED:
            continue
        if field_name == "estimated_project_value":
            money = _fmt_money(value)
            if money:
                supported_values.add(money)
        elif field_name == "square_footage":
            scale = _fmt_scale(value)
            if scale:
                supported_values.add(scale)
        elif field_name == "permit_date" and value:
            supported_dates.add(str(value))

    for line in post.lines:
        if line.kind != "verified":
            continue
        for token in _MONEY_RE.findall(line.text):
            if token not in supported_values:
                problems.append(f"unsupported figure in draft: {token}")
        for token in _DATE_RE.findall(line.text):
            if token not in supported_dates:
                problems.append(f"unsupported date in draft: {token}")
    return problems
