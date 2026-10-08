# BuildScope Production Data Architecture: PostgreSQL + PostGIS Migration Guide

**Product:** BUILD SCOPE — Construction Opportunity Intelligence  
**Document:** Production Architecture Blueprint & Database Migration Path  
**Status:** Canonical Target Architecture (PostgreSQL 16 + PostGIS 3.4)

---

## 1. Architectural Blueprint & Rationale

BuildScope requires high-concurrency commercial construction intelligence with spatial territory analysis, immutable evidence provenance, event-driven auditing, and multi-tenant organization workspaces.

While SQLite serves local prototyping with zero-ops reliability, the production target is **PostgreSQL + PostGIS + Object Storage + Asynchronous Event Outbox**.

### Core Components and Rationale

| Component | Technology | Rationale in BuildScope |
|---|---|---|
| **Authoritative Store** | PostgreSQL 16 | Relational ACID, concurrent row-level locking, JSONB for immutable raw payload auditing, foreign key cascade integrity. |
| **Geospatial Engine** | PostGIS 3.4 | True spatial indexing (GiST), radial contractor territory searches (`ST_DWithin`), municipal boundary checks (`ST_Contains`), and spatial clustering (`ST_ClusterDBSCAN`). |
| **Raw Evidence Store** | S3 / GCS Object Storage + PostgreSQL metadata | Immutable storage of municipal PDF filings, plans, and large JSON payloads with SHA-256 integrity verification. |
| **Event Outbox** | Transactional Outbox in PostgreSQL | Guarantees at-least-once asynchronous event delivery (alerts, webhooks, search indexing) without distributed 2PC transactions. |
| **Search Projection** | PostgreSQL Full-Text (tsvector/GIN) -> OpenSearch | Native English search on project title, work description, and owner; easily projected to OpenSearch when scale exceeds 10M records. |
| **Cache Layer** | Redis 7 | Session storage, rate-limiting counters, and materialized market stats caching. |

---

## 2. Schema Entity Mapping

| # | Existing SQLite Entity | Target PostgreSQL Entity | Spatial / JSONB Capabilities |
|---|---|---|---|
| 1 | `source` | `sources` | Source reliability, connector kind, refresh cron, parser version |
| 2 | `ingest_run` | `ingest_runs` | Ingestion metrics, error traces, duration tracking |
| 3 | `raw_record` | `source_records` | `payload` as `JSONB`, SHA-256 `content_hash`, immutable partition |
| 4 | `source_coverage` | `source_coverage` | Temporal windows, observed dates, pagination state |
| 5 | `permit` | `canonical_permits` | Normalized permit fields, `coordinates geometry(Point, 4326)` |
| 6 | `project` | `projects` | `location geometry(Point, 4326)`, classification score, trade |
| 7 | `project_permit` | `project_source_records` | Junction linking normalized permits to resolved project identities |
| 8 | `evidence` | `project_evidence` | Per-field evidence, tier (1=direct permit, 2=scope text), excerpt |
| 9 | `project_party` | `project_stakeholders` | Stakeholder roles (GC, owner, architect, engineer, applicant) |
| 10 | *(derived)* | `companies` | First-class entity for contractors, owners, developers, architects |
| 11 | *(derived)* | `company_relationships`| Observed co-occurrence graph between contractors, owners, and architects |
| 12 | `project_classification`| `classifications` | Audit log of scoring rules, points, and gate validations |
| 13 | *(derived)* | `procurement_states` | Formal bid/procurement history (maintains strict separation from workflow) |
| 14 | `project_change` | `project_changes` | Field-level diffs between consecutive assembly passes |
| 15 | `project_state_snapshot` | `project_snapshots` | State hashes for zero-false-positive change detection |
| 16 | `project_slug` | `project_slugs` | Permanent URL routing identities with redirect history |
| 17 | `app_user` | `accounts` | Scrypt/Argon2 password hashes, MFA, role-based access control |
| 18 | `organization` | `organizations` | Multi-user team workspace root |
| 19 | `organization_member` | `organization_members` | Org roles: OWNER, ADMIN, ESTIMATOR, MEMBER |
| 20 | `plan` | `plans` | Commercial subscription tiers (Starter, Pro, Enterprise, Operator) |
| 21 | `subscription` | `subscriptions` | Active subscription state, seat count, renewal terms |
| 22 | *(derived)* | `entitlements` | Specific feature gates (API access, territory alerts, export) |
| 23 | `user_preference` | `saved_searches` | Saved search criteria, automated territory watch boundaries |
| 24 | `saved_opportunity` | `saved_projects` | User bookmarks (distinct from monitored projects) |
| 25 | `watched_opportunity`| `watched_projects` | Active monitoring registrations driving event notifications |
| 26 | `pipeline_entry` | `pipeline_entries` | Contractor's internal CRM pursuit stage (Target, Bidding, Won) |
| 27 | `opportunity_note` | `project_notes` | Private internal team notes with revision timestamps |
| 28 | `alert_event` / `alert`| `alerts` | In-app and email notifications directly linked to verified events |
| 29 | *(new)* | `intelligence_events` | Formal event model log (ProjectCreated, EvidenceAdded, etc.) |
| 30 | `analytics_event` | `analytics_events` | Anonymized funnel navigation and discovery telemetry |

