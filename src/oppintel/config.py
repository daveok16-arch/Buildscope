"""Configuration loading for markets, sources and trade profiles.

Nothing in the application hardcodes Dallas–Fort Worth or HVAC. The active market and trade
are resolved from `config/markets.yaml` and `config/trades.yaml`, so a new market or trade is
a configuration change rather than an application rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"


@dataclass
class SourceConfig:
    id: str
    name: str
    publisher: str
    kind: str
    enabled: bool = True
    market_coverage: str = "unknown"
    coverage_note: str | None = None
    base_url: str | None = None
    domain: str | None = None
    dataset_id: str | None = None
    portal_url: str | None = None
    source_url_template: str | None = None
    jurisdiction_city: str | None = None
    jurisdiction_state: str = "TX"
    reliability: float = 0.8
    notes: str = ""
    #: Lower bound for ingestion. `since` is an explicit ISO date; `since_months` is a rolling
    #: window measured back from today. Precedence: CLI `--since` > `since` > `since_months` >
    #: None (all history). All three current sources filter by date at the source, so the bound
    #: cuts the download as well as the stored rows.
    since: str | None = None
    since_months: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceConfig:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def is_current(self) -> bool:
        return self.market_coverage == "current"

    def resolved_since(self, today: date | None = None) -> date | None:
        """The configured lower bound as a date, or None for all history.

        Precedence: an explicit `since` wins; otherwise `since_months` is measured back from
        `today`. Sub-day units are not supported, so 1 month means "same day, previous month".
        """
        if self.since:
            return date.fromisoformat(self.since)
        if self.since_months:
            today = today or date.today()
            month_index = today.month - 1 - int(self.since_months)
            year = today.year + month_index // 12
            month = month_index % 12 + 1
            day = min(today.day, _days_in_month(year, month))
            return date(year, month, day)
        return None


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day


@dataclass
class CityConfig:
    """A city inside a market. `slug` addresses it in URLs; `name` matches source values.

    `aliases` are alternative spellings the sources publish for the same city (for example
    "Mckinney" for "McKinney"). They are configuration, not code, so a new source spelling is a
    config edit. Matching stays exact (case-insensitive) against the name or an alias: no fuzzy
    matching, because a wrong merge is worse than a separate row.
    """

    slug: str
    name: str
    aliases: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CityConfig:
        return cls(
            slug=str(data["slug"]),
            name=str(data["name"]),
            aliases=[str(a) for a in data.get("aliases") or []],
        )

    def matches(self, value: str | None) -> bool:
        """True when a source's city value is this city, by name or a declared alias."""
        if not value:
            return False
        wanted = value.strip().lower()
        return wanted == self.name.strip().lower() or any(
            wanted == a.strip().lower() for a in self.aliases
        )


@dataclass
class MarketConfig:
    """A geographic market: a set of cities served by a set of sources and trades."""

    id: str
    slug: str
    name: str
    short_name: str
    state: str = "TX"
    active: bool = False
    trades: list[str] = field(default_factory=list)
    cities: list[CityConfig] = field(default_factory=list)
    landing_pages: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    seo: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketConfig:
        return cls(
            id=str(data["id"]),
            slug=str(data["slug"]),
            name=str(data["name"]),
            short_name=str(data.get("short_name") or data["name"]),
            state=str(data.get("state") or "TX"),
            active=bool(data.get("active", False)),
            trades=list(data.get("trades") or []),
            cities=[CityConfig.from_dict(c) for c in data.get("cities") or []],
            landing_pages=list(data.get("landing_pages") or []),
            sources=list(data.get("sources") or []),
            seo=dict(data.get("seo") or {}),
        )

    @property
    def city_names(self) -> list[str]:
        """City names as the permit sources record them, used for filtering."""
        return [c.name for c in self.cities]

    def city_slug(self, name: str | None) -> str | None:
        """Reverse lookup: a source's city value to its URL slug, honouring aliases."""
        if not name:
            return None
        for city in self.cities:
            if city.matches(name):
                return city.slug
        return None

    def city_name(self, slug: str | None) -> str | None:
        if not slug:
            return None
        wanted = slug.strip().lower()
        for city in self.cities:
            if city.slug.lower() == wanted:
                return city.name
        return None


