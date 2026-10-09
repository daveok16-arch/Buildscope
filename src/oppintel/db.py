"""SQLite persistence layer.

The schema deliberately avoids SQLite-only constructs (no AUTOINCREMENT keyword beyond
INTEGER PRIMARY KEY, no STRICT tables) so migrating to PostgreSQL later is a driver swap
rather than a rewrite of the queries.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Evidence, Permit, Project, ProjectParty

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    publisher          TEXT,
    kind               TEXT NOT NULL,
    jurisdiction_city  TEXT,
    market_coverage    TEXT,
    coverage_note      TEXT,
    portal_url         TEXT,
    reliability        REAL,
    notes              TEXT,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_run (
    id                 INTEGER PRIMARY KEY,
    source_id          TEXT NOT NULL REFERENCES source(id),
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL,
    rows_fetched       INTEGER DEFAULT 0,
    rows_landed        INTEGER DEFAULT 0,
    permits_created    INTEGER DEFAULT 0,
    error              TEXT
);

CREATE TABLE IF NOT EXISTS raw_record (
    id                 INTEGER PRIMARY KEY,
    source_id          TEXT NOT NULL REFERENCES source(id),
    run_id             INTEGER REFERENCES ingest_run(id),
    natural_key        TEXT NOT NULL,
    payload            TEXT NOT NULL,
    payload_hash       TEXT NOT NULL,
    fetched_at         TEXT NOT NULL,
    UNIQUE (source_id, natural_key, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_record_source ON raw_record(source_id, natural_key);

-- Per-source coverage metadata, refreshed on every ingest run. Records what the source
-- actually returned rather than what it is documented to return, so a truncated crawl or a
-- moving coverage window is visible instead of being inferred from the record count.
CREATE TABLE IF NOT EXISTS source_coverage (
    source_id          TEXT PRIMARY KEY REFERENCES source(id) ON DELETE CASCADE,
    earliest_date      TEXT,
    latest_date        TEXT,
    retrieval_date     TEXT NOT NULL,
    record_count       INTEGER NOT NULL DEFAULT 0,
    commercial_count   INTEGER NOT NULL DEFAULT 0,
    mechanical_count   INTEGER NOT NULL DEFAULT 0,
    pagination_pages   INTEGER,
    pagination_notes   TEXT,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permit (
    id                 INTEGER PRIMARY KEY,
    source_id          TEXT NOT NULL REFERENCES source(id),
    natural_key        TEXT NOT NULL,
    permit_number      TEXT NOT NULL,
    permit_type        TEXT,
    permit_subtype     TEXT,
    permit_date        TEXT,
    status             TEXT,
    address            TEXT,
    address_key        TEXT,
    city               TEXT,
    state              TEXT,
    zip_code           TEXT,
    work_description   TEXT,
    land_use           TEXT,
    specific_use       TEXT,
    job_value          REAL,
    square_footage     REAL,
    owner              TEXT,
    contractor         TEXT,
    is_commercial      INTEGER,
    source_url         TEXT,
    source_date        TEXT,
    updated_at         TEXT NOT NULL,
    UNIQUE (source_id, natural_key)
);

CREATE INDEX IF NOT EXISTS idx_permit_address_key ON permit(address_key);
CREATE INDEX IF NOT EXISTS idx_permit_date ON permit(permit_date);
CREATE INDEX IF NOT EXISTS idx_permit_commercial ON permit(is_commercial);

CREATE TABLE IF NOT EXISTS project (
    id                        INTEGER PRIMARY KEY,
    project_key               TEXT NOT NULL UNIQUE,
    trade                     TEXT NOT NULL,
    project_name              TEXT,
    address                   TEXT,
    city                      TEXT,
    state                     TEXT,
    project_type              TEXT,
    estimated_project_value   REAL,
    square_footage            REAL,
    permit_number             TEXT,
    permit_date               TEXT,
    project_status            TEXT,
    owner                     TEXT,
    developer                 TEXT,
    general_contractor        TEXT,
    architect                 TEXT,
    mechanical_hvac_evidence  TEXT,
    source_name               TEXT,
    source_url                TEXT,
    source_date               TEXT,
    last_verified             TEXT,
    mechanical_evidence_tier  INTEGER,
    property_class            TEXT,
    location_precision        TEXT,
    procurement_status        TEXT,
    classification            TEXT,
    classification_score      INTEGER,
    classification_reasons    TEXT,
    disputed_fields           TEXT,
    discrepancies             TEXT,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_city ON project(city);
CREATE INDEX IF NOT EXISTS idx_project_classification ON project(classification);
CREATE INDEX IF NOT EXISTS idx_project_type ON project(project_type);
CREATE INDEX IF NOT EXISTS idx_project_value ON project(estimated_project_value);
CREATE INDEX IF NOT EXISTS idx_project_permit_date ON project(permit_date);

CREATE TABLE IF NOT EXISTS project_permit (
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    permit_id          INTEGER NOT NULL REFERENCES permit(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, permit_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    field_name         TEXT NOT NULL,
    value              TEXT,
    source_id          TEXT NOT NULL,
    source_name        TEXT NOT NULL,
    source_url         TEXT,
    source_record_key  TEXT,
    source_date        TEXT,
    observed_at        TEXT NOT NULL,
    evidence_type      TEXT,
    tier               INTEGER,
    excerpt            TEXT
);

CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project_id, field_name);

CREATE TABLE IF NOT EXISTS project_party (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    role               TEXT NOT NULL,
    name               TEXT NOT NULL,
    source_id          TEXT NOT NULL,
    source_url         TEXT,
    excerpt            TEXT
);

CREATE INDEX IF NOT EXISTS idx_party_project ON project_party(project_id, role);
CREATE INDEX IF NOT EXISTS idx_party_name ON project_party(name);

CREATE TABLE IF NOT EXISTS project_classification (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    classified_at      TEXT NOT NULL,
    classification     TEXT NOT NULL,
    score              INTEGER NOT NULL,
    reasons            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_classification_project ON project_classification(project_id);

-- =====================================================================
-- Intelligence graph: entities, locations, documents, events, evidence history
-- =====================================================================
--
-- These tables extend the existing permit/project/evidence pipeline; they do not replace
-- it. The rule that shapes every one of them is that a relationship exists only when a
-- source stated it, and an observation is appended, never overwritten.

-- One row per real-world company or person. Two names merge only when their deterministic
-- canonical keys are equal; nothing is merged on fuzzy similarity, because a false merge
-- (two different companies presented as one) is more damaging than a duplicate entity.
CREATE TABLE IF NOT EXISTS entity (
    id                 INTEGER PRIMARY KEY,
    entity_type        TEXT NOT NULL,              -- 'company' | 'person'
    canonical_key      TEXT NOT NULL,              -- deterministic merge identity
    display_name       TEXT NOT NULL,              -- most complete observed spelling
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (entity_type, canonical_key)
);

-- Every observed spelling of an entity, with the source that used it. Kept so a name
-- variant is preserved rather than overwritten by the canonical form.
CREATE TABLE IF NOT EXISTS entity_name (
    id                 INTEGER PRIMARY KEY,
    entity_id          INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    normalized_name    TEXT NOT NULL,
    source_id          TEXT,
    observed_at        TEXT NOT NULL,
    UNIQUE (entity_id, normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_entity_name_norm ON entity_name(normalized_name);

-- A resolved relationship between an entity and a project, carrying the role the source
-- actually stated. The role comes from evidence, never from the mere presence of a name.
CREATE TABLE IF NOT EXISTS entity_project (
    id                 INTEGER PRIMARY KEY,
    entity_id          INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    role               TEXT NOT NULL,              -- owner, general_contractor, architect, ...
    source_id          TEXT,
    source_url         TEXT,
    excerpt            TEXT,
    observed_at        TEXT NOT NULL,
    UNIQUE (entity_id, project_id, role)
);
CREATE INDEX IF NOT EXISTS idx_entity_project_project ON entity_project(project_id, role);
CREATE INDEX IF NOT EXISTS idx_entity_project_entity ON entity_project(entity_id);

-- Normalized project location. The raw address is preserved exactly as the source stated
-- it. Coordinates are populated only with a recorded provenance and confidence; a value
-- with no provenance is left null rather than guessed.
CREATE TABLE IF NOT EXISTS project_location (
    project_id         INTEGER PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
    raw_address        TEXT,
    normalized_address TEXT,
    building_key       TEXT,
    city               TEXT,
    state              TEXT,
    zip_code           TEXT,
    latitude           REAL,
    longitude          REAL,
    geocode_source     TEXT,
    geocode_confidence REAL,
    jurisdiction       TEXT,
    location_precision TEXT,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_location_building ON project_location(building_key);
CREATE INDEX IF NOT EXISTS idx_location_city ON project_location(city);

-- A source record or published document a project's facts were drawn from. The verbatim
-- payload itself stays in `raw_record`; this references it by id.
CREATE TABLE IF NOT EXISTS document (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER REFERENCES project(id) ON DELETE CASCADE,
    source_id          TEXT,
    document_type      TEXT NOT NULL,             -- permit_record, plan, filing, ...
    title              TEXT,
    source_url         TEXT,
    source_record_key  TEXT,
    raw_record_id      INTEGER REFERENCES raw_record(id),
    captured_at        TEXT NOT NULL,
    source_date        TEXT,
    UNIQUE (source_id, source_record_key, document_type)
);
CREATE INDEX IF NOT EXISTS idx_document_project ON document(project_id);

-- Durable, append-only project events: documented lifecycle changes derived from evidence.
-- `event_uid` is a deterministic identity, so re-deriving the same fact is a no-op and an
-- unchanged project produces no new event.
CREATE TABLE IF NOT EXISTS project_event (
    id                 INTEGER PRIMARY KEY,
    event_uid          TEXT NOT NULL UNIQUE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    event_type         TEXT NOT NULL,
    occurred_at        TEXT,                      -- when the fact happened at the source
    recorded_at        TEXT NOT NULL,             -- when BuildScope observed it
    source_id          TEXT,
    source_url         TEXT,
    source_record_key  TEXT,
    summary            TEXT NOT NULL,
    payload            TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_event_project ON project_event(project_id, occurred_at, recorded_at);
CREATE INDEX IF NOT EXISTS idx_event_type ON project_event(event_type, recorded_at);

-- The trade(s) a project carries, classified from documented permit text via the configured
-- taxonomy. A project may legitimately carry more than one trade (a building permit for
-- general construction and a mechanical permit at the same address), so this is a
-- relationship rather than a single column. `is_primary` marks the deepest-evidence trade.
CREATE TABLE IF NOT EXISTS project_trade (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    trade_id           TEXT NOT NULL,
    is_primary         INTEGER NOT NULL DEFAULT 0,
    confidence         TEXT NOT NULL DEFAULT 'documented',
    evidence_source    TEXT,
    classified_at      TEXT NOT NULL,
    UNIQUE (project_id, trade_id)
);
CREATE INDEX IF NOT EXISTS idx_project_trade ON project_trade(trade_id, is_primary);
CREATE INDEX IF NOT EXISTS idx_project_trade_project ON project_trade(project_id);

-- Immutable evidence archive. Every distinct observation is appended here and never
-- overwritten, so a newer record adds history rather than erasing the prior one. This is
-- what makes "what changed over time" answerable; the `evidence` table remains the current
-- supporting set for the displayed values.
CREATE TABLE IF NOT EXISTS evidence_history (
    id                 INTEGER PRIMARY KEY,
    fingerprint        TEXT NOT NULL UNIQUE,      -- deterministic identity of the observation
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    field_name         TEXT NOT NULL,
    value              TEXT,
    source_id          TEXT NOT NULL,
    source_name        TEXT NOT NULL,
    source_url         TEXT,
    source_record_key  TEXT,
    source_date        TEXT,
    evidence_type      TEXT,
    tier               INTEGER,
    excerpt            TEXT,
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ehistory_project ON evidence_history(project_id, field_name);
CREATE INDEX IF NOT EXISTS idx_ehistory_source ON evidence_history(source_id, source_record_key);
"""

