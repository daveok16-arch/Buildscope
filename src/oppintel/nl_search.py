"""Structured Natural Language Search Interpreter for BuildScope.

Translates plain-language search queries (e.g. "Show me commercial projects in Plano with
mechanical evidence updated in the last 30 days") into grounded, canonical OpportunityFilters.

Key principle: The interpreter NEVER silently invents a filter. All extracted filters are
surfaced explicitly in the UI as 'INTERPRETED FILTERS' alongside verified evidence tiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import MarketConfig, TradeConfig, active_market, active_trade


@dataclass
class InterpretedFilterBadge:
    label: str
    value: str
    param_name: str
    param_value: Any


@dataclass
class InterpretedQuery:
    original_query: str
    clean_keyword: str | None
    extracted_filters: dict[str, Any]
    badges: list[InterpretedFilterBadge] = field(default_factory=list)
    is_structured: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "clean_keyword": self.clean_keyword,
            "extracted_filters": self.extracted_filters,
            "badges": [
                {"label": b.label, "value": b.value, "param_name": b.param_name, "param_value": b.param_value}
                for b in self.badges
            ],
            "is_structured": self.is_structured,
        }


class StructuredSearchInterpreter:
    """Parses natural-language queries into structured parameters."""

    def __init__(self, market: MarketConfig | None = None, trade: TradeConfig | None = None):
        self.market = market or active_market()
        self.trade = trade or active_trade()

    def parse(self, query: str | None) -> InterpretedQuery:
        if not query or not query.strip():
            return InterpretedQuery(
                original_query="",
                clean_keyword=None,
                extracted_filters={},
                badges=[],
                is_structured=False,
            )

        raw = query.strip()
        text = raw.lower()
        extracted: dict[str, Any] = {}
        badges: list[InterpretedFilterBadge] = []
        consumed_spans: list[tuple[int, int]] = []

        # 1. City extraction
        for city_name in self.market.city_names:
            pattern = rf"\b(?:in|at|near|around)?\s*({re.escape(city_name.lower())})\b"
            match = re.search(pattern, text)
            if match:
                extracted["city"] = city_name
                badges.append(
                    InterpretedFilterBadge(
                        label="City",
                        value=city_name,
                        param_name="city",
                        param_value=city_name,
                    )
                )
                consumed_spans.append(match.span())
                break

        # 2. Project type extraction
        project_type_map = {
            "healthcare": ["healthcare", "health care", "hospital", "clinic", "medical"],
            "data_center": ["data center", "datacenter", "data centre"],
            "manufacturing": ["manufacturing", "factory", "assembly plant"],
            "industrial": ["industrial", "warehouse", "logistics", "distribution center"],
            "office": ["office", "office building", "corporate"],
            "retail": ["retail", "restaurant", "store", "shopping center"],
            "hospitality": ["hotel", "motel", "hospitality", "resort"],
            "education": ["school", "education", "university", "college", "campus"],
            "multifamily": ["multifamily", "multi-family", "apartment", "apartments"],
            "commercial": ["commercial project", "commercial building", "commercial"],
        }
        for ptype, aliases in project_type_map.items():
            found = False
            for alias in aliases:
                match = re.search(rf"\b{re.escape(alias)}\b", text)
                if match:
                    # Map to canonical display name if appropriate
                    canonical_name = ptype.replace("_", " ").title() if ptype != "commercial" else "Commercial"
                    extracted["project_type"] = canonical_name
                    badges.append(
                        InterpretedFilterBadge(
                            label="Project Type",
                            value=canonical_name,
                            param_name="project_type",
                            param_value=canonical_name,
                        )
                    )
                    consumed_spans.append(match.span())
                    found = True
                    break
            if found:
                break

        # 3. Trade & Evidence tier extraction
        if re.search(r"\b(tier[\s-]1|direct permit|mechanical permit)\b", text):
            extracted["classification"] = "HIGH"
            extracted["mechanical_only"] = True
            badges.append(
                InterpretedFilterBadge(
                    label="Trade Evidence",
                    value="Tier 1 (Mechanical Permit on File)",
                    param_name="classification",
                    param_value="HIGH",
                )
            )
        elif re.search(r"\b(tier[\s-]2|scope text|mechanical scope)\b", text):
            extracted["classification"] = "MEDIUM"
            extracted["mechanical_only"] = True
            badges.append(
                InterpretedFilterBadge(
                    label="Trade Evidence",
                    value="Tier 2 (Mechanical Scope Named)",
                    param_name="classification",
                    param_value="MEDIUM",
                )
            )
        elif re.search(r"\b(mechanical evidence|hvac evidence|with hvac|with mechanical|mechanical)\b", text):
            extracted["mechanical_only"] = True
            badges.append(
                InterpretedFilterBadge(
                    label="Trade Evidence",
                    value="Documented Mechanical/HVAC",
                    param_name="mechanical_only",
                    param_value=True,
                )
            )

        # 4. Freshness extraction
        days_match = re.search(r"\b(?:in the |past |last )?(\d+)\s*days\b", text)
        if days_match:
            days = int(days_match.group(1))
            extracted["freshness_days"] = days
            badges.append(
                InterpretedFilterBadge(
                    label="Updated",
                    value=f"Last {days} days",
                    param_name="freshness_days",
                    param_value=days,
                )
            )
            consumed_spans.append(days_match.span())
        elif re.search(r"\b(last month|past month|30 days)\b", text):
            extracted["freshness_days"] = 30
            badges.append(
                InterpretedFilterBadge(
                    label="Updated",
                    value="Last 30 days",
                    param_name="freshness_days",
                    param_value=30,
                )
            )
        elif re.search(r"\b(last week|past week|7 days)\b", text):
            extracted["freshness_days"] = 7
            badges.append(
                InterpretedFilterBadge(
                    label="Updated",
                    value="Last 7 days",
                    param_name="freshness_days",
                    param_value=7,
                )
            )

        # 5. Value thresholds extraction
        val_match = re.search(r"\b(?:over|above|min|minimum|greater than)\s*\$?(\d+(?:\.\d+)?)\s*(m|million|k|thousand)?\b", text)
        if val_match:
            amount = float(val_match.group(1))
            multiplier = (val_match.group(2) or "").lower()
            if multiplier in ("m", "million"):
                amount *= 1_000_000
            elif multiplier in ("k", "thousand"):
                amount *= 1_000
            extracted["min_value"] = amount
            badges.append(
                InterpretedFilterBadge(
                    label="Est. Value",
                    value=f">= ${amount:,.0f}",
                    param_name="min_value",
                    param_value=amount,
                )
            )
            consumed_spans.append(val_match.span())

        # Clean residual search terms for full-text keyword
        clean_text = text
        # Remove common filler phrases
        fillers = [
            r"\bshow me\b",
            r"\bfind\b",
            r"\blook for\b",
            r"\bprojects\b",
            r"\bopportunities\b",
            r"\bpermits\b",
            r"\bwith\b",
            r"\bin\b",
            r"\bupdated\b",
            r"\brecently\b",
        ]
        for filler in fillers:
            clean_text = re.sub(filler, " ", clean_text)

        # Remove consumed words
        for start, end in sorted(consumed_spans, reverse=True):
            clean_text = clean_text[:start] + " " + clean_text[end:]

        clean_kw = " ".join(clean_text.split()).strip()
        # If what remains is just punctuation or very short filler, drop it
        if len(clean_kw) < 2 or clean_kw in ("all", "any", "the"):
            clean_kw = None

        is_structured = len(badges) > 0
        return InterpretedQuery(
            original_query=raw,
            clean_keyword=clean_kw,
            extracted_filters=extracted,
            badges=badges,
            is_structured=is_structured,
        )