def type_slug(project_type: str | None) -> str:
    """A URL slug for a project-type landing page.

    Kept here rather than in the application layer so a page URL and the value it filters on
    are derived by one rule. Deterministic and lossy on purpose: the slug addresses a page, and
    the page resolves the name back from the database, so a collision would surface as a page
    with two names rather than as a silent mis-filter.
    """
    if not project_type:
        return ""
    lowered = project_type.strip().lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


@dataclass
class TradeConfig:
    id: str
    label: str
    active: bool
    market: dict[str, Any]
    slug: str = ""
    short_label: str = ""
    #: How the application layer scopes public discovery to this trade. Declares which stored
    #: field evidences the trade, so an HVAC directory cannot list a project with no
    #: mechanical activity. The classification gates are unaffected by this setting.
    discovery: dict[str, Any] = field(default_factory=dict)
    seo: dict[str, Any] = field(default_factory=dict)
    mechanical_permit_type_keywords: list[str] = field(default_factory=list)
    mechanical_scope_keywords: list[str] = field(default_factory=list)
    construction_activity_keywords: list[str] = field(default_factory=list)
    property_classes: list[dict[str, Any]] = field(default_factory=list)
    scoring: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    active_status_keywords: list[str] = field(default_factory=list)
    inactive_status_keywords: list[str] = field(default_factory=list)
    residential_exclusion_keywords: list[str] = field(default_factory=list)

    @property
    def evidence_field(self) -> str | None:
        """The stored column that evidences this trade, or None when undeclared."""
        value = (self.discovery or {}).get("evidence_field")
        if not value:
            return None
        text = str(value)
        # The column name cannot be bound as a query parameter, so it is validated before it is
        # ever interpolated into SQL. This is the only identifier that reaches a query.
        if not re.fullmatch(r"[a-z_]+", text):
            raise ValueError(f"Invalid discovery evidence_field: {text!r}")
        return text

    @property
    def evidence_values(self) -> list[Any]:
        return list((self.discovery or {}).get("evidence_values") or [])

    @property
    def strong_evidence_value(self) -> Any:
        """The value denoting the strongest evidence tier, used for headline statistics."""
        return (self.discovery or {}).get("strong_evidence_value")

    def evidence_clause(self, alias: str = "p") -> tuple[str | None, list[Any]]:
        """SQL predicate selecting rows that carry this trade's evidence.

        Centralised so every layer — service, reports, sitemap — scopes by the same rule. A
        literal column name in any one of those places would be a hardcoded trade.
        """
        field = self.evidence_field
        values = self.evidence_values
        if not field or not values:
            return None, []
        placeholders = ",".join("?" for _ in values)
        return f"{alias}.{field} IN ({placeholders})", list(values)

    def strong_evidence_clause(self, alias: str = "p") -> tuple[str | None, list[Any]]:
        """SQL predicate selecting rows at the strongest evidence tier."""
        field = self.evidence_field
        value = self.strong_evidence_value
        if not field or value is None:
            return None, []
        return f"{alias}.{field} = ?", [value]

    def property_class_for(self, text: str) -> dict[str, Any] | None:
        """Return the highest-scoring property class matching the given text.

        Longer keyword matches win so that "assisted living" is preferred over the bare
        "living" style of accidental substring hit, and the most specific class is used.
        """
        if not text:
            return None
        haystack = text.lower()
        best: dict[str, Any] | None = None
        best_len = 0
        for klass in self.property_classes:
            for keyword in klass.get("keywords", []):
                if keyword in haystack and len(keyword) > best_len:
                    best = klass
                    best_len = len(keyword)
        return best


@lru_cache(maxsize=1)
def load_sources(path: Path | None = None) -> dict[str, SourceConfig]:
    path = path or (CONFIG_DIR / "sources.yaml")
    raw = yaml.safe_load(path.read_text())
    return {s["id"]: SourceConfig.from_dict(s) for s in raw["sources"]}