#: Application-layer schema. Kept separate from the intelligence schema above so the
#: boundary between "facts we collected" and "how the website operates" stays visible.
#:
#: Nothing here duplicates intelligence data. Saved opportunities and alerts reference a
#: project by id; they never copy project fields, so a re-ingest cannot leave a user's saved
#: record holding stale or contradictory facts.
APP_SCHEMA = """
-- Public URL identity for a project. Generated from the project's own facts, and stored so
-- a published URL never changes when the underlying row is rewritten by a re-assembly.
CREATE TABLE IF NOT EXISTS project_slug (
    slug               TEXT PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_slug_project ON project_slug(project_id);

-- Minimal account model. No passwords are stored in plain text; see app/auth.py.
CREATE TABLE IF NOT EXISTS app_user (
    id                 INTEGER PRIMARY KEY,
    email              TEXT NOT NULL UNIQUE,
    password_hash      TEXT NOT NULL,
    display_name       TEXT,
    access_level       TEXT NOT NULL DEFAULT 'FREE',
    is_active          INTEGER NOT NULL DEFAULT 1,
    google_id          TEXT,
    company            TEXT,
    role               TEXT,
    onboarding_completed INTEGER NOT NULL DEFAULT 0,
    reset_token        TEXT,
    reset_token_expires_at TEXT,
    created_at         TEXT NOT NULL,
    last_login_at      TEXT
);

-- A saved opportunity stores the *relationship*, never a copy of the project.
CREATE TABLE IF NOT EXISTS saved_opportunity (
    user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    saved_at           TEXT NOT NULL,
    note               TEXT,
    PRIMARY KEY (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_saved_user ON saved_opportunity(user_id, saved_at);

-- One preference row per user. Defaults are applied at creation from the active market and
-- trade, so a new user starts on the product's current configuration rather than a literal.
CREATE TABLE IF NOT EXISTS user_preference (
    user_id            INTEGER PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    market_id          TEXT,
    trade_id           TEXT,
    cities             TEXT,
    project_types      TEXT,
    notify_in_app      INTEGER NOT NULL DEFAULT 1,
    notify_email       INTEGER NOT NULL DEFAULT 0,
    min_value          REAL,
    max_value          REAL,
    updated_at         TEXT NOT NULL
);

-- =====================================================================
-- Change detection and monitoring
-- =====================================================================

-- One row per *detected difference* between two consecutive assembly passes. Written only
-- when a tracked field actually changed value, so the timeline is a record of real events
-- rather than a log of every pipeline run. `previous_value` and `current_value` are stored
-- verbatim so the product can state exactly what changed without recomputing it.
CREATE TABLE IF NOT EXISTS project_change (
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    field_name         TEXT NOT NULL,
    previous_value     TEXT,
    current_value      TEXT,
    change_kind        TEXT NOT NULL,
    summary            TEXT NOT NULL,
    source_id          TEXT,
    source_name        TEXT,
    source_url         TEXT,
    detected_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_project ON project_change(project_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_change_detected ON project_change(detected_at DESC);

-- The previous state of a project, used to diff the next assembly pass against. Kept apart
-- from `project` so the intelligence table itself carries no monitoring bookkeeping.
CREATE TABLE IF NOT EXISTS project_state_snapshot (
    project_id         INTEGER PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
    state_hash         TEXT NOT NULL,
    tracked_values     TEXT NOT NULL,
    captured_at        TEXT NOT NULL
);

-- An opportunity the user has asked the system to monitor. Distinct from a save: a save
-- bookmarks a record, a watch asks for the record to be re-checked for meaningful change.
CREATE TABLE IF NOT EXISTS watched_opportunity (
    user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    watched_at         TEXT NOT NULL,
    last_seen_change_id INTEGER,
    PRIMARY KEY (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_watched_user ON watched_opportunity(user_id, watched_at DESC);

-- The user's own workflow state for an opportunity. This is explicitly *not* a statement
-- about the project's procurement status; the vocabulary is kept apart from
-- `project.procurement_status` so the two can never be read as the same thing.
CREATE TABLE IF NOT EXISTS pipeline_entry (
    user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    stage              TEXT NOT NULL,
    follow_up_date     TEXT,
    assigned_to        INTEGER REFERENCES app_user(id) ON DELETE SET NULL,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_user ON pipeline_entry(user_id, stage, updated_at DESC);

-- Free-text notes a user keeps against an opportunity. User-authored, never merged into the
-- intelligence record and never shown to another account.
CREATE TABLE IF NOT EXISTS opportunity_note (
    id                 INTEGER PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    body               TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_note_user ON opportunity_note(user_id, project_id, created_at DESC);

-- User-applied tags. A tag is the user's own label, so it is stored per user and never
-- treated as a fact about the project.
CREATE TABLE IF NOT EXISTS opportunity_tag (
    user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    tag                TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, project_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_tag_user ON opportunity_tag(user_id, tag);

-- Per-user activity history over an opportunity: when it was saved, watched, moved, noted.
-- Distinct from `project_change`, which records what the *source data* did.
CREATE TABLE IF NOT EXISTS user_activity (
    id                 INTEGER PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    action             TEXT NOT NULL,
    detail             TEXT,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_user ON user_activity(user_id, created_at DESC);

-- Covering indexes for the directory's hot predicates. The public listing always filters on
-- classification and procurement together and orders by permit date, so a composite index lets
-- SQLite satisfy the filter and the order from one structure instead of a scan plus a sort.
CREATE INDEX IF NOT EXISTS idx_project_public_listing
    ON project(classification, procurement_status, permit_date DESC);
CREATE INDEX IF NOT EXISTS idx_project_public_updated
    ON project(classification, procurement_status, updated_at DESC);
-- Supports the value-band filter and the type landing pages without a full scan.
CREATE INDEX IF NOT EXISTS idx_project_type_public
    ON project(project_type, classification, procurement_status);

-- One row per market+trade holding the computed public headline statistics for the last
-- refresh. The application reads every headline figure from here, so routes never run their
-- own COUNT(*) and two pages cannot disagree. `metrics` is a JSON blob so the metric set can
-- grow without a schema migration; `computed_at` and `last_observed` make a figure's as-of
-- time explicit. Declared here (not in a migration) because it is additive and idempotent.
CREATE TABLE IF NOT EXISTS market_stat_snapshot (
    id             INTEGER PRIMARY KEY,
    market_id      TEXT NOT NULL,
    trade_id       TEXT NOT NULL,
    metrics        TEXT NOT NULL,
    computed_at    TEXT NOT NULL,
    last_observed  TEXT,
    UNIQUE (market_id, trade_id)
);

CREATE INDEX IF NOT EXISTS idx_market_stat_snapshot_market
    ON market_stat_snapshot(market_id, trade_id);

-- =====================================================================
-- Organizations and entitlement
-- =====================================================================

-- An organization groups accounts that share work. Membership is the only way one account
-- sees another's private records, and it is granted explicitly rather than inferred.
CREATE TABLE IF NOT EXISTS organization (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    slug               TEXT NOT NULL UNIQUE,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organization_member (
    org_id             INTEGER NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    role               TEXT NOT NULL DEFAULT 'MEMBER',
    created_at         TEXT NOT NULL,
    PRIMARY KEY (org_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_org_member_user ON organization_member(user_id);

-- Plan definitions. Separated from subscriptions and from entitlements: a plan is a
-- catalogue entry, a subscription is an account's declared relationship to a plan, and an
-- entitlement is the set of features that relationship grants. Payment is deliberately not
-- modelled; `subscription` records a status set by an operator or an external system, and no
-- code path can mark an account paid without that record existing.
CREATE TABLE IF NOT EXISTS plan (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    rank               INTEGER NOT NULL DEFAULT 0,
    description        TEXT,
    entitlements       TEXT NOT NULL DEFAULT '[]',
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscription (
    user_id            INTEGER PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    plan_id            TEXT NOT NULL REFERENCES plan(id),
    status             TEXT NOT NULL,
    external_ref       TEXT,
    started_at         TEXT NOT NULL,
    renewed_at         TEXT,
    updated_at         TEXT NOT NULL
);

-- =====================================================================
-- Data quality and error visibility
-- =====================================================================

-- Issues the pipeline observed while ingesting or assembling. Recorded rather than silently
-- repaired: a missing field stays missing and is reported as missing, never filled with a
-- plausible-looking value.
CREATE TABLE IF NOT EXISTS data_quality_issue (
    id                 INTEGER PRIMARY KEY,
    issue_type         TEXT NOT NULL,
    severity           TEXT NOT NULL,
    source_id          TEXT,
    project_id         INTEGER REFERENCES project(id) ON DELETE CASCADE,
    detail             TEXT NOT NULL,
    detected_at        TEXT NOT NULL,
    resolved_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_quality_type ON data_quality_issue(issue_type, severity);
CREATE INDEX IF NOT EXISTS idx_quality_source ON data_quality_issue(source_id);

-- Application-side errors, so a failed request is visible to an operator without reading the
-- process log. Deliberately carries no user id, no email and no request body.
CREATE TABLE IF NOT EXISTS app_error (
    id                 INTEGER PRIMARY KEY,
    path               TEXT NOT NULL,
    method             TEXT NOT NULL,
    status             INTEGER,
    message            TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_error_created ON app_error(created_at DESC);

-- Event-driven alerts are created after the application tables are migrated, because an
-- older deployment may hold a different `alert_event` shape. See `_ensure_alert_event`.

-- Product analytics. Deliberately minimal: an event name, an optional project, and a
-- timestamp. No IP address, no user agent, no free-text payload.
CREATE TABLE IF NOT EXISTS analytics_event (
    id                 INTEGER PRIMARY KEY,
    event_name         TEXT NOT NULL,
    project_id         INTEGER REFERENCES project(id) ON DELETE SET NULL,
    market_id          TEXT,
    trade_id           TEXT,
    created_at         TEXT NOT NULL,
    -- Search diagnostics. Added so the product can answer "what do users search for" and
    -- "which searches return nothing", which the event name alone could not. Still carries no
    -- user id, no IP, and no user agent, so the table cannot be joined back to a person.
    query_text         TEXT,
    result_count       INTEGER,
    filter_summary     TEXT,
    session_id         TEXT,
    campaign           TEXT
);

CREATE INDEX IF NOT EXISTS idx_analytics_event ON analytics_event(event_name, created_at);
-- `idx_analytics_query` is created after the application-table migration, not here: an existing
-- database predates the `query_text` column, and an index statement naming a column that has not
-- been added yet fails the whole schema script. See `_ensure_analytics_indexes`.

-- Full-text index over the searchable project text. A separate table rather than a virtual
-- column on `project`, so the intelligence schema stays untouched and the index can be
-- rebuilt independently. Rows are referenced by project id.
CREATE VIRTUAL TABLE IF NOT EXISTS project_search USING fts5(
    project_id UNINDEXED,
    project_name,
    address,
    city,
    permit_number,
    work_description,
    owner,
    project_type,
    tokenize = 'unicode61'
);
"""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_fingerprint(project_id: int, ev: dict[str, Any]) -> str:
    """Deterministic identity of one evidence observation.

    The fingerprint covers what makes two observations *the same fact*: the project, the
    field, the asserted value, and the source record it came from. A newer record with a
    different value produces a different fingerprint and is appended alongside the old one;
    re-observing the identical fact produces the same fingerprint and only refreshes
    `last_seen_at`. Nothing about the observation is ever rewritten.
    """
    digest = hashlib.sha1(
        "|".join(
            [
                str(project_id),
                str(ev.get("field_name")),
                str(ev.get("value")),
                str(ev.get("source_id")),
                str(ev.get("source_record_key") or ""),
                str(ev.get("source_date") or ""),
                str(ev.get("evidence_type") or ""),
                str(ev.get("tier") if ev.get("tier") is not None else ""),
            ]
        ).encode("utf-8")
    )
    return digest.hexdigest()[:40]