---

## 3. Production PostgreSQL 16 + PostGIS DDL

```sql
-- Enable PostGIS & Full-Text Search Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

-- ============================================================================
-- 1. SOURCES & RAW DATA INGESTION
-- ============================================================================

CREATE TABLE sources (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    publisher VARCHAR(255),
    kind VARCHAR(32) NOT NULL, -- 'arcgis', 'socrata', 'accela', 'html'
    jurisdiction_city VARCHAR(128),
    jurisdiction_state VARCHAR(2) NOT NULL DEFAULT 'TX',
    market_coverage VARCHAR(32) NOT NULL DEFAULT 'current',
    coverage_note TEXT,
    portal_url TEXT,
    base_url TEXT,
    reliability NUMERIC(3, 2) NOT NULL DEFAULT 0.90,
    parser_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    refresh_cron VARCHAR(64) DEFAULT '0 */6 * * *',
    last_successful_fetch TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE source_records (
    id BIGSERIAL PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    natural_key VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_url TEXT,
    CONSTRAINT uq_source_record UNIQUE (source_id, natural_key, payload_hash)
);

CREATE INDEX idx_source_records_lookup ON source_records(source_id, natural_key);
CREATE INDEX idx_source_records_hash ON source_records(payload_hash);
CREATE INDEX idx_source_records_payload_gin ON source_records USING gin (payload);

-- ============================================================================
-- 2. CANONICAL PROJECTS & POSTGIS SPATIAL ENTITIES
-- ============================================================================

CREATE TABLE projects (
    id BIGSERIAL PRIMARY KEY,
    project_key VARCHAR(128) NOT NULL UNIQUE,
    trade VARCHAR(64) NOT NULL DEFAULT 'commercial_hvac',
    project_name VARCHAR(255),
    address VARCHAR(255),
    city VARCHAR(128),
    state VARCHAR(2) NOT NULL DEFAULT 'TX',
    zip_code VARCHAR(10),
    location GEOMETRY(Point, 4326), -- PostGIS Point: longitude, latitude
    project_type VARCHAR(64),
    property_class VARCHAR(64),
    estimated_project_value NUMERIC(14, 2),
    square_footage NUMERIC(10, 2),
    permit_number VARCHAR(128),
    permit_date DATE,
    project_status VARCHAR(64),
    owner VARCHAR(255),
    developer VARCHAR(255),
    general_contractor VARCHAR(255),
    architect VARCHAR(255),
    mechanical_hvac_evidence TEXT,
    mechanical_evidence_tier SMALLINT, -- 1 = Direct permit, 2 = Scope text, NULL = Base
    procurement_status VARCHAR(64) NOT NULL DEFAULT 'Not verified',
    classification VARCHAR(16) NOT NULL DEFAULT 'LOW',
    classification_score INTEGER NOT NULL DEFAULT 0,
    classification_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    discrepancies JSONB NOT NULL DEFAULT '[]'::jsonb,
    disputed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_name VARCHAR(255),
    source_url TEXT,
    source_date DATE,
    last_verified TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    search_vector TSVECTOR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- PostGIS Spatial Index for lightning-fast radius searches
CREATE INDEX idx_projects_location_gist ON projects USING GIST (location);

-- B-Tree indexes for fast filtered discovery
CREATE INDEX idx_projects_classification ON projects (classification, procurement_status, permit_date DESC);
CREATE INDEX idx_projects_city ON projects (city);
CREATE INDEX idx_projects_project_type ON projects (project_type);
CREATE INDEX idx_projects_estimated_value ON projects (estimated_project_value);

-- Full-Text Search GIN index
CREATE INDEX idx_projects_search_gin ON projects USING GIN (search_vector);

-- Trigger to maintain search_vector automatically
CREATE OR REPLACE FUNCTION update_project_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.project_name, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.address, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.city, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.general_contractor, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.owner, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.mechanical_hvac_evidence, '')), 'C') ||
        setweight(to_tsvector('english', COALESCE(NEW.project_type, '')), 'C');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_project_search_vector
BEFORE INSERT OR UPDATE ON projects
FOR EACH ROW EXECUTE FUNCTION update_project_search_vector();

-- ============================================================================
-- 3. PER-FIELD EVIDENCE PROVENANCE & STAKEHOLDERS
-- ============================================================================

CREATE TABLE project_evidence (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field_name VARCHAR(64) NOT NULL,
    value TEXT,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(id),
    source_name VARCHAR(255) NOT NULL,
    source_url TEXT,
    source_record_key VARCHAR(128),
    source_date DATE,
    evidence_type VARCHAR(32) NOT NULL DEFAULT 'permit',
    tier SMALLINT,
    excerpt TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_evidence_project_field ON project_evidence (project_id, field_name);

CREATE TABLE companies (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL UNIQUE,
    canonical_role VARCHAR(64),
    primary_city VARCHAR(128),
    website TEXT,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_companies_name_trgm ON companies USING gin (name gin_trgm_ops);

CREATE TABLE project_stakeholders (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    company_id BIGINT REFERENCES companies(id) ON DELETE SET NULL,
    role VARCHAR(64) NOT NULL, -- 'owner', 'developer', 'architect', 'general_contractor', 'engineer'
    raw_name VARCHAR(255) NOT NULL,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(id),
    source_url TEXT,
    excerpt TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stakeholders_project ON project_stakeholders (project_id, role);
CREATE INDEX idx_stakeholders_company ON project_stakeholders (company_id);

-- ============================================================================
-- 4. FORMAL EVENT MODEL & TRANSACTIONAL OUTBOX
-- ============================================================================

CREATE TABLE intelligence_events (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL UNIQUE,
    event_type VARCHAR(64) NOT NULL,
    project_id BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    source_id VARCHAR(64) REFERENCES sources(id) ON DELETE SET NULL,
    source_record_key VARCHAR(128),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_hash CHAR(64) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ
);

CREATE INDEX idx_events_project ON intelligence_events (project_id, occurred_at DESC);
CREATE INDEX idx_events_unpublished ON intelligence_events (id) WHERE published = FALSE;

-- ============================================================================
-- 5. WORKSPACE, PIPELINE & ALERTS
-- ============================================================================

CREATE TABLE accounts (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(255),
    access_level VARCHAR(32) NOT NULL DEFAULT 'FREE', -- 'FREE', 'PRO', 'OPERATOR'
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE watched_projects (
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    watched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_change_id BIGINT,
    PRIMARY KEY (account_id, project_id)
);

CREATE TABLE pipeline_entries (
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage VARCHAR(32) NOT NULL, -- 'New', 'Reviewing', 'Target', 'Contacted', 'Pursuing', 'Closed out'
    follow_up_date DATE,
    assigned_to BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, project_id)
);

CREATE TABLE alerts (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_id BIGINT REFERENCES intelligence_events(id) ON DELETE CASCADE,
    alert_kind VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    body TEXT,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_alerts_account_unread ON alerts (account_id, is_read, created_at DESC);
```