def ingestion_window_since(
    sources: dict[str, SourceConfig] | None = None, today: date | None = None
) -> date | None:
    """The earliest date any enabled source ingests from, or None for all history.

    This is the one place the "since" a disclosure line shows is derived: each enabled source
    resolves its own `since`/`since_months` bound (``SourceConfig.resolved_since``) and the
    earliest of those is the collection window the site actually holds. A source with no bound
    (all history) makes the result None, because the dataset then has no single lower bound to
    state. Config-driven, so a source added or re-scoped changes the stated date with no code
    change.
    """
    sources = sources if sources is not None else load_sources()
    bounds: list[date] = []
    for source in sources.values():
        if not source.enabled:
            continue
        bound = source.resolved_since(today)
        if bound is None:
            return None
        bounds.append(bound)
    return min(bounds) if bounds else None


@lru_cache(maxsize=1)
def load_retention(path: Path | None = None) -> "RetentionSettings":
    """The raw-archive retention policy from `config/sources.yaml`.

    Absent or partial configuration is "do nothing": the dataclass defaults keep every file, so
    a missing block can never cause a delete.
    """
    from .retention import RetentionSettings

    path = path or (CONFIG_DIR / "sources.yaml")
    raw = yaml.safe_load(path.read_text()) or {}
    block = raw.get("retention") or {}
    return RetentionSettings(
        enabled=bool(block.get("enabled", False)),
        keep_last=block.get("keep_last"),
        keep_days=block.get("keep_days"),
        gzip_after_days=block.get("gzip_after_days"),
    )


@lru_cache(maxsize=1)
def load_trades(path: Path | None = None) -> dict[str, TradeConfig]:
    path = path or (CONFIG_DIR / "trades.yaml")
    raw = yaml.safe_load(path.read_text())
    trades: dict[str, TradeConfig] = {}
    for trade_id, data in raw["trades"].items():
        known = {f for f in TradeConfig.__dataclass_fields__}
        trades[trade_id] = TradeConfig(
            id=trade_id, **{k: v for k, v in data.items() if k in known}
        )
    return trades


def active_trade() -> TradeConfig:
    for trade in load_trades().values():
        if trade.active:
            return trade
    raise RuntimeError("No active trade profile found in config/trades.yaml")


# --- trade taxonomy -----------------------------------------------------------


@dataclass
class TradeTaxonomyEntry:
    """One trade in the controlled vocabulary.

    Distinct from `TradeConfig`: a `TradeConfig` is a full intelligence profile (scoring,
    thresholds, market) for the trade the product actively serves, whereas a taxonomy entry
    is only the label and the keywords used to recognise the trade on a record. The taxonomy
    lets a project carry any trade without a schema change, and `active_profile` links an
    entry to its deeper profile where one exists.
    """

    id: str
    label: str
    slug: str
    keywords: tuple[str, ...] = ()
    active_profile: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TradeTaxonomyEntry:
        return cls(
            id=str(data["id"]),
            label=str(data.get("label") or data["id"]),
            slug=str(data.get("slug") or data["id"]),
            keywords=tuple(str(k).lower() for k in data.get("keywords") or ()),
            active_profile=data.get("active_profile"),
        )


@lru_cache(maxsize=1)
def load_trade_taxonomy(path: Path | None = None) -> tuple[TradeTaxonomyEntry, ...]:
    """Load the trade taxonomy from configuration, in declared order."""
    path = path or (CONFIG_DIR / "trade_taxonomy.yaml")
    raw = yaml.safe_load(path.read_text()) or {}
    return tuple(TradeTaxonomyEntry.from_dict(t) for t in raw.get("trades") or [])


def classify_trade(text: str | None, *, taxonomy: tuple[TradeTaxonomyEntry, ...] | None = None) -> str | None:
    """The taxonomy trade id that best matches the given text, or None.

    The longest matching keyword wins, so "fire protection" beats a stray "fire", and the
    match is whole-word so "electric" does not fire on "electrical" twice over. A record whose
    text names no trade keeps a null trade rather than a default: an unknown trade is an
    honest gap, not a value to invent.
    """
    if not text or not text.strip():
        return None
    entries = taxonomy if taxonomy is not None else load_trade_taxonomy()
    haystack = text.lower()
    best: str | None = None
    best_len = 0
    for entry in entries:
        for keyword in entry.keywords:
            if len(keyword) <= best_len:
                continue
            if re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", haystack):
                best = entry.id
                best_len = len(keyword)
    return best