class Database:
    """Thin wrapper around sqlite3 with the operations the pipeline needs."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def init_schema(self) -> None:
        """Create the intelligence schema.

        Deliberately does not create the application tables: the intelligence layer must keep
        working against a database that has never been touched by the web application.
        """
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def init_app_schema(self) -> None:
        """Create the application tables on top of an existing intelligence database.

        Additive and idempotent: every statement is `IF NOT EXISTS`, so this can run against a
        production database that already holds ingested data without altering it.
        """
        self.conn.executescript(APP_SCHEMA)
        self._migrate_app_tables()
        self._ensure_alert_event()
        self._ensure_analytics_indexes()
        self.conn.commit()

    def _ensure_analytics_indexes(self) -> None:
        """Indexes over analytics columns that a migration may have just added.

        The schema script cannot declare these: on an existing database the column does not
        exist until `_migrate_app_tables` adds it, and an index naming an unknown column aborts
        the whole script. Declaring them after the migration keeps both paths — fresh and
        upgraded — on the same index set.
        """
        if not self._table_exists("analytics_event"):
            return
        if "query_text" in self._columns("analytics_event"):
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analytics_query "
                "ON analytics_event(event_name, query_text)"
            )

    def _ensure_alert_event(self) -> None:
        """Create the alert table in its current shape.

        Kept out of `APP_SCHEMA` because an older deployment may already hold `alert_event`
        with a narrower uniqueness constraint and no `change_id` column. Creating the table and
        its indexes here, after the migration step has rebuilt any legacy table, means the
        index statements always apply to the current shape.
        """
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alert_event (
                id                 INTEGER PRIMARY KEY,
                user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                change_id          INTEGER REFERENCES project_change(id) ON DELETE CASCADE,
                kind               TEXT NOT NULL,
                title              TEXT NOT NULL DEFAULT '',
                body               TEXT,
                created_at         TEXT NOT NULL,
                read_at            TEXT,
                email_sent_at      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_alert_user ON alert_event(user_id, read_at);
            -- One alert per detected change per user. Partial indexes are used because SQLite
            -- treats NULLs as distinct, so the change-scoped and match-scoped uniqueness rules
            -- have to be declared separately.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_change
                ON alert_event(user_id, change_id, kind) WHERE change_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_match
                ON alert_event(user_id, project_id, kind) WHERE change_id IS NULL;
            """
        )

    def _migrate_app_tables(self) -> None:
        """Bring an existing application schema forward to the current shape.

        `CREATE TABLE IF NOT EXISTS` does not alter a table that already exists, so a deployed
        database would otherwise miss a newly added column or keep an outdated constraint. Two
        kinds of change are handled, both idempotent:

        * A **column addition**, applied with `ALTER TABLE ... ADD COLUMN`, which preserves the
          existing rows untouched.
        * A **constraint change**, which SQLite cannot express with `ALTER`, so the table is
          rebuilt: renamed aside, recreated from the current definition, rows copied, and the
          old table dropped. This is done inside the caller's transaction so a failure leaves
          the original table in place.
        """
        expected: dict[str, tuple[tuple[str, str], ...]] = {
            "user_preference": (("min_value", "REAL"), ("max_value", "REAL")),
            "app_user": (
                ("google_id", "TEXT"),
                ("company", "TEXT"),
                ("role", "TEXT"),
                ("onboarding_completed", "INTEGER DEFAULT 0"),
                ("reset_token", "TEXT"),
                ("reset_token_expires_at", "TEXT"),
            ),
            "analytics_event": (
                ("query_text", "TEXT"),
                ("result_count", "INTEGER"),
                ("filter_summary", "TEXT"),
                ("session_id", "TEXT"),
                ("campaign", "TEXT"),
            ),
        }
        for table, columns in expected.items():
            if not self._table_exists(table):
                continue
            present = self._columns(table)
            for name, kind in columns:
                if name not in present:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

        # `alert_event` originally carried a `UNIQUE (user_id, project_id, kind)` constraint,
        # which would silently drop a second alert of the same kind on one project. The alert
        # model needs one alert per detected change, so the constraint has to go, and only a
        # rebuild can remove it.
        if self._table_exists("alert_event") and "change_id" not in self._columns("alert_event"):
            self._rebuild_alert_event()

    def _columns(self, table: str) -> set[str]:
        return {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def _rebuild_alert_event(self) -> None:
        """Recreate `alert_event` with the current definition, preserving existing rows.

        Old rows are carried across with a null `change_id`. They remain valid alerts; they
        simply predate change-linking, which is stated rather than fabricated, so the integrity
        check that looks for an underlying event treats a null change as the first-match case.
        """
        self.conn.execute("ALTER TABLE alert_event RENAME TO alert_event_legacy")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alert_event (
                id                 INTEGER PRIMARY KEY,
                user_id            INTEGER NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
                project_id         INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
                change_id          INTEGER REFERENCES project_change(id) ON DELETE CASCADE,
                kind               TEXT NOT NULL,
                title              TEXT NOT NULL DEFAULT '',
                body               TEXT,
                created_at         TEXT NOT NULL,
                read_at            TEXT,
                email_sent_at      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_alert_user ON alert_event(user_id, read_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_change
                ON alert_event(user_id, change_id, kind) WHERE change_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_match
                ON alert_event(user_id, project_id, kind) WHERE change_id IS NULL;
            """
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO alert_event (id, user_id, project_id, change_id, kind, title,
                body, created_at, read_at)
            SELECT id, user_id, project_id, NULL, kind, kind, NULL, created_at, read_at
              FROM alert_event_legacy
            """
        )
        self.conn.execute("DROP TABLE alert_event_legacy")

    def _table_exists(self, name: str) -> bool:
        return bool(
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
        )

    # --- sources and runs -----------------------------------------------------

    def upsert_source(self, cfg: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO source (id, name, publisher, kind, jurisdiction_city, market_coverage,
                                coverage_note, portal_url, reliability, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                publisher = excluded.publisher,
                kind = excluded.kind,
                jurisdiction_city = excluded.jurisdiction_city,
                market_coverage = excluded.market_coverage,
                coverage_note = excluded.coverage_note,
                portal_url = excluded.portal_url,
                reliability = excluded.reliability,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                cfg.id, cfg.name, cfg.publisher, cfg.kind, cfg.jurisdiction_city,
                cfg.market_coverage,
                cfg.coverage_note, cfg.portal_url, cfg.reliability, cfg.notes,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def begin_run(self, source_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO ingest_run (source_id, started_at, status) VALUES (?, ?, ?)",
            (source_id, datetime.now(timezone.utc).isoformat(), "running"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        rows_fetched: int = 0,
        rows_landed: int = 0,
        permits_created: int = 0,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE ingest_run
               SET finished_at = ?, status = ?, rows_fetched = ?, rows_landed = ?,
                   permits_created = ?, error = ?
             WHERE id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(), status, rows_fetched, rows_landed,
                permits_created, error, run_id,
            ),
        )
        self.conn.commit()

    # --- raw landing ----------------------------------------------------------

    def land_raw(
        self, source_id: str, run_id: int, natural_key: str,
        payload: dict[str, Any], payload_hash: str, fetched_at: datetime,
    ) -> bool:
        """Store a verbatim source record. Returns False if already stored."""
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO raw_record
                (source_id, run_id, natural_key, payload, payload_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_id, run_id, natural_key, json.dumps(payload, default=str),
             payload_hash, fetched_at.isoformat()),
        )
        return cur.rowcount > 0

    def commit(self) -> None:
        self.conn.commit()

    # --- permits --------------------------------------------------------------

    def upsert_permit(self, permit: Permit, address_key: str | None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        is_commercial = None if permit.is_commercial is None else int(permit.is_commercial)
        self.conn.execute(
            """
            INSERT INTO permit (source_id, natural_key, permit_number, permit_type,
                permit_subtype, permit_date, status, address, address_key, city, state,
                zip_code, work_description, land_use, specific_use, job_value,
                square_footage, owner, contractor, is_commercial, source_url, source_date,
                updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, natural_key) DO UPDATE SET
                permit_number = excluded.permit_number,
                permit_type = excluded.permit_type,
                permit_subtype = excluded.permit_subtype,
                permit_date = excluded.permit_date,
                status = excluded.status,
                address = excluded.address,
                address_key = excluded.address_key,
                city = excluded.city,
                state = excluded.state,
                zip_code = excluded.zip_code,
                work_description = excluded.work_description,
                land_use = excluded.land_use,
                specific_use = excluded.specific_use,
                job_value = excluded.job_value,
                square_footage = excluded.square_footage,
                owner = excluded.owner,
                contractor = excluded.contractor,
                is_commercial = excluded.is_commercial,
                source_url = excluded.source_url,
                source_date = excluded.source_date,
                updated_at = excluded.updated_at
            """,
            (
                permit.source_id, permit.natural_key, permit.permit_number,
                permit.permit_type, permit.permit_subtype, _iso(permit.permit_date),
                permit.status, permit.address, address_key, permit.city, permit.state,
                permit.zip_code, permit.work_description, permit.land_use,
                permit.specific_use, permit.job_value, permit.square_footage,
                permit.owner, permit.contractor, is_commercial, permit.source_url,
                _iso(permit.source_date), now,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM permit WHERE source_id = ? AND natural_key = ?",
            (permit.source_id, permit.natural_key),
        ).fetchone()
        return int(row["id"])

    def load_permits_for_assembly(self) -> list[tuple[Permit, int]]:
        """Load commercial permits for clustering into projects.

        Residential rows are excluded here, before assembly, so they can never
        contaminate a commercial opportunity.
        """
        rows = self.conn.execute(
            """
            SELECT * FROM permit
             WHERE is_commercial = 1 OR is_commercial IS NULL
             ORDER BY address_key, permit_date
            """
        ).fetchall()
        result: list[tuple[Permit, int]] = []
        for row in rows:
            from .models import parse_date

            permit = Permit(
                source_id=row["source_id"],
                permit_number=row["permit_number"],
                natural_key=row["natural_key"],
                permit_type=row["permit_type"],
                permit_subtype=row["permit_subtype"],
                permit_date=parse_date(row["permit_date"]),
                status=row["status"],
                address=row["address"],
                city=row["city"],
                state=row["state"],
                zip_code=row["zip_code"],
                work_description=row["work_description"],
                land_use=row["land_use"],
                specific_use=row["specific_use"],
                job_value=row["job_value"],
                square_footage=row["square_footage"],
                owner=row["owner"],
                contractor=row["contractor"],
                is_commercial=None if row["is_commercial"] is None else bool(row["is_commercial"]),
                source_url=row["source_url"],
                source_date=parse_date(row["source_date"]),
            )
            result.append((permit, int(row["id"])))
        return result

    # --- projects -------------------------------------------------------------

    def find_project_id(self, project_key: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM project WHERE project_key = ?", (project_key,)
        ).fetchone()
        return int(row["id"]) if row else None

    def upsert_project(self, project: Project) -> int:
        now = datetime.now(timezone.utc).isoformat()
        reasons = json.dumps(project.classification_reasons or [])
        self.conn.execute(
            """
            INSERT INTO project (project_key, trade, project_name, address, city, state,
                project_type, estimated_project_value, square_footage, permit_number,
                permit_date, project_status, owner, developer, general_contractor,
                architect, mechanical_hvac_evidence, source_name, source_url, source_date,
                last_verified, mechanical_evidence_tier, property_class, location_precision,
                procurement_status, classification, classification_score,
                classification_reasons, disputed_fields, discrepancies, created_at,
                updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_key) DO UPDATE SET
                project_name = excluded.project_name,
                address = excluded.address,
                city = excluded.city,
                state = excluded.state,
                project_type = excluded.project_type,
                estimated_project_value = excluded.estimated_project_value,
                square_footage = excluded.square_footage,
                permit_number = excluded.permit_number,
                permit_date = excluded.permit_date,
                project_status = excluded.project_status,
                owner = excluded.owner,
                developer = excluded.developer,
                general_contractor = excluded.general_contractor,
                architect = excluded.architect,
                mechanical_hvac_evidence = excluded.mechanical_hvac_evidence,
                source_name = excluded.source_name,
                source_url = excluded.source_url,
                source_date = excluded.source_date,
                last_verified = excluded.last_verified,
                mechanical_evidence_tier = excluded.mechanical_evidence_tier,
                property_class = excluded.property_class,
                location_precision = excluded.location_precision,
                procurement_status = excluded.procurement_status,
                classification = excluded.classification,
                classification_score = excluded.classification_score,
                classification_reasons = excluded.classification_reasons,
                disputed_fields = excluded.disputed_fields,
                discrepancies = excluded.discrepancies,
                updated_at = excluded.updated_at
            """,
            (
                project.project_key, project.trade, project.project_name, project.address,
                project.city, project.state, project.project_type,
                project.estimated_project_value, project.square_footage,
                project.permit_number, _iso(project.permit_date), project.project_status,
                project.owner, project.developer, project.general_contractor,
                project.architect, project.mechanical_hvac_evidence, project.source_name,
                project.source_url, _iso(project.source_date), _iso(project.last_verified),
                project.mechanical_evidence_tier, project.property_class,
                project.location_precision, project.procurement_status, project.classification,
                project.classification_score, reasons,
                json.dumps(project.disputed_fields or []),
                json.dumps(project.discrepancies or []), now, now,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM project WHERE project_key = ?", (project.project_key,)
        ).fetchone()
        project_id = int(row["id"])

        # Evidence, permit links, and parties are fully rebuilt on each pass so that a
        # corrected source record does not leave stale facts attached to the project.
        self.conn.execute("DELETE FROM evidence WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM project_permit WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM project_party WHERE project_id = ?", (project_id,))

        for ev in project.evidence:
            self._insert_evidence(project_id, ev)
        for party in project.parties:
            self.conn.execute(
                """
                INSERT INTO project_party (project_id, role, name, source_id, source_url, excerpt)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, party.role, party.name, party.source_id,
                 party.source_url, party.excerpt),
            )
        # The `evidence` table above is the current supporting set and is rebuilt each pass.
        # The immutable archive below is append-only: a re-observation refreshes last_seen_at,
        # and a value that changed appends a new row, so the prior fact is never lost.
        seen_at = now
        self.append_evidence_history(project_id, project.evidence, seen_at=seen_at)
        self.record_documents_from_evidence(project_id, project.evidence)
        self.conn.commit()
        return project_id

    def record_documents_from_evidence(self, project_id: int, evidence_rows: Iterable[Any]) -> int:
        """Record the source documents a project's facts were drawn from.

        One document per (source, source record) that contributed evidence. The verbatim
        payload itself stays in `raw_record`; this references it by natural key where one
        exists, so a reader can trace a claim back to the landed record without copying it.
        Returns the number of documents touched.
        """
        seen: set[tuple[str | None, str | None]] = set()
        touched = 0
        for ev in evidence_rows:
            if isinstance(ev, dict):
                source_id = ev.get("source_id")
                record_key = ev.get("source_record_key")
                source_url = ev.get("source_url")
                excerpt = ev.get("excerpt")
                observed_at = ev.get("observed_at")
                source_date = ev.get("source_date")
            else:
                source_id = ev.source_id
                record_key = ev.source_record_key
                source_url = ev.source_url
                excerpt = ev.excerpt
                observed_at = _iso(ev.observed_at)
                source_date = _iso(ev.source_date)
            key = (source_id, record_key)
            if key in seen or not (source_id and record_key):
                continue
            seen.add(key)
            raw = self.conn.execute(
                "SELECT id FROM raw_record WHERE source_id = ? AND natural_key = ? "
                "ORDER BY id DESC LIMIT 1",
                (source_id, record_key),
            ).fetchone()
            self.upsert_document(
                project_id,
                source_id=source_id,
                document_type="permit_record",
                title=excerpt or f"Source record {record_key}",
                source_url=source_url,
                source_record_key=record_key,
                raw_record_id=int(raw["id"]) if raw else None,
                captured_at=_iso(observed_at),
                source_date=_iso(source_date),
            )
            touched += 1
        return touched

    def _insert_evidence(self, project_id: int, ev: Evidence) -> None:
        self.conn.execute(
            """
            INSERT INTO evidence (project_id, field_name, value, source_id, source_name,
                source_url, source_record_key, source_date, observed_at, evidence_type,
                tier, excerpt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id, ev.field_name, ev.value, ev.source_id, ev.source_name,
                ev.source_url, ev.source_record_key, _iso(ev.source_date),
                _iso(ev.observed_at), ev.evidence_type, ev.tier, ev.excerpt,
            ),
        )

    def link_permit(self, project_id: int, permit_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO project_permit (project_id, permit_id) VALUES (?, ?)",
            (project_id, permit_id),
        )

    def record_classification(self, project_id: int, project: Project) -> None:
        """Append a classification decision to the history table.

        Only appended when the decision actually changes, so the history stays a record of
        real transitions rather than a log of every pipeline pass.
        """
        if not project.classification:
            return
        last = self.conn.execute(
            """
            SELECT classification, score FROM project_classification
             WHERE project_id = ? ORDER BY id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if last and last["classification"] == project.classification:
            return
        self.conn.execute(
            """
            INSERT INTO project_classification (project_id, classified_at, classification,
                score, reasons)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_id, datetime.now(timezone.utc).isoformat(),
                project.classification, project.classification_score or 0,
                json.dumps(project.classification_reasons or []),
            ),
        )
        self.conn.commit()

    def record_source_coverage(
        self,
        source_id: str,
        *,
        retrieval_date: datetime,
        pagination_pages: int | None = None,
        pagination_notes: str | None = None,
    ) -> None:
        """Recompute and store coverage metadata for a source from the stored permits.

        Derived from the database rather than from the crawl's own counters, so the figures
        describe what is actually held and cannot drift from the permit table.
        """
        row = self.conn.execute(
            """
            SELECT MIN(permit_date) AS earliest,
                   MAX(CASE WHEN permit_date IS NULL OR permit_date <= ? THEN permit_date END)
                       AS latest,
                   COUNT(*) AS records,
                   SUM(CASE WHEN is_commercial = 1 THEN 1 ELSE 0 END) AS commercial,
                   SUM(CASE WHEN LOWER(COALESCE(permit_type,'')) LIKE '%mechanical%'
                            THEN 1 ELSE 0 END) AS mechanical
              FROM permit WHERE source_id = ?
            """,
            (datetime.now(timezone.utc).date().isoformat(), source_id),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()
        # Preserve a previously recorded pagination count when this call does not supply one.
        # The ingest run knows the page count, but the later assemble run does not, so without
        # this the figure would be erased the moment projects were rebuilt.
        if pagination_pages is None:
            prior = self.conn.execute(
                "SELECT pagination_pages FROM source_coverage WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if prior is not None:
                pagination_pages = prior["pagination_pages"]
        if pagination_notes is None:
            prior_notes = self.conn.execute(
                "SELECT pagination_notes FROM source_coverage WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if prior_notes is not None:
                pagination_notes = prior_notes["pagination_notes"]

        self.conn.execute(
            """
            INSERT INTO source_coverage (source_id, earliest_date, latest_date, retrieval_date,
                record_count, commercial_count, mechanical_count, pagination_pages,
                pagination_notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                earliest_date = excluded.earliest_date,
                latest_date = excluded.latest_date,
                retrieval_date = excluded.retrieval_date,
                record_count = excluded.record_count,
                commercial_count = excluded.commercial_count,
                mechanical_count = excluded.mechanical_count,
                pagination_pages = excluded.pagination_pages,
                pagination_notes = excluded.pagination_notes,
                updated_at = excluded.updated_at
            """,
            (
                source_id,
                row["earliest"], row["latest"], retrieval_date.date().isoformat(),
                row["records"] or 0, row["commercial"] or 0, row["mechanical"] or 0,
                pagination_pages, pagination_notes, now,
            ),
        )
        self.conn.commit()

    # --- change detection -----------------------------------------------------

    def has_app_schema(self) -> bool:
        """Whether the application tables exist.

        The intelligence layer must keep working against a database the web application has
        never touched, so monitoring and data-quality bookkeeping are skipped rather than
        required when those tables are absent.
        """
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_change'"
        ).fetchone()
        return row is not None

    def project_ids(self) -> list[int]:
        """Every project id, for a monitoring pass over the whole dataset."""
        return [int(r["id"]) for r in self.conn.execute("SELECT id FROM project ORDER BY id")]

    def project_row(self, project_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def permits_for_project(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT pm.*, s.name AS source_display_name
              FROM permit pm
              JOIN project_permit pp ON pp.permit_id = pm.id
              LEFT JOIN source s ON s.id = pm.source_id
             WHERE pp.project_id = ?
             ORDER BY pm.permit_date, pm.permit_number
            """,
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def previous_snapshot(self, project_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM project_state_snapshot WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["tracked_values"])
        except (TypeError, ValueError):
            return None

    def save_snapshot(self, project_id: int, values: dict[str, Any], digest: str) -> None:
        self.conn.execute(
            """
            INSERT INTO project_state_snapshot (project_id, state_hash, tracked_values, captured_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                state_hash = excluded.state_hash,
                tracked_values = excluded.tracked_values,
                captured_at = excluded.captured_at
            """,
            (project_id, digest, json.dumps(values, sort_keys=True), _utcnow()),
        )

    def record_changes(self, changes: list[Any]) -> int:
        """Persist detected changes. Returns the number written.

        A change row is only ever written by the detector, so the table is a record of
        observed differences and cannot be seeded with invented events.
        """
        if not changes:
            return 0
        now = _utcnow()
        for change in changes:
            self.conn.execute(
                """
                INSERT INTO project_change (project_id, field_name, previous_value,
                    current_value, change_kind, summary, source_id, source_name, source_url,
                    detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.project_id, change.field_name,
                    None if change.previous_value is None else str(change.previous_value),
                    None if change.current_value is None else str(change.current_value),
                    change.change_kind, change.summary, change.source_id, change.source_name,
                    change.source_url, now,
                ),
            )
        self.conn.commit()
        return len(changes)

    def changes_for_project(self, project_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM project_change
             WHERE project_id = ?
             ORDER BY id DESC
             LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def changes_since(self, since_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT c.*, p.project_name, p.address, p.city
              FROM project_change c
              JOIN project p ON p.id = c.project_id
             WHERE c.id > ?
             ORDER BY c.id
             LIMIT ?
            """,
            (since_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- data quality ---------------------------------------------------------

    def record_quality_issue(
        self, issue_type: str, severity: str, detail: str,
        *, source_id: str | None = None, project_id: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO data_quality_issue (issue_type, severity, source_id, project_id,
                detail, detected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (issue_type, severity, source_id, project_id, detail, _utcnow()),
        )

    def quality_issues(self, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM data_quality_issue
             WHERE resolved_at IS NULL
             ORDER BY
               CASE severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
               detected_at DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_quality_issues(self, issue_type: str | None = None) -> None:
        """Retire open issues at the start of a pass so counts reflect the current state."""
        if issue_type:
            self.conn.execute(
                "DELETE FROM data_quality_issue WHERE issue_type = ?", (issue_type,)
            )
        else:
            self.conn.execute("DELETE FROM data_quality_issue")
        self.conn.commit()

    def record_app_error(self, path: str, method: str, status: int | None, message: str) -> None:
        self.conn.execute(
            """
            INSERT INTO app_error (path, method, status, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (path[:300], method[:10], status, message[:500], _utcnow()),
        )
        self.conn.commit()

    def recent_app_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM app_error ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def source_coverage(self) -> list[dict[str, Any]]:
        """Coverage metadata for every source, joined to its human-readable name."""
        return [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT s.name, s.jurisdiction_city, s.market_coverage, c.*
                  FROM source s
                  LEFT JOIN source_coverage c ON c.source_id = s.id
                 ORDER BY c.record_count DESC NULLS LAST, s.name
                """
            )
        ]

    # --- intelligence graph: evidence history ---------------------------------

    def append_evidence_history(self, project_id: int, rows: Iterable[Any], *, seen_at: str) -> int:
        """Append each distinct evidence observation to the immutable archive.

        Accepts either `Evidence` objects or plain mappings. Returns the number of *new*
        observations. The fingerprint is the identity of the observation, so a re-run that
        observes the same fact updates `last_seen_at` instead of inserting a duplicate, while a
        fact that changes value appends a new row and leaves the prior one in place. Nothing
        here is ever deleted or overwritten.
        """
        created = 0
        for ev in rows:
            row = ev if isinstance(ev, dict) else {
                "field_name": ev.field_name, "value": ev.value, "source_id": ev.source_id,
                "source_name": ev.source_name, "source_url": ev.source_url,
                "source_record_key": ev.source_record_key,
                "source_date": _iso(ev.source_date), "evidence_type": ev.evidence_type,
                "tier": ev.tier, "excerpt": ev.excerpt,
            }
            fingerprint = _evidence_fingerprint(project_id, row)
            cur = self.conn.execute(
                """
                INSERT INTO evidence_history (fingerprint, project_id, field_name, value,
                    source_id, source_name, source_url, source_record_key, source_date,
                    evidence_type, tier, excerpt, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET last_seen_at = excluded.last_seen_at
                """,
                (
                    fingerprint, project_id, row["field_name"], row.get("value"),
                    row["source_id"], row["source_name"], row.get("source_url"),
                    row.get("source_record_key"), row.get("source_date"),
                    row.get("evidence_type"), row.get("tier"), row.get("excerpt"),
                    seen_at, seen_at,
                ),
            )
            created += 1 if cur.rowcount and cur.rowcount > 0 else 0
        return created

    def evidence_history_for(self, project_id: int, *, field_name: str | None = None) -> list[dict[str, Any]]:
        """Every archived observation for a project, newest observation first."""
        sql = "SELECT * FROM evidence_history WHERE project_id = ?"
        params: list[Any] = [project_id]
        if field_name:
            sql += " AND field_name = ?"
            params.append(field_name)
        sql += " ORDER BY first_seen_at DESC, id DESC"
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def evidence_history_count(self, project_id: int) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM evidence_history WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )

    # --- intelligence graph: entities -----------------------------------------

    def upsert_entity(self, *, entity_type: str, canonical_key: str, display_name: str) -> int:
        """Insert or fetch an entity by its deterministic key. Returns its id.

        The display name is refreshed to the most complete observed spelling by the caller;
        this method only guarantees one row per (type, key), which is what stops two spellings
        of one company becoming two entities.
        """
        now = _utcnow()
        self.conn.execute(
            """
            INSERT INTO entity (entity_type, canonical_key, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, canonical_key) DO UPDATE SET
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (entity_type, canonical_key, display_name, now, now),
        )
        row = self.conn.execute(
            "SELECT id FROM entity WHERE entity_type = ? AND canonical_key = ?",
            (entity_type, canonical_key),
        ).fetchone()
        return int(row["id"])

    def add_entity_name(
        self, entity_id: int, *, name: str, normalized_name: str,
        source_id: str | None = None,
    ) -> None:
        """Record an observed spelling of an entity, preserving the variant."""
        self.conn.execute(
            """
            INSERT OR IGNORE INTO entity_name (entity_id, name, normalized_name, source_id, observed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entity_id, name, normalized_name, source_id, _utcnow()),
        )

    def link_entity_project(
        self, entity_id: int, project_id: int, *, role: str,
        source_id: str | None = None, source_url: str | None = None, excerpt: str | None = None,
    ) -> None:
        """Record an entity's evidenced role on a project. Idempotent per (entity, project, role)."""
        self.conn.execute(
            """
            INSERT OR IGNORE INTO entity_project (entity_id, project_id, role, source_id,
                source_url, excerpt, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, project_id, role, source_id, source_url, excerpt, _utcnow()),
        )

    def entities_for_project(self, project_id: int, *, role: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT e.id, e.entity_type, e.display_name, ep.role, ep.source_id, ep.source_url,
                   ep.excerpt
              FROM entity_project ep
              JOIN entity e ON e.id = ep.entity_id
             WHERE ep.project_id = ?
        """
        params: list[Any] = [project_id]
        if role:
            sql += " AND ep.role = ?"
            params.append(role)
        sql += " ORDER BY ep.role, e.display_name"
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def entity_by_key(self, canonical_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM entity WHERE canonical_key = ?", (canonical_key,)
        ).fetchone()
        return dict(row) if row else None

    # --- intelligence graph: locations ----------------------------------------

    def upsert_project_location(self, project_id: int, location: dict[str, Any]) -> None:
        """Store the normalized location for a project.

        Coordinates are written only when the caller supplies a geocode source; the method
        refuses to persist a latitude or longitude without one, so a fabricated coordinate
        cannot be stored.
        """
        lat = location.get("latitude")
        lon = location.get("longitude")
        geocode_source = location.get("geocode_source")
        if (lat is not None or lon is not None) and not geocode_source:
            raise ValueError("Refusing to store coordinates without a geocode provenance.")
        self.conn.execute(
            """
            INSERT INTO project_location (project_id, raw_address, normalized_address,
                building_key, city, state, zip_code, latitude, longitude, geocode_source,
                geocode_confidence, jurisdiction, location_precision, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                raw_address = excluded.raw_address,
                normalized_address = excluded.normalized_address,
                building_key = excluded.building_key,
                city = excluded.city,
                state = excluded.state,
                zip_code = excluded.zip_code,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                geocode_source = excluded.geocode_source,
                geocode_confidence = excluded.geocode_confidence,
                jurisdiction = excluded.jurisdiction,
                location_precision = excluded.location_precision,
                updated_at = excluded.updated_at
            """,
            (
                project_id, location.get("raw_address"), location.get("normalized_address"),
                location.get("building_key"), location.get("city"), location.get("state"),
                location.get("zip_code"), lat, lon, geocode_source,
                location.get("geocode_confidence"), location.get("jurisdiction"),
                location.get("location_precision"), _utcnow(),
            ),
        )

    def location_for_project(self, project_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM project_location WHERE project_id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    # --- intelligence graph: documents ----------------------------------------

    def upsert_document(
        self, project_id: int | None, *, source_id: str | None, document_type: str,
        title: str | None, source_url: str | None, source_record_key: str | None,
        raw_record_id: int | None = None, captured_at: str | None = None,
        source_date: str | None = None,
    ) -> None:
        """Record a source document a project's facts were drawn from."""
        self.conn.execute(
            """
            INSERT INTO document (project_id, source_id, document_type, title, source_url,
                source_record_key, raw_record_id, captured_at, source_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, source_record_key, document_type) DO UPDATE SET
                project_id = COALESCE(excluded.project_id, document.project_id),
                title = excluded.title,
                source_url = excluded.source_url,
                raw_record_id = COALESCE(excluded.raw_record_id, document.raw_record_id),
                source_date = excluded.source_date
            """,
            (
                project_id, source_id, document_type, title, source_url, source_record_key,
                raw_record_id, captured_at or _utcnow(), source_date,
            ),
        )

    def documents_for_project(self, project_id: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM document WHERE project_id = ? ORDER BY captured_at, id",
                (project_id,),
            ).fetchall()
        ]

    # --- intelligence graph: events -------------------------------------------

    def insert_events(self, events: Iterable[dict[str, Any]]) -> int:
        """Append events, ignoring ones already recorded. Returns the number newly inserted.

        `event_uid` is unique, so re-deriving an unchanged fact is a no-op. This is the
        mechanism that guarantees "no change = no new event".
        """
        created = 0
        for event in events:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO project_event (event_uid, project_id, event_type,
                    occurred_at, recorded_at, source_id, source_url, source_record_key,
                    summary, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_uid"], event["project_id"], event["event_type"],
                    event.get("occurred_at"), event["recorded_at"], event.get("source_id"),
                    event.get("source_url"), event.get("source_record_key"),
                    event["summary"], event.get("payload") or "{}",
                ),
            )
            created += 1 if cur.rowcount and cur.rowcount > 0 else 0
        return created

    def events_for_project(self, project_id: int) -> list[dict[str, Any]]:
        """A project's events, oldest first, ordered for a timeline."""
        return [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT * FROM project_event
                 WHERE project_id = ?
                 ORDER BY COALESCE(occurred_at, recorded_at), recorded_at, id
                """,
                (project_id,),
            ).fetchall()
        ]

    def event_count(self, project_id: int) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM project_event WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )

    # --- intelligence graph: trades -------------------------------------------

    def replace_project_trades(self, project_id: int, trades: Iterable[dict[str, Any]]) -> None:
        """Replace a project's classified trades.

        Trades are derived from the current permit text, so they are rebuilt on each pass the
        same way evidence is; unlike evidence, a trade classification is a derived analytical
        label rather than a sourced fact, so rebuilding does not lose history (the history of a
        *fact* lives in evidence_history).
        """
        self.conn.execute("DELETE FROM project_trade WHERE project_id = ?", (project_id,))
        for trade in trades:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO project_trade (project_id, trade_id, is_primary,
                    confidence, evidence_source, classified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, trade["trade_id"], 1 if trade.get("is_primary") else 0,
                    trade.get("confidence") or "documented", trade.get("evidence_source"),
                    _utcnow(),
                ),
            )

    def trades_for_project(self, project_id: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT * FROM project_trade
                 WHERE project_id = ?
                 ORDER BY is_primary DESC, trade_id
                """,
                (project_id,),
            ).fetchall()
        ]

    def trade_counts(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT trade_id, COUNT(*) AS projects,
                       SUM(is_primary) AS primary_projects
                  FROM project_trade
                 GROUP BY trade_id
                 ORDER BY projects DESC, trade_id
                """
            ).fetchall()
        ]

    # --- reporting ------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
            row = self.conn.execute(sql, tuple(params)).fetchone()
            return row[0] if row else None

        by_class = {
            r["classification"]: r["n"]
            for r in self.conn.execute(
                "SELECT classification, COUNT(*) AS n FROM project GROUP BY classification"
            )
        }
        by_city = {
            r["city"]: r["n"]
            for r in self.conn.execute(
                "SELECT city, COUNT(*) AS n FROM project GROUP BY city ORDER BY n DESC LIMIT 15"
            )
        }
        by_source = [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT s.name AS source_name, s.market_coverage, COUNT(DISTINCT p.id) AS permits
                  FROM source s LEFT JOIN permit p ON p.source_id = s.id
                 GROUP BY s.id ORDER BY permits DESC
                """
            )
        ]
        return {
            "projects": scalar("SELECT COUNT(*) FROM project") or 0,
            "permits": scalar("SELECT COUNT(*) FROM permit") or 0,
            "raw_records": scalar("SELECT COUNT(*) FROM raw_record") or 0,
            "evidence_rows": scalar("SELECT COUNT(*) FROM evidence") or 0,
            "with_mechanical_evidence": scalar(
                "SELECT COUNT(*) FROM project WHERE mechanical_hvac_evidence IS NOT NULL"
            ) or 0,
            "by_classification": by_class,
            "by_city": by_city,
            "by_source": by_source,
        }