"""Company & Stakeholder Intelligence for BuildScope.

Turns recorded project stakeholders (contractors, owners, developers, architects, engineers)
into first-class entities with relationship intelligence, project portfolios, geographic
footprints, and recurring partner networks.

Rule: Never fabricate a stakeholder. All entities and metrics are substantiated directly
from verified public permit and project filings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .slugs import slugify


@dataclass
class CompanySummary:
    name: str
    slug: str
    primary_role: str
    project_count: int
    cities: list[str]
    project_types: list[str]
    total_declared_value: float | None
    mechanical_project_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "primary_role": self.primary_role,
            "project_count": self.project_count,
            "cities": self.cities,
            "project_types": self.project_types,
            "total_declared_value": self.total_declared_value,
            "mechanical_project_count": self.mechanical_project_count,
        }


@dataclass
class RecurringPartner:
    name: str
    slug: str
    role: str
    shared_projects: int


@dataclass
class CompanyProfile:
    name: str
    slug: str
    primary_role: str
    roles_observed: list[str]
    project_count: int
    total_declared_value: float | None
    cities: list[str]
    project_types: list[str]
    mechanical_project_count: int
    associated_projects: list[dict[str, Any]]
    recurring_partners: list[RecurringPartner]
    sources_observed: list[str]


#: Role filter aliases a caller may use, mapped to the filter key they select. An unrecognised
#: role selects nothing here and is treated as "no role filter", matching the route's fixed
#: select options; it never widens the result set.
_ROLE_ALIASES: dict[str, str] = {
    "contractor": "contractor",
    "general_contractor": "contractor",
    "gc": "contractor",
    "owner": "owner",
    "property_owner": "owner",
    "architect": "architect",
    "designer": "architect",
    "developer": "developer",
}

#: The stored party roles each filter key selects.
_ROLE_SELECTION: dict[str, tuple[str, ...]] = {
    "contractor": ("general_contractor", "contractor"),
    "owner": ("owner",),
    "architect": ("architect",),
    "developer": ("developer",),
}


def clean_company_name(raw: str | None) -> str | None:
    if not raw:
        return None
    name = raw.strip()
    if len(name) < 2 or name.lower() in ("none", "n/a", "unknown", "null", "owner"):
        return None
    # Remove surrounding quotes or excessive punctuation
    name = re.sub(r"^[\"']|[\"']$", "", name).strip()
    return name or None


class CompanyService:
    """Queries and aggregates company intelligence from authoritative project records."""

    def __init__(self, db: Any):
        if hasattr(db, "conn"):
            self.db = db
        elif hasattr(db, "config") and "APP_CONFIG" in db.config:
            self.db = Database(db.config["APP_CONFIG"].database_path)
        else:
            self.db = db

    def list_companies(
        self,
        *,
        role: str | None = None,
        q: str | None = None,
        city: str | None = None,
        limit: int = 60,
    ) -> list[CompanySummary]:
        """Aggregate stakeholder companies across all assembled commercial projects.

        Each branch of the union carries its own copy of the filters, so its bindings are
        built alongside its own SQL. The previous version built one filter string and
        string-substituted it into every branch, which both duplicated the placeholders and
        rewrote the `role` column name inside unrelated fragments; that is why a filtered
        query failed with a binding-count error.
        """
        role_selection: tuple[str, ...] | None = None
        if role:
            key = _ROLE_ALIASES.get(role.lower().strip())
            if key:
                role_selection = _ROLE_SELECTION[key]

        name_like = f"%{q.strip().lower()}%" if q and q.strip() else None
        city_exact = city.strip().lower() if city and city.strip() else None

        def branch(role_expr: str, name_expr: str) -> tuple[str, list[Any]]:
            """The filter fragment and its bindings for one branch."""
            clauses = [
                "p.classification IN ('HIGH', 'MEDIUM')",
                f"{name_expr} IS NOT NULL",
                f"LENGTH(TRIM({name_expr})) > 1",
                f"LOWER(TRIM({name_expr})) NOT IN ('none', 'n/a', 'unknown', 'null', 'owner')",
            ]
            branch_params: list[Any] = []
            if role_selection is not None:
                placeholders = ", ".join("?" for _ in role_selection)
                clauses.append(f"{role_expr} IN ({placeholders})")
                branch_params.extend(role_selection)
            if name_like is not None:
                clauses.append(f"LOWER({name_expr}) LIKE ?")
                branch_params.append(name_like)
            if city_exact is not None:
                clauses.append("LOWER(p.city) = ?")
                branch_params.append(city_exact)
            return " AND ".join(clauses), branch_params

        party_where, party_params = branch("pp.role", "pp.name")
        gc_where, gc_params = branch("'general_contractor'", "p.general_contractor")
        owner_where, owner_params = branch("'owner'", "p.owner")

        # Union stakeholders from project_party and the direct project role columns. The role
        # and name columns are aliased before the filters apply, so the same filter text is
        # valid in every branch without a substitution step.
        query = f"""
        WITH stakeholder_raw AS (
            SELECT p.id as project_id, pp.role as role, pp.name as name, p.city, p.project_type,
                   p.estimated_project_value, p.mechanical_evidence_tier
              FROM project p
              JOIN project_party pp ON pp.project_id = p.id
             WHERE {party_where}
            UNION ALL
            SELECT p.id as project_id, 'general_contractor' as role, p.general_contractor as name,
                   p.city, p.project_type, p.estimated_project_value, p.mechanical_evidence_tier
              FROM project p
             WHERE {gc_where}
            UNION ALL
            SELECT p.id as project_id, 'owner' as role, p.owner as name,
                   p.city, p.project_type, p.estimated_project_value, p.mechanical_evidence_tier
              FROM project p
             WHERE {owner_where}
        )
        SELECT name,
               role as primary_role,
               COUNT(DISTINCT project_id) as project_count,
               SUM(CASE WHEN mechanical_evidence_tier IN (1, 2) THEN 1 ELSE 0 END) as mechanical_project_count,
               SUM(COALESCE(estimated_project_value, 0)) as total_declared_value,
               GROUP_CONCAT(DISTINCT city) as cities_concat,
               GROUP_CONCAT(DISTINCT project_type) as project_types_concat
          FROM stakeholder_raw
         GROUP BY LOWER(TRIM(name))
        HAVING project_count >= 1
         ORDER BY project_count DESC, total_declared_value DESC
         LIMIT ?
        """
        params: list[Any] = [*party_params, *gc_params, *owner_params, limit]

        rows = self.db.conn.execute(query, tuple(params)).fetchall()
        summaries: list[CompanySummary] = []
        seen_slugs: set[str] = set()

        for r in rows:
            name = clean_company_name(r["name"])
            if not name:
                continue
            base_slug = slugify(name)
            if not base_slug:
                continue
            if base_slug in seen_slugs:
                continue
            seen_slugs.add(base_slug)

            cities = [c.strip() for c in (r["cities_concat"] or "").split(",") if c.strip()]
            types = [t.strip() for t in (r["project_types_concat"] or "").split(",") if t.strip()]
            val = float(r["total_declared_value"]) if r["total_declared_value"] and r["total_declared_value"] > 0 else None

            role_fmt = (r["primary_role"] or "Contractor").replace("_", " ").title()

            summaries.append(
                CompanySummary(
                    name=name,
                    slug=base_slug,
                    primary_role=role_fmt,
                    project_count=int(r["project_count"]),
                    cities=sorted(cities)[:5],
                    project_types=sorted(types)[:4],
                    total_declared_value=val,
                    mechanical_project_count=int(r["mechanical_project_count"]),
                )
            )

        return summaries

    def get_company_profile(self, slug_or_name: str) -> CompanyProfile | None:
        """Retrieve full company intelligence dossier by slug or name."""
        all_companies = self.list_companies(limit=500)
        target = None
        for c in all_companies:
            if c.slug == slug_or_name or c.name.lower() == slug_or_name.lower():
                target = c
                break

        if not target:
            # Fallback direct search
            clean_search = slug_or_name.replace("-", " ").strip()
            direct = self.list_companies(q=clean_search, limit=1)
            if direct:
                target = direct[0]

        if not target:
            return None

        # Fetch all associated projects with details
        query = """
        SELECT DISTINCT p.id, ps.slug, p.project_name, p.address, p.city, p.state,
                        p.project_type, p.classification, p.mechanical_evidence_tier,
                        p.estimated_project_value, p.permit_number, p.permit_date,
                        p.project_status, p.source_name, p.source_url
          FROM project p
          LEFT JOIN project_slug ps ON ps.project_id = p.id
          LEFT JOIN project_party pp ON pp.project_id = p.id
         WHERE (LOWER(TRIM(p.general_contractor)) = LOWER(TRIM(?))
            OR LOWER(TRIM(p.owner)) = LOWER(TRIM(?))
            OR LOWER(TRIM(p.architect)) = LOWER(TRIM(?))
            OR LOWER(TRIM(p.developer)) = LOWER(TRIM(?))
            OR LOWER(TRIM(pp.name)) = LOWER(TRIM(?)))
         ORDER BY p.permit_date DESC NULLS LAST, p.id DESC
         LIMIT 50
        """
        rows = self.db.conn.execute(query, (target.name, target.name, target.name, target.name, target.name)).fetchall()

        projects: list[dict[str, Any]] = []
        project_ids: list[int] = []
        sources: set[str] = set()

        for r in rows:
            pid = int(r["id"])
            project_ids.append(pid)
            if r["source_name"]:
                sources.add(r["source_name"])
            projects.append({
                "id": pid,
                "slug": r["slug"] or str(pid),
                "project_name": r["project_name"] or r["address"] or f"Project #{pid}",
                "address": r["address"],
                "city": r["city"],
                "state": r["state"] or "TX",
                "project_type": r["project_type"],
                "classification": r["classification"],
                "evidence_tier": r["mechanical_evidence_tier"],
                "estimated_value": r["estimated_project_value"],
                "permit_number": r["permit_number"],
                "permit_date": r["permit_date"],
                "status": r["project_status"],
                "source_name": r["source_name"],
            })

        # Calculate recurring partners (other stakeholders on the same projects)
        recurring_partners: list[RecurringPartner] = []
        if project_ids:
            placeholders = ",".join("?" for _ in project_ids)
            partner_query = f"""
            SELECT pp.name, pp.role, COUNT(DISTINCT pp.project_id) as shared_projects
              FROM project_party pp
             WHERE pp.project_id IN ({placeholders})
               AND LOWER(TRIM(pp.name)) != LOWER(TRIM(?))
               AND LENGTH(TRIM(pp.name)) > 1
               AND LOWER(TRIM(pp.name)) NOT IN ('none', 'n/a', 'unknown', 'null', 'owner')
             GROUP BY LOWER(TRIM(pp.name)), pp.role
            HAVING shared_projects >= 1
             ORDER BY shared_projects DESC, pp.name ASC
             LIMIT 10
            """
            p_params = list(project_ids) + [target.name]
            p_rows = self.db.conn.execute(partner_query, tuple(p_params)).fetchall()
            for pr in p_rows:
                p_name = clean_company_name(pr["name"])
                if p_name:
                    recurring_partners.append(
                        RecurringPartner(
                            name=p_name,
                            slug=slugify(p_name),
                            role=(pr["role"] or "Partner").replace("_", " ").title(),
                            shared_projects=int(pr["shared_projects"]),
                        )
                    )

        return CompanyProfile(
            name=target.name,
            slug=target.slug,
            primary_role=target.primary_role,
            roles_observed=[target.primary_role],
            project_count=len(projects),
            total_declared_value=target.total_declared_value,
            cities=target.cities,
            project_types=target.project_types,
            mechanical_project_count=target.mechanical_project_count,
            associated_projects=projects,
            recurring_partners=recurring_partners,
            sources_observed=sorted(sources),
        )