# --- markets ------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_markets(path: Path | None = None) -> dict[str, MarketConfig]:
    """Load every configured market, keyed by id."""
    path = path or (CONFIG_DIR / "markets.yaml")
    raw = yaml.safe_load(path.read_text())
    return {
        m["id"]: MarketConfig.from_dict(m) for m in raw.get("markets") or []
    }


@lru_cache(maxsize=1)
def _active_market_id(path: Path | None = None) -> str | None:
    path = path or (CONFIG_DIR / "markets.yaml")
    raw = yaml.safe_load(path.read_text())
    return (raw.get("defaults") or {}).get("active_market")


def active_market() -> MarketConfig:
    """The market the application serves by default.

    Resolved from configuration rather than hardcoded, so switching markets is a config change.
    """
    markets = load_markets()
    configured = _active_market_id()
    if configured and configured in markets:
        return markets[configured]
    for market in markets.values():
        if market.active:
            return market
    raise RuntimeError("No active market found in config/markets.yaml")


def market_by_slug(slug: str) -> MarketConfig | None:
    for market in load_markets().values():
        if market.slug == slug:
            return market
    return None


def trade_by_slug(slug: str) -> TradeConfig | None:
    for trade in load_trades().values():
        if (trade.slug or trade.id) == slug:
            return trade
    return None


def active_trades() -> list[TradeConfig]:
    """Trades enabled anywhere. Only one is active in the MVP; the list is future-facing."""
    return [t for t in load_trades().values() if t.active]


# --- keyword map --------------------------------------------------------------


@dataclass
class KeywordEntry:
    """One keyword: the phrase, the intent behind it and the role it plays for its page."""

    phrase: str
    intent: str
    role: str
    reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeywordEntry:
        return cls(
            phrase=str(data["phrase"]),
            intent=str(data.get("intent") or "commercial"),
            role=str(data.get("role") or "secondary"),
            reason=data.get("reason"),
        )


@dataclass
class KeywordPage:
    """A page in the keyword map, with the keywords it is allowed to target."""

    id: str
    path: str
    page_type: str
    intent: str
    title: str
    primary_keyword: str
    keywords: list[KeywordEntry] = field(default_factory=list)
    supporting: list[str] = field(default_factory=list)
    quality_gate: dict[str, int] = field(default_factory=dict)
    explained_on: str | None = None

    @property
    def primary_entries(self) -> list[KeywordEntry]:
        return [k for k in self.keywords if k.role == "primary"]

    @property
    def deferred(self) -> list[KeywordEntry]:
        return [k for k in self.keywords if k.role == "deferred"]

    def all_phrases(self) -> list[str]:
        return [k.phrase for k in self.keywords]


@dataclass
class KeywordMap:
    """The whole keyword-to-page map, loaded from configuration."""

    version: int
    intents: dict[str, str] = field(default_factory=dict)
    pages: list[KeywordPage] = field(default_factory=list)
    guide_topics: list[dict[str, Any]] = field(default_factory=list)

    def page(self, page_id: str) -> KeywordPage | None:
        return next((p for p in self.pages if p.id == page_id), None)

    def primary_claims(self) -> dict[str, str]:
        """Primary keyword phrase to the page id that claims it.

        Used to prove no two pages compete for one primary keyword. Returned as a mapping so a
        collision is visible as a shorter dict than the number of claims.
        """
        claims: dict[str, str] = {}
        for page in self.pages:
            for entry in page.primary_entries:
                claims.setdefault(entry.phrase, page.id)
        return claims

    def duplicate_primary_claims(self) -> list[tuple[str, list[str]]]:
        """Primary phrases claimed by more than one page, with the claiming page ids."""
        by_phrase: dict[str, list[str]] = {}
        for page in self.pages:
            for entry in page.primary_entries:
                by_phrase.setdefault(entry.phrase, []).append(page.id)
        return [(phrase, ids) for phrase, ids in by_phrase.items() if len(ids) > 1]

    def all_keywords(self) -> list[KeywordEntry]:
        return [k for page in self.pages for k in page.keywords]


# --- search vocabulary --------------------------------------------------------