---

## 4. PostGIS Spatial Queries in BuildScope

### A. Radial Contractor Territory Search (e.g. 15-Mile Radius from Plano Office)

```sql
-- Find high-signal projects within 15 miles (24,140 meters) of 7800 Preston Rd, Plano (-96.804, 33.085)
SELECT p.id, p.project_name, p.address, p.city, p.classification,
       p.mechanical_evidence_tier,
       ROUND((ST_Distance(p.location::geography, ST_SetSRID(ST_MakePoint(-96.804, 33.085), 4326)::geography) / 1609.34)::numeric, 1) AS distance_miles
  FROM projects p
 WHERE p.classification IN ('HIGH', 'MEDIUM')
   AND ST_DWithin(
       p.location::geography,
       ST_SetSRID(ST_MakePoint(-96.804, 33.085), 4326)::geography,
       24140 -- 15 miles in meters
   )
 ORDER BY distance_miles ASC
 LIMIT 25;
```

### B. Jurisdiction Territory Verification

```sql
-- Verify if a project point falls cleanly inside the Fort Worth municipal boundary polygon
SELECT p.id, p.address, j.name AS verified_jurisdiction
  FROM projects p
  JOIN jurisdictions j ON ST_Contains(j.boundary, p.location)
 WHERE p.city = 'Fort Worth';
```

---

## 5. Phased Migration Plan

1. **Phase 1: Dual Connection Layer**  
   Introduce SQLAlchemy or psycopg3 engine in `db.py` that switches based on `DATABASE_URL`.
2. **Phase 2: Historical ETL Backfill**  
   Run a one-time streaming script `python -m oppintel.cli migrate-to-postgres` that transfers raw records, permits, assembled projects, evidence, and slug history.
3. **Phase 3: Geocoding Enrichment**  
   Populate `location` geometry columns from municipal address endpoints or MapServer coordinate layers.
4. **Phase 4: Read/Write Split Cutover**  
   Direct live ingestion writes to PostgreSQL; point Flask read queries to PostgreSQL.
5. **Phase 5: Outbox Worker Activation**  
   Run background Celery / RQ worker to consume `intelligence_events` and push instant notifications.