@dataclass(frozen=True)
class VocabularyGroup:
    """A set of terms a search treats as equivalent, e.g. ``ahu`` and ``air handling unit``."""

    id: str
    label: str
    terms: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VocabularyGroup:
        return cls(
            id=str(data.get("id") or ""),
            label=str(data.get("label") or ""),
            terms=tuple(str(t).strip().lower() for t in data.get("terms") or [] if str(t).strip()),
        )


@dataclass(frozen=True)
class SearchVocabulary:
    """Query terms mapped to their equivalents, loaded from configuration.

    Global groups apply everywhere; per-trade groups add the trade's own equipment names.
    A term appears in at most one effective group (validated by ``conflicts``), so expansion
    is deterministic rather than depending on dict ordering.
    """

    version: int
    groups: tuple[VocabularyGroup, ...] = ()
    trade_groups: dict[str, tuple[VocabularyGroup, ...]] = field(default_factory=dict)

    def for_trade(self, trade_id: str | None) -> tuple[VocabularyGroup, ...]:
        """The effective groups for a trade: the global groups plus that trade's additions."""
        return self.groups + tuple(self.trade_groups.get(trade_id or "", ()))

    def term_index(self, trade_id: str | None = None) -> dict[str, VocabularyGroup]:
        """Map every known term to the group it belongs to, for one trade."""
        index: dict[str, VocabularyGroup] = {}
        for group in self.for_trade(trade_id):
            for term in group.terms:
                index.setdefault(term, group)
        return index

    def conflicts(self, trade_id: str | None = None) -> dict[str, list[str]]:
        """Terms claimed by more than one group, with the competing group ids.

        A term in two groups would expand ambiguously, so this is asserted empty by the tests
        rather than left to a silent first-wins.
        """
        by_term: dict[str, list[str]] = {}
        for group in self.for_trade(trade_id):
            for term in group.terms:
                by_term.setdefault(term, []).append(group.id)
        return {term: ids for term, ids in by_term.items() if len(ids) > 1}


@lru_cache(maxsize=1)
def load_search_vocabulary(path: Path | None = None) -> SearchVocabulary:
    """Load the search synonym/abbreviation map. Cached, like every other configuration file."""
    path = path or (CONFIG_DIR / "search_vocabulary.yaml")
    raw = yaml.safe_load(path.read_text()) or {}
    trade_groups = {
        str(trade_id): tuple(VocabularyGroup.from_dict(g) for g in groups or [])
        for trade_id, groups in (raw.get("trades") or {}).items()
    }
    return SearchVocabulary(
        version=int(raw.get("version") or 1),
        groups=tuple(VocabularyGroup.from_dict(g) for g in raw.get("groups") or []),
        trade_groups=trade_groups,
    )


@lru_cache(maxsize=1)
def load_keyword_map(path: Path | None = None) -> KeywordMap:
    """Load the keyword-to-page map.

    Loaded once and cached, like every other configuration file, so the SEO engine reads one
    consistent map for a process rather than re-parsing YAML per request.
    """
    path = path or (CONFIG_DIR / "keywords.yaml")
    raw = yaml.safe_load(path.read_text()) or {}
    pages: list[KeywordPage] = []
    for item in raw.get("pages") or []:
        pages.append(
            KeywordPage(
                id=str(item["id"]),
                path=str(item["path"]),
                page_type=str(item.get("page_type") or "hub"),
                intent=str(item.get("intent") or "informational"),
                title=str(item.get("title") or ""),
                primary_keyword=str(item.get("primary_keyword") or ""),
                keywords=[
                    KeywordEntry.from_dict(k) for k in item.get("keywords") or []
                ],
                supporting=list(item.get("supporting") or []),
                quality_gate=dict(item.get("quality_gate") or {}),
                explained_on=item.get("explained_on"),
            )
        )
    return KeywordMap(
        version=int(raw.get("version") or 1),
        intents=dict(raw.get("intents") or {}),
        pages=pages,
        guide_topics=list(raw.get("guide_topics") or []),
    )


def reset_config_cache() -> None:
    """Clear cached configuration. Used by tests that write temporary config files."""
    load_markets.cache_clear()
    _active_market_id.cache_clear()
    load_sources.cache_clear()
    load_retention.cache_clear()
    load_trades.cache_clear()
    load_keyword_map.cache_clear()