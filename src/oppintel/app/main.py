"""The web application.

A thin presentation layer over `OpportunityService`. Route handlers do three things only:
resolve configuration, call the service, and render a template. No route builds SQL, scores a
project, or decides what counts as an opportunity, because those rules live in the intelligence
layer and applying them twice would let the two disagree.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import urllib.request
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import click
from typing import Any

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..config import (
    MarketConfig,
    TradeConfig,
    load_markets,
    load_trades,
    market_by_slug,
    trade_by_slug,
)
from ..coverage import all_market_coverage, coverage_summary
from ..db import Database
from ..slugs import project_id_for_slug
from ..service import (
    DEFAULT_SORT,
    PUBLIC_CLASSIFICATIONS,
    SORT_OPTIONS,
    OpportunityFilters,
    OpportunityService,
)
from .accounts import AuthError, AccountService, record_analytics
from .alerts import AlertService
from .analytics_funnel import (
    LANDING_CATEGORY,
    LANDING_CITY,
    LANDING_CITY_TRADE,
    LANDING_DIRECTORY,
    LANDING_GUIDE,
    LANDING_MARKET,
    LANDING_PROJECT_TYPE,
    LANDING_TRADE,
    record_landing,
)
from .config import AppConfig, load_config
from .entitlements import SubscriptionService
from .mailer import DeliveryError, ResetMailer
from .seo import SeoBuilder
from .security import install_security
from ..companies import CompanyService
from ..nl_search import StructuredSearchInterpreter
from .workflow import (
    DEFAULT_STAGE,
    PIPELINE_STAGES,
    STAGE_LABELS,
    WorkflowError,
    WorkflowService,
)

log = logging.getLogger(__name__)


class FirebaseVerificationError(Exception):
    """A Firebase ID token could not be trusted as proof of identity."""


def _load_firebase_config() -> dict[str, Any] | None:
    """Read the Firebase web configuration, which is public client configuration.

    The apiKey here is a Firebase *web* key, not a secret credential: it is shipped to the
    browser by design and only identifies the project. It is used server-side solely to call
    the Identity Toolkit lookup endpoint, which is what validates a token; it cannot mint a
    session on its own.

    The configuration is injected per deployment rather than committed. Precedence:

    1. ``FIREBASE_CONFIG_JSON`` — the JSON object inline, for hosts that inject secrets as
       environment values and cannot mount a file.
    2. ``FIREBASE_CONFIG_PATH`` — a path to a JSON file.
    3. ``firebase-applet-config.json`` in the working directory or the repository root, for
       local development only. The file is git-ignored, so a checkout never carries the key.
    """
    inline = os.environ.get("FIREBASE_CONFIG_JSON")
    if inline:
        try:
            parsed = json.loads(inline)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            log.warning("FIREBASE_CONFIG_JSON is set but is not valid JSON; ignoring it.")
    candidates = [
        os.environ.get("FIREBASE_CONFIG_PATH"),
        "firebase-applet-config.json",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "firebase-applet-config.json",
        ),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return None


def _expected_firebase_audience() -> str | None:
    """The Firebase project the ID token must have been minted for.

    Prefer an explicit environment variable so a deployment is not tied to the committed
    config file; fall back to the projectId in the Firebase config.
    """
    project_id = os.environ.get("FIREBASE_PROJECT_ID")
    if project_id:
        return project_id
    cfg = _load_firebase_config()
    if cfg:
        return cfg.get("projectId")
    return None


def _verify_firebase_id_token(id_token: str) -> dict[str, Any]:
    """Verify a Firebase/Google ID token and return its trusted claims.

    Two independent server-side checks, and a claim is only trusted when the *Google* endpoint
    confirms the signature and expiry:

    1. Google's OAuth2 `tokeninfo` endpoint cryptographically verifies the ID token signature,
       issuer and expiry, and returns the verified claims.
    2. Firebase Identity Toolkit `accounts:lookup` verifies the token against the project and
       returns the account's own email, so the identity cannot be spoofed by a token minted for
       a different audience.

    The token is never trusted as a raw payload, and no claim supplied elsewhere by the client
    (an email query parameter, a form field, a client-supplied uid) is consulted.
    """
    token = (id_token or "").strip()
    if not token or len(token) > 8192:
        raise FirebaseVerificationError("missing or malformed token")

    claims: dict[str, Any] = {}
    verified = False

    # 1. Google tokeninfo: verifies signature, iss and exp.
    try:
        req = urllib.request.Request(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={token}",
            headers={"User-Agent": "BuildScope-Backend/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("email"):
            claims = {
                "email": data.get("email"),
                "sub": data.get("sub") or data.get("user_id"),
                "name": data.get("name"),
                "email_verified": str(data.get("email_verified", "")).lower() == "true",
                "aud": data.get("aud"),
                "iss": data.get("iss"),
            }
            verified = True
    except Exception:
        verified = False

    # 2. Firebase Identity Toolkit: verifies against the project and is authoritative for uid.
    fb_cfg = _load_firebase_config()
    api_key = fb_cfg.get("apiKey") if fb_cfg else None
    if api_key:
        try:
            payload = json.dumps({"idToken": token}).encode("utf-8")
            req2 = urllib.request.Request(
                f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={api_key}",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "BuildScope-Backend/1.0"},
            )
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                acc_data = json.loads(resp2.read().decode("utf-8"))
            users = acc_data.get("users", [])
            if users:
                u = users[0]
                claims = {
                    "email": u.get("email"),
                    "sub": u.get("localId"),
                    "name": u.get("displayName"),
                    "email_verified": bool(u.get("emailVerified")),
                    "aud": claims.get("aud"),
                    "iss": claims.get("iss"),
                }
                verified = True
        except Exception as exc2:
            logging.warning("Firebase Identity Toolkit lookup failed: %s", exc2)

    if not verified or not claims.get("email") or not claims.get("sub"):
        raise FirebaseVerificationError("token could not be verified")

    # The token must belong to this project and must carry a verified email.
    expected_aud = _expected_firebase_audience()
    if expected_aud and claims.get("aud") and claims["aud"] != expected_aud:
        raise FirebaseVerificationError("token audience does not match this project")
    if not claims.get("email_verified"):
        raise FirebaseVerificationError("email is not verified by the identity provider")

    return claims


def create_app(config: AppConfig | None = None) -> Flask:
    """Application factory. Used by the dev server, the CLI and the tests alike."""
    cfg = config or load_config()
    if not cfg.secret_key_from_env:
        log.warning(
            "SECRET_KEY is not set. A random key was generated for this process, so sessions "
            "will not survive a restart. Set SECRET_KEY in production."
        )

    app = Flask(
        __name__,
        template_folder=str(cfg.templates_dir),
        static_folder=str(cfg.static_dir),
    )
    # Static assets are content-fingerprinted in the templates (`app.css?v=<hash>`), so a long
    # public cache is safe and a rebuild is still picked up. Without this Flask sends `no-cache`.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.session_cookie_secure,
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    app.config["APP_CONFIG"] = cfg

    # --- database and per-request context ------------------------------------

    def open_db() -> Database:
        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        return db

    def current_market() -> MarketConfig:
        """The market for this request.

        Resolved per request so a future multi-market deployment can switch on host or path
        without touching the handlers.
        """
        slug = getattr(g, "market_slug", None)
        if slug:
            market = market_by_slug(slug)
            if market:
                return market
        return _active(markets)

    def current_trade() -> TradeConfig:
        slug = getattr(g, "trade_slug", None)
        if slug:
            trade = trade_by_slug(slug)
            if trade:
                return trade
        return _active_trade()

    @app.before_request
    def load_context() -> None:
        g.db = open_db()
        g.accounts = AccountService(g.db)
        g.user = g.accounts.get_user(session.get("user_id"))
        g.service = _service(g.db, current_market(), current_trade(), g.user)
        g.market = g.service.market
        g.trade = g.service.trade
        g.workflow = WorkflowService(g.db)
        g.alerts = AlertService(g.db)
        g.subscriptions = SubscriptionService(g.db)
        g.entitlement = g.subscriptions.entitlements_for(g.user)
        g.mailer = ResetMailer(backend=cfg.mail_backend, base_url=cfg.base_url)
        g.started_at = datetime.now(timezone.utc)
        g.session_id = _session_token()
        g.campaign = _campaign_from_request()

    @app.teardown_request
    def close_db(exception: BaseException | None = None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    # --- template helpers -----------------------------------------------------

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        """Values every template needs, resolved once per request."""
        market = getattr(g, "market", None)
        trade = getattr(g, "trade", None)
        return {
            "market": market,
            "trade": trade,
            "all_markets": sorted(markets.values(), key=lambda m: (not m.active, m.name)),
            "all_trades": sorted(_trade_map().values(), key=lambda t: (not t.active, t.label)),
            "current_user": getattr(g, "user", None),
            "base_url": cfg.base_url,
            "now_year": datetime.now(timezone.utc).year,
            "freshness": _freshness_label(getattr(g, "db", None)),
            "saved_count": (
                g.service.saved_count(g.user.id) if getattr(g, "user", None) else 0
            ),
            "unread_alerts": (
                g.alerts.unread_count(g.user.id)
                if getattr(g, "user", None) and getattr(g, "alerts", None)
                else 0
            ),
            "firebase_config": _load_firebase_config(),
            "asset_url": asset_url,
        }

    @lru_cache(maxsize=None)
    def asset_version(filename: str) -> str:
        """A short fingerprint of a static file (mtime + size), or empty when it is absent.

        Versions the stylesheet URL so a long public cache stays safe: a new build changes the
        fingerprint and the browser fetches the new file instead of reusing the old one.
        """
        try:
            stat = (cfg.static_dir / filename).stat()
        except OSError:
            return ""
        digest = hashlib.sha256(f"{int(stat.st_mtime)}:{stat.st_size}".encode()).hexdigest()
        return digest[:12]

    def asset_url(filename: str) -> str:
        """`url_for('static', filename=...)` with a content fingerprint query appended."""
        base = url_for("static", filename=filename)
        version = asset_version(filename)
        return f"{base}?v={version}" if version else base

    @app.template_filter("money")
    def money_filter(value: Any) -> str:
        if value is None:
            return "Not verified"
        try:
            return f"${float(value):,.0f}"
        except (TypeError, ValueError):
            return "Not verified"

    @app.template_filter("sqft")
    def sqft_filter(value: Any) -> str:
        if value is None:
            return "Not verified"
        try:
            return f"{float(value):,.0f} sq ft"
        except (TypeError, ValueError):
            return "Not verified"

    @app.template_filter("or_na")
    def or_na_filter(value: Any) -> str:
        """Render a missing value as 'Not verified'.

        Centralised so no template can invent a placeholder or leave a blank that reads as
        "zero" or "none".
        """
        if value is None:
            return "Not verified"
        text = str(value).strip()
        return text or "Not verified"

    @app.template_filter("nice_date")
    def nice_date_filter(value: Any) -> str:
        return OpportunityService.format_date(value)

    @app.template_filter("filing_date")
    def filing_date_filter(value: Any) -> str:
        """Render a permit/filing date, marking a future value as an unverified date.

        A permit cannot be filed after the record observing it, so a future date is not
        presented as a filing date. The source's value is still shown, labelled, rather than
        hidden or corrected.
        """
        from ..dates import occurrence_is_future

        if not value:
            return "Not verified"
        rendered = OpportunityService.format_date(value)
        if occurrence_is_future(value):
            return f"{rendered} (date unverified — after today)"
        return rendered

    @app.template_filter("month_year")
    def month_year_filter(value: Any) -> str:
        return OpportunityService.format_month(value)

    # --- error handling -------------------------------------------------------

    @app.errorhandler(404)
    def not_found(error: Any) -> tuple[str, int]:
        # Error pages still need metadata, because a 404 is rendered through the same base
        # layout. Building it here keeps the layout from having to handle an undefined value.
        seo = g.seo_builder.simple(
            "Page not found", "The requested page could not be found."
        )
        seo.noindex = True
        return (
            render_template(
                "errors/404.html", page_title="Page not found", seo=seo
            ),
            404,
        )

    @app.errorhandler(403)
    def forbidden(error: Any) -> tuple[str, int]:
        """Signed in, but not permitted.

        Distinct from 404 so the response is honest about what happened: the page exists, the
        account simply does not have access. The body discloses nothing about what the page
        contains.
        """
        seo = g.seo_builder.simple(
            "Not permitted", "This account does not have access to that page."
        )
        seo.noindex = True
        return (
            render_template(
                "errors/403.html", page_title="Not permitted", seo=seo
            ),
            403,
        )

    @app.errorhandler(500)
    def server_error(error: Any) -> tuple[str, int]:
        # The exception is logged with its traceback server-side; the user sees nothing that
        # could disclose a path, a query or a credential.
        log.exception("Unhandled application error")
        # Recorded without a traceback, a user id or a request body, so the operations view can
        # show that something failed without the table becoming a store of sensitive detail.
        db = getattr(g, "db", None)
        if db is not None:
            try:
                db.record_app_error(
                    request.path, request.method, 500,
                    error.__class__.__name__,
                )
            except Exception:  # noqa: BLE001 - never let error reporting mask the error
                log.exception("Failed to record application error")
        seo = g.seo_builder.simple("Something went wrong", "An unexpected error occurred.")
        seo.noindex = True
        return (
            render_template(
                "errors/500.html",
                page_title="Something went wrong",
                support_note="The issue has been logged.",
                seo=seo,
            ),
            500,
        )

    # =====================================================================
    # Public pages
    # =====================================================================

    @app.route("/")
    def home() -> str:
        from .stat_snapshot import METRIC_DEFINITIONS

        stats = g.service.market_statistics()
        latest = g.service.list_opportunities(
            OpportunityFilters(page_size=6, sort=DEFAULT_SORT)
        )
        return render_template(
            "home.html",
            stats=stats,
            stat_definitions={k: v[1] for k, v in METRIC_DEFINITIONS.items()},
            latest=latest.items,
            recent_changes=g.service.recent_changes(limit=4, days=30),
            cities=g.service.city_statistics()[:8],
            types=g.service.type_statistics(limit=8),
            page_title=(
                f"Commercial {g.trade.short_label or g.trade.label} Construction "
                f"Opportunities Across {g.market.short_name}"
            ),
            seo=g.seo_for_home(stats),
        )

    @app.route("/opportunities")
    def opportunities() -> str:
        filters = _filters_from_request(request.args)
        # The visitor's own words, kept before the interpreter rewrites `filters.q` into
        # structured filters. Analytics must record what was typed, not the residual keyword.
        raw_query = filters.q
        available_cities = g.service.available_cities()
        interpreted = _interpret_search_query(filters.q, available_cities)
        if interpreted and interpreted.get("has_structured_intent"):
            if not filters.city and interpreted.get("city"):
                filters.city = interpreted["city"]
            if not filters.project_type and interpreted.get("project_type"):
                filters.project_type = interpreted["project_type"]
            if not filters.classification and interpreted.get("classification"):
                filters.classification = interpreted["classification"]
            if not filters.mechanical_only and interpreted.get("mechanical_only"):
                filters.mechanical_only = True
            if not filters.freshness_days and interpreted.get("freshness_days"):
                filters.freshness_days = interpreted["freshness_days"]
            if not filters.min_value and interpreted.get("min_value"):
                filters.min_value = interpreted["min_value"]
            if interpreted.get("clean_q") != filters.q:
                filters.q = interpreted.get("clean_q")

        result = g.service.list_opportunities(filters)
        tier1_count = sum(1 for item in result.items if item.get("mechanical_evidence_tier") == 1)
        tier2_count = sum(1 for item in result.items if item.get("mechanical_evidence_tier") == 2)
        base_count = len(result.items) - tier1_count - tier2_count
        evidence_breakdown = {
            "tier1": tier1_count,
            "tier2": tier2_count,
            "base": max(0, base_count),
        }

        record_analytics(
            g.db, "search_performed",
            market_id=g.market.id, trade_id=g.trade.id,
            query_text=raw_query,
            result_count=result.total,
            filter_summary=_filter_summary(filters),
            session_id=getattr(g, "session_id", None),
            campaign=getattr(g, "campaign", None),
        )
        # A search that returned nothing is the most actionable signal in the data, so it is
        # recorded as its own event rather than only inferred from a result_count of zero. The
        # event is written only when a query was actually typed, so a bare directory view does
        # not masquerade as a zero-result search.
        if raw_query and result.total == 0:
            record_analytics(
                g.db, "search_no_results",
                market_id=g.market.id, trade_id=g.trade.id,
                query_text=raw_query,
                result_count=0,
                filter_summary=_filter_summary(filters),
                session_id=getattr(g, "session_id", None),
                campaign=getattr(g, "campaign", None),
            )
        # The directory is the product's main organic entry point, so a view of it is recorded
        # as a funnel landing.
        record_landing(g.db, LANDING_DIRECTORY, market_id=g.market.id, trade_id=g.trade.id)
        # Only the first page of the unfiltered directory is canonical; a filtered or paged
        # view is a distinct result set and must not compete with it in search results.
        is_canonical = not any(
            [filters.q, filters.city, filters.project_type, filters.classification,
             filters.procurement_status, filters.date_from, filters.date_to]
        ) and filters.page == 1
        return render_template(
            "opportunities/list.html",
            result=result,
            filters=filters,
            interpreted=interpreted,
            evidence_breakdown=evidence_breakdown,
            cities=available_cities,
            project_types=g.service.available_project_types(),
            procurement_options=g.service.procurement_options(),
            sort_options=SORT_OPTIONS,
            active_filter_count=_active_filter_count(filters),
            date_presets=_date_presets(),
            page_title=(
                f"{g.market.short_name} Commercial {g.trade.short_label} Opportunities"
            ),
            seo=g.seo_for_directory(filters, is_canonical, result.total),
        )

    @app.route("/opportunities/<slug>")
    def opportunity_detail(slug: str) -> str:
        project = g.service.get_by_slug(slug)
        if project is None:
            abort(404)
        record_analytics(
            g.db, "opportunity_viewed", project_id=project["id"],
            market_id=g.market.id, trade_id=g.trade.id,
        )

        # Match reasons, timeline and the account's own working record. Each is produced by a
        # shared implementation: the same reasons the feed shows, the same change rows the
        # alerts read, and notes scoped to this account alone.
        from .matching import evaluate_match

        prefs = g.accounts.get_preferences(g.user.id) if g.user else {}
        project["match_reasons"] = evaluate_match(
            project, trade=g.trade, market=g.market, preferences=prefs
        ).reasons
        project["timeline"] = g.service.timeline_for(project["id"])
        project["notes"] = (
            g.workflow.notes_for(g.user.id, project["id"]) if g.user else []
        )
        project["tags"] = (
            g.workflow.tags_for(g.user.id, project["id"]) if g.user else []
        )

        return render_template(
            "opportunities/detail.html",
            project=project,
            related=g.service.related_for(project),
            is_saved=g.service.is_saved(g.user.id if g.user else None, project["id"]),
            stages=PIPELINE_STAGES,
            page_title=_detail_title(project, g),
            seo=g.seo_for_opportunity(project),
        )

    @app.route("/markets")
    def markets_index() -> str:
        # Coverage is computed from stored rows, not from configuration intent, so a market
        # that is merely listed is never presented as one that is served.
        coverage = {c.market_id: c for c in all_market_coverage(g.db)}
        cards = []
        for market in sorted(markets.values(), key=lambda m: (not m.active, m.name)):
            stats = None
            if market.id == g.market.id:
                stats = g.service.market_statistics()
            cards.append(
                {"market": market, "stats": stats, "coverage": coverage.get(market.id)}
            )
        return render_template(
            "markets/index.html",
            cards=cards,
            coverage_summary=coverage_summary(g.db),
            page_title="Markets",
            seo=g.seo_for_simple("Markets", "Markets covered by the platform."),
        )

    @app.route("/markets/<market_slug>")
    def market_landing(market_slug: str) -> str:
        market = market_by_slug(market_slug)
        if market is None or not market.active:
            abort(404)
        g.market_slug = market_slug
        g.service = _service(g.db, market, current_trade(), g.user)
        g.market = market
        stats = g.service.market_statistics()
        result = g.service.list_opportunities(OpportunityFilters(page_size=6))
        record_landing(g.db, LANDING_MARKET, market_id=market.id, trade_id=g.trade.id)
        return render_template(
            "markets/detail.html",
            mkt=market,
            stats=stats,
            opportunities=result.items,
            cities=g.service.city_statistics(),
            page_title=f"{market.short_name} Commercial Construction Opportunities",
            seo=g.seo_for_market(market, stats),
        )

    @app.route("/markets/<market_slug>/<slug>")
    def market_child(market_slug: str, slug: str) -> str:
        """Resolve a two-segment market URL.

        The market+trade page and the market+city page share a URL shape
        (`/markets/<market>/<slug>`), so one endpoint resolves which the slug refers to. Flask
        can only match one rule per path shape, and registering both would make one of them
        unreachable. A slug that names a known trade is a trade page; otherwise it is treated as
        a city.

        Trade slugs are checked first because they are a closed, configured set, whereas a city
        slug that happens to equal a trade name would be ambiguous.
        """
        if trade_by_slug(slug) is not None:
            return _market_trade_page(market_slug, slug)
        return _market_city_page(market_slug, slug)

    @app.route("/markets/<market_slug>/<city_slug>/<trade_slug>")
    def city_trade_page(market_slug: str, city_slug: str, trade_slug: str) -> str:
        """A city crossed with a trade — the genuinely programmatic combination.

        This is where combinatorial explosion lives: every configured city times every active
        trade. It is therefore the page the quality gate governs. A combination without enough
        real evidence still renders honestly, but is marked noindex and withheld from the
        sitemap until the data justifies indexing it.
        """
        market = market_by_slug(market_slug)
        trade = trade_by_slug(trade_slug)
        if market is None or trade is None or not market.active or not trade.active:
            abort(404)
        # Only cities declared as indexable landing pages participate, so the set of
        # combinations stays bounded by configuration rather than by whatever the database holds.
        if city_slug not in {p["slug"] for p in market.landing_pages}:
            abort(404)
        city_name = market.city_name(city_slug)
        if not city_name:
            abort(404)

        g.market_slug = market_slug
        g.trade_slug = trade_slug
        g.service = _service(g.db, market, trade, g.user)
        g.market, g.trade = market, trade
        stats = g.service.statistics_for(where="p.city = ?", params=[city_name])
        result = g.service.list_opportunities(
            OpportunityFilters(city=city_name, page_size=10)
        )
        record_landing(g.db, LANDING_CITY_TRADE, market_id=market.id, trade_id=trade.id)
        gate = _gate_for("city_trade", stats, f"{city_name} {trade.label}")
        seo = g.seo_builder.city_trade_page(market, city_name, city_slug, trade, stats)
        return render_template(
            "markets/city_trade.html",
            mkt=market,
            city_name=city_name,
            city_slug=city_slug,
            trd=trade,
            stats=stats,
            result=result,
            gate=gate,
            page_title=f"{city_name} Commercial {trade.short_label} Construction Opportunities",
            seo=g.seo_builder.apply_gate(seo, gate),
        )

    def _market_city_page(market_slug: str, city_slug: str) -> str:
        market = market_by_slug(market_slug)
        if market is None or not market.active:
            abort(404)
        # Only cities declared as indexable landing pages get a page, so the site never
        # generates a near-duplicate for every city in the database.
        if city_slug not in {p["slug"] for p in market.landing_pages}:
            abort(404)
        city_name = market.city_name(city_slug)
        if not city_name:
            abort(404)

        g.market_slug = market_slug
        g.service = _service(g.db, market, current_trade(), g.user)
        g.market = market
        filters = OpportunityFilters(city=city_name, page_size=10)
        result = g.service.list_opportunities(filters)
        stats = g.service.statistics_for(where="p.city = ?", params=[city_name])
        record_landing(g.db, LANDING_CITY, market_id=market.id, trade_id=g.trade.id)
        return render_template(
            "markets/city.html",
            mkt=market,
            city_name=city_name,
            city_slug=city_slug,
            stats=stats,
            result=result,
            page_title=f"{city_name} Commercial {g.trade.short_label} Construction Opportunities",
            seo=g.seo_for_city(market, city_name, stats, city_slug),
        )

    @app.route("/trades")
    def trades_index() -> str:
        return render_template(
            "trades/index.html",
            trades=sorted(_trade_map().values(), key=lambda t: (not t.active, t.label)),
            page_title="Trades",
            seo=g.seo_for_simple(
                "Trades", "Trades supported by the platform."
            ),
        )

    @app.route("/trades/<trade_slug>")
    def trade_landing(trade_slug: str) -> str:
        trade = trade_by_slug(trade_slug)
        if trade is None or not trade.active:
            abort(404)
        g.trade_slug = trade_slug
        g.service = _service(g.db, current_market(), trade, g.user)
        g.trade = trade
        stats = g.service.market_statistics()
        result = g.service.list_opportunities(OpportunityFilters(page_size=6))
        record_landing(g.db, LANDING_TRADE, market_id=g.market.id, trade_id=trade.id)
        return render_template(
            "trades/detail.html",
            trd=trade,
            stats=stats,
            opportunities=result.items,
            page_title=f"Commercial {trade.short_label or trade.label} Opportunities",
            seo=g.seo_for_trade(trade, stats),
        )

    def _market_trade_page(market_slug: str, trade_slug: str) -> str:
        """The market crossed with a trade — a configured, curated page."""
        market = market_by_slug(market_slug)
        trade = trade_by_slug(trade_slug)
        if market is None or trade is None or not market.active or not trade.active:
            abort(404)
        g.market_slug = market_slug
        g.trade_slug = trade_slug
        g.service = _service(g.db, market, trade, g.user)
        g.market, g.trade = market, trade
        stats = g.service.market_statistics()
        result = g.service.list_opportunities(OpportunityFilters(page_size=8))
        return render_template(
            "markets/trade.html",
            mkt=market,
            trd=trade,
            stats=stats,
            result=result,
            cities=g.service.city_statistics()[:10],
            page_title=(
                f"{market.short_name} Commercial {trade.short_label} Construction Opportunities"
            ),
            seo=g.seo_for_market_trade(market, trade, stats),
        )

    @app.route("/project-types")
    def project_types_index() -> str:
        """Index of project types that hold real records. Nothing is generated speculatively."""
        from ..config import type_slug

        types = g.service.type_statistics(limit=100)
        return render_template(
            "project_types/index.html",
            types=types,
            type_slug=type_slug,
            page_title=f"{g.market.short_name} Project Types",
            seo=g.seo_builder.project_types_index(types),
        )

    @app.route("/project-types/<type_slug_value>")
    def project_type_page(type_slug_value: str) -> str:
        """A landing page for one project type, built only from real records.

        The slug is resolved back to a stored `project_type` value through the database, so a
        page cannot exist for a type the data does not contain, and a request for an unknown
        type is a 404 rather than an empty page.
        """
        mapping = g.service.project_type_slug_map()
        name = mapping.get(type_slug_value)
        if not name:
            abort(404)
        stats = g.service.statistics_for_type(name)
        result = g.service.list_opportunities(
            OpportunityFilters(project_type=name, page_size=12)
        )
        record_landing(g.db, LANDING_PROJECT_TYPE, market_id=g.market.id, trade_id=g.trade.id)
        return render_template(
            "project_types/detail.html",
            project_type=name,
            stats=stats,
            result=result,
            cities=g.service.cities_for_type(name),
            page_title=f"{name} Construction Opportunities in {g.market.short_name}",
            seo=g.seo_builder.project_type_page(name, stats),
        )

    @app.route("/commercial-construction-leads")
    def core_category() -> str:
        """The primary category page for the leads / project-intelligence intent.

        Answers "what are commercial construction leads and where do I get them" with the
        product's own evidence standard, then hands the visitor the directory. Example
        opportunities are real records the account can open, not marketing mock-ups.
        """
        stats = g.service.market_statistics()
        examples = g.service.list_opportunities(OpportunityFilters(page_size=4)).items
        record_landing(g.db, LANDING_CATEGORY, market_id=g.market.id, trade_id=g.trade.id)
        return render_template(
            "core_category.html",
            stats=stats,
            examples=examples,
            cities=g.service.city_statistics()[:6],
            types=g.service.type_statistics(limit=6),
            page_title="Commercial Construction Leads and Project Intelligence",
            seo=g.seo_builder.core_category_page(stats, examples),
        )

    @app.route("/guides")
    def guides_index() -> str:
        """Educational content index. Each guide is static, owned prose — never generated
        filler — and marked indexable only when it carries real substance."""
        from .content import GUIDES

        return render_template(
            "content/index.html",
            guides=GUIDES,
            page_title="Guides",
            seo=g.seo_builder.simple(
                "Guides",
                "How to read permit evidence and evaluate a construction opportunity.",
            ),
        )

    @app.route("/guides/<guide_slug>")
    def guide_detail(guide_slug: str) -> str:
        from .content import GUIDES

        guide = next((item for item in GUIDES if item["slug"] == guide_slug), None)
        if guide is None:
            abort(404)
        record_landing(g.db, LANDING_GUIDE, market_id=g.market.id, trade_id=g.trade.id)
        return render_template(
            "content/detail.html",
            guide=guide,
            page_title=guide["title"],
            seo=g.seo_builder.guide_page(guide),
        )

    @app.route("/how-it-works")
    def how_it_works() -> str:
        # The ingest funnel is shown here so the "Collect -> Normalize -> Assemble" claim is
        # accountable: the visitor sees how many raw permits landed, how many were linked to a
        # project, and how many dropped. It reads the same stored snapshot as every headline.
        stats = g.service.market_statistics()
        return render_template(
            "how_it_works.html",
            stats=stats,
            page_title="How It Works",
            seo=g.seo_for_simple(
                "How It Works",
                "How public construction records become verified commercial opportunities.",
            ),
        )

    @app.route("/reports")
    def reports_index() -> str:
        """Published reports only. Internal audit reports are never served."""
        from .reports import published_reports

        return render_template(
            "reports/index.html",
            reports=published_reports(g.db, cfg),
            page_title="Reports",
            seo=g.seo_for_simple(
                "Reports",
                "Market summaries and opportunity briefs generated from verified permit data.",
            ),
        )

    @app.route("/reports/<report_slug>")
    def report_detail(report_slug: str) -> str:
        from .reports import published_reports

        report = next(
            (r for r in published_reports(g.db, cfg) if r["slug"] == report_slug), None
        )
        if report is None:
            abort(404)
        return render_template(
            "reports/detail.html",
            report=report,
            body=report["body"],
            page_title=report["title"],
            seo=g.seo_for_simple(report["title"], report["summary"]),
        )

    @app.route("/companies")
    def companies_index() -> str:
        role = request.args.get("role")
        q = request.args.get("q")
        city = request.args.get("city")
        company_service = CompanyService(g.db)
        companies = company_service.list_companies(role=role, q=q, city=city, limit=60)
        return render_template(
            "companies/index.html",
            companies=companies,
            selected_role=role,
            q=q,
            selected_city=city,
            active_filter_count=sum(bool(v) for v in (q, role, city)),
            cities=g.service.available_cities(),
            page_title=f"{g.market.short_name} Commercial Construction Companies & Stakeholders",
            seo=g.seo_builder.simple(
                "Commercial Construction Companies & Stakeholders",
                "Directory of verified general contractors, owners, developers, and architects across commercial projects.",
            ),
        )

    @app.route("/companies/<slug>")
    def company_detail(slug: str) -> str:
        company_service = CompanyService(g.db)
        profile = company_service.get_company_profile(slug)
        if not profile:
            abort(404)
        return render_template(
            "companies/detail.html",
            company=profile,
            page_title=f"{profile.name} — Construction Intelligence Profile",
            seo=g.seo_builder.simple(
                f"{profile.name} — Commercial Construction Profile",
                f"Commercial construction projects, recurring partners, and market presence for {profile.name}.",
            ),
        )

    @app.route("/changes")
    def changes_feed() -> str:
        days = _safe_int(request.args.get("days"), 60)
        page = max(_safe_int(request.args.get("page"), 1), 1)
        page_size = 25
        total = g.service.recent_changes_count(days=days)
        breakdown = g.service.recent_changes_breakdown(days=days)
        recent_changes = g.service.recent_changes(
            limit=page_size, days=days, offset=(page - 1) * page_size
        )
        total_pages = max((total + page_size - 1) // page_size, 1)
        freshness = g.service.data_freshness()
        return render_template(
            "changes.html",
            changes=recent_changes,
            days=days,
            total=total,
            breakdown=breakdown,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            freshness=freshness,
            page_title=f"Market Change Audit — Detected Project Changes in {g.market.short_name}",
            seo=g.seo_builder.simple(
                "Market Change Audit",
                "Audit log of differences detected between collection runs across commercial "
                "construction and permit records.",
            ),
        )

    @app.route("/content/linkedin")
    @_require_admin
    def linkedin_content() -> Any:
        """Operator view: proposed posts and the draft builder.

        Internal on purpose. A draft is evidence-backed but still needs a human to edit, pick a
        graphic, and decide whether the timing is right; it is not published from here.
        """
        from ..linkedin import FORMATS, FORMAT_IDS, discover_candidates

        raw_format = (request.args.get("format") or "").strip()
        project_id = _safe_int(request.args.get("project_id"), 0) or None
        window = (request.args.get("window") or "30d").strip()
        draft = None
        draft_error = None
        validation: list[str] = []

        candidates = discover_candidates(g.db, trade=g.trade.id, limit=20)

        if raw_format:
            if raw_format not in FORMAT_IDS:
                draft_error = f"Unknown format: {raw_format}"
            else:
                from ..linkedin import build_draft, validate_post

                chosen_project = project_id
                try:
                    if raw_format in ("project_spotlight", "data_quality", "change_alert"):
                        if chosen_project is None:
                            if candidates:
                                chosen_project = candidates[0].project_id
                            else:
                                raise ValueError("no candidate project is available")
                        draft = build_draft(
                            g.db, format=raw_format, project_id=chosen_project,
                            window=window, trade=g.trade.id,
                        )
                    else:
                        ids = [c.project_id for c in candidates[:10]]
                        draft = build_draft(
                            g.db, format=raw_format, project_ids=ids,
                            window=window, trade=g.trade.id,
                        )
                    validation = validate_post(g.db, draft)
                except ValueError as exc:
                    draft_error = str(exc)

        return render_template(
            "content_linkedin.html",
            formats=FORMATS,
            candidates=candidates,
            draft=draft,
            draft_error=draft_error,
            validation=validation,
            active_format=raw_format,
            active_project=project_id,
            window=window,
            page_title="LinkedIn Content Intelligence — Operator",
        )

    @app.route("/trends")
    def trends_view() -> str:
        """Trend Radar: defensible change in the records BuildScope actually holds.

        Read-only and cheap: every figure is a count over the project, permit, change and
        intelligence tables, using their existing indexes. The page states each metric's
        definition and the source-coverage limits next to the figure, so a reader can tell an
        observed change from a change in coverage.
        """
        from ..trends import (
            WINDOWS, DEFAULT_WINDOW, WINDOW_DAYS, MIN_OBSERVATIONS_FOR_TREND,
            build_trend_report, format_period,
        )

        requested = request.args.get("window") or DEFAULT_WINDOW
        window = requested if requested in WINDOW_DAYS else DEFAULT_WINDOW
        report = build_trend_report(g.db, window=window, trade=g.trade.id)
        return render_template(
            "trends.html",
            report=report,
            windows=[label for label, _days in WINDOWS],
            active_window=window,
            period_label=format_period(report.period),
            previous_label=format_period(report.previous_period),
            min_observations=MIN_OBSERVATIONS_FOR_TREND,
            page_title=f"Trend Radar — Observed Construction Activity in {g.market.short_name}",
            seo=g.seo_builder.simple(
                "Trend Radar",
                "Observed changes in commercial construction records across configured markets, "
                "with each metric defined and source-coverage limits stated.",
            ),
        )

    @app.route("/analytics")
    def analytics_view() -> str:
        stats = g.service.market_statistics()
        cities = g.service.city_statistics()
        types = g.service.type_statistics(limit=15)
        recent_changes = g.service.recent_changes(limit=8, days=30)
        return render_template(
            "analytics.html",
            stats=stats,
            cities=cities,
            types=types,
            recent_changes=recent_changes,
            page_title=f"{g.market.short_name} Commercial Construction Intelligence Analytics",
            seo=g.seo_builder.simple(
                "Market Analytics",
                "Drill-down market analytics across commercial construction opportunities, property types, and trade evidence.",
            ),
        )

    @app.route("/sitemap.xml")
    def sitemap() -> Any:
        xml = g.seo_builder.sitemap()
        response = app.response_class(xml, mimetype="application/xml")
        # Generated from the database, so a year-long cache would keep a stale route list. A day
        # is short enough that a rebuild is picked up and long enough to spare the crawler.
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.route("/robots.txt")
    def robots() -> Any:
        response = app.response_class(g.seo_builder.robots(), mimetype="text/plain")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.route("/healthz")
    def healthz() -> Any:
        """Liveness probe for the hosting platform.

        A platform restarts a service that stops answering, so this has to distinguish a running
        process from a usable one. The one condition worth a restart is an unreachable database,
        which returns 503.

        An *empty* database is deliberately not a failure. On a first deploy the disk starts
        empty and the refresh loop fills it; restarting the process would not change that, so
        returning 503 would only produce a restart loop. Emptiness is reported in the body and
        the probe stays 200.

        Reports counts and never content, so it discloses nothing about a project.
        """
        payload: dict[str, Any] = {"status": "ok", "database": str(cfg.database_path)}
        try:
            # Counts come from the stats snapshot, not a fresh COUNT(*), so the probe and the
            # pages agree and the probe does not race the refresh loop.
            from .stat_snapshot import read_snapshot

            metrics = read_snapshot(g.db, g.market, g.trade)
            row = {
                "projects": metrics.get("projects_total", 0),
                "permits": metrics.get("permit_records_all", 0),
            }
            payload["projects"] = row["projects"]
            payload["permits"] = row["permits"]
            # The public jurisdiction count, so the probe and the pages agree on the same word.
            payload["cities_with_public_projects"] = metrics.get("active_jurisdictions", 0)
            # Coverage state is reported so an operator can distinguish "the process is up" from
            # "the market is actually served". Counts only; never project content.
            try:
                payload["coverage"] = coverage_summary(g.db)
            except Exception as exc:  # pragma: no cover - coverage must not fail the probe
                log.warning("coverage summary failed: %r", exc)
            if not row["projects"]:
                payload["status"] = "empty"
        except Exception as exc:
            payload["status"] = "database_unavailable"
            log.warning("health check failed: %r", exc)
            return jsonify(payload), 503
        return jsonify(payload)

    # =====================================================================
    # Accounts
    # =====================================================================

    @app.route("/signin", methods=["GET", "POST"])
    def signin() -> Any:
        if g.user:
            return redirect(url_for("home"))
        error = None
        if request.method == "POST":
            try:
                user = g.accounts.authenticate(
                    request.form.get("email", ""), request.form.get("password", "")
                )
                _start_session(user.id)
                record_analytics(g.db, "signin", market_id=g.market.id, trade_id=g.trade.id)
                target = request.args.get("next") or url_for("home")
                return redirect(_safe_redirect(target))
            except AuthError as exc:
                error = str(exc)
        stats = g.service.market_statistics()
        recent_signals = g.service.list_opportunities(OpportunityFilters(page_size=3)).items
        return render_template(
            "account/signin.html",
            error=error,
            stats=stats,
            recent_signals=recent_signals,
            page_title="Sign In",
            seo=_private_seo(g, "Sign In", "Sign in to your BuildScope workspace."),
        )

    @app.route("/signup", methods=["GET", "POST"])
    def signup() -> Any:
        if g.user:
            return redirect(url_for("home"))
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            if password != request.form.get("password_confirm", ""):
                error = "Passwords do not match."
            else:
                try:
                    user = g.accounts.create_account(
                        request.form.get("email", ""),
                        password,
                        request.form.get("display_name", ""),
                        market=g.market,
                        trade=g.trade,
                    )
                    _start_session(user.id)
                    record_analytics(g.db, "signup", market_id=g.market.id, trade_id=g.trade.id)
                    return redirect(url_for("home"))
                except AuthError as exc:
                    error = str(exc)
        stats = g.service.market_statistics()
        recent_signals = g.service.list_opportunities(OpportunityFilters(page_size=3)).items
        return render_template(
            "account/signup.html",
            error=error,
            stats=stats,
            recent_signals=recent_signals,
            page_title="Create Free Account",
            seo=_private_seo(
                g, "Create Free Account", "Save opportunities and set preferences."
            ),
        )

    @app.route("/auth/google", methods=["GET", "POST"])
    def auth_google() -> Any:
        """Legacy Google entry point. It no longer authenticates anyone.

        This route previously accepted an email address from a query parameter and started a
        session as that account, which allowed anyone who knew an address to sign in as its
        owner. Google sign-in is now performed entirely in the browser (Firebase), which posts
        a signed ID token to `/auth/firebase-verify`; the server trusts only that verified
        token. Any identity a client supplies here is ignored.
        """
        if g.user:
            return redirect(url_for("home"))
        flash("Continue with Google from the sign-in page to authenticate.", "info")
        return redirect(url_for("signin"))

    @app.route("/auth/firebase-verify", methods=["POST"])
    def auth_firebase_verify() -> Any:
        """Verify a Firebase / Google ID token server-side and create the session.

        Identity is taken only from the verified token. No email parameter, form field or other
        client-supplied value is ever used to decide who the caller is.
        """
        if g.user:
            return jsonify({"ok": True, "redirect": url_for("dashboard")})

        data = request.get_json(silent=True) or request.form
        id_token = data.get("id_token")
        if not id_token:
            return jsonify({"ok": False, "error": "Missing Firebase ID token."}), 400

        try:
            claims = _verify_firebase_id_token(id_token)
        except FirebaseVerificationError:
            return jsonify({"ok": False, "error": "Invalid or expired Firebase token."}), 401

        email = claims["email"]
        google_id = claims["sub"]
        display_name = claims.get("name")

        try:
            user, is_new = g.accounts.authenticate_or_link_google(
                email,
                google_id,
                display_name,
                market=g.market,
                trade=g.trade,
            )
            _start_session(user.id)
            record_analytics(g.db, "signin_google_firebase", market_id=g.market.id, trade_id=g.trade.id)
            next_url = _safe_redirect(request.args.get("next") or url_for("dashboard"))
            return jsonify({"ok": True, "redirect": next_url})
        except AuthError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password() -> Any:
        if g.user:
            return redirect(url_for("home"))
        error = None
        success = None
        if request.method == "POST":
            email = (request.form.get("email") or "").strip()
            if not email:
                error = "Please enter your work email address."
            else:
                # A token is generated only for a real account, and it is delivered out of band
                # by the mailer — never rendered, logged or returned here, because exposing it
                # would let anyone reset a password they do not own. Delivery happens only when
                # an account exists, so it cannot become an enumeration oracle.
                issued = g.accounts.create_password_reset_token(email)
                if issued is not None:
                    token, user = issued
                    try:
                        g.mailer.send_reset(to_email=user.email, token=token)
                    except DeliveryError:
                        # A delivery failure is logged server-side without the token, and the
                        # visitor still sees the same neutral message, so the form reveals
                        # nothing about whether the address is registered.
                        log.exception("Password reset delivery failed")
                success = "If an active account exists for that email, password reset instructions have been generated."
        return render_template(
            "account/forgot_password.html",
            error=error,
            success=success,
            page_title="Reset Password",
            seo=_private_seo(g, "Reset Password", "Reset your BuildScope account password."),
        )

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token: str) -> Any:
        if g.user:
            return redirect(url_for("home"))
        user = g.accounts.verify_reset_token(token)
        if not user:
            return render_template(
                "account/reset_password.html",
                invalid_token=True,
                error="Password reset link is invalid or has expired.",
                page_title="Reset Password",
                seo=_private_seo(g, "Reset Password", "Reset your BuildScope account password."),
            )
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            password_confirm = request.form.get("password_confirm", "")
            if password != password_confirm:
                error = "Passwords do not match."
            else:
                try:
                    g.accounts.reset_password_with_token(token, password)
                    flash("Password has been successfully updated. You may now sign in.", "info")
                    return redirect(url_for("signin"))
                except AuthError as exc:
                    error = str(exc)
        return render_template(
            "account/reset_password.html",
            token=token,
            user=user,
            error=error,
            page_title="Set New Password",
            seo=_private_seo(g, "Set New Password", "Set a new password for your account."),
        )

    @app.route("/signout", methods=["POST", "GET"])
    def signout() -> Any:
        session.clear()
        return redirect(url_for("home"))

    @app.route("/saved")
    def saved() -> Any:
        if not g.user:
            return redirect(url_for("signin", next=url_for("saved")))
        ids = g.accounts.saved_project_ids(g.user.id)
        items = []
        for project_id in ids:
            slug = _slug_for(g.db, project_id)
            if not slug:
                continue
            project = g.service.get_by_slug(slug)
            if project:
                items.append(project)
        return render_template(
            "account/saved.html",
            items=items,
            page_title="Saved Opportunities",
            seo=_private_seo(g, "Saved Opportunities", "Your saved opportunities."),
        )

    @app.route("/saved/<int:project_id>", methods=["POST"])
    def save_opportunity(project_id: int) -> Any:
        if not g.user:
            if request.headers.get("Accept", "").startswith("application/json"):
                return {"ok": False, "reason": "auth_required"}, 401
            return redirect(url_for("signin", next=request.referrer or url_for("opportunities")))

        action = request.form.get("action", "save")
        if action == "remove":
            g.accounts.unsave_opportunity(g.user.id, project_id)
            saved_now = False
        else:
            saved_now = g.accounts.save_opportunity(g.user.id, project_id)
            if saved_now:
                record_analytics(
                    g.db, "opportunity_saved", project_id=project_id,
                    market_id=g.market.id, trade_id=g.trade.id,
                )

        if request.headers.get("Accept", "").startswith("application/json"):
            return {"ok": True, "saved": saved_now}
        if request.form.get("redirect") == "saved":
            return redirect(url_for("saved"))
        return redirect(request.referrer or url_for("opportunities"))

    @app.route("/preferences", methods=["GET", "POST"])
    def preferences() -> Any:
        if not g.user:
            return redirect(url_for("signin", next=url_for("preferences")))
        if request.method == "POST":
            g.accounts.update_preferences(
                g.user.id,
                market_id=request.form.get("market_id") or g.market.id,
                trade_id=request.form.get("trade_id") or g.trade.id,
                cities=request.form.getlist("cities"),
                project_types=request.form.getlist("project_types"),
                notify_in_app=bool(request.form.get("notify_in_app")),
                notify_email=bool(request.form.get("notify_email")),
            )
            flash("Preferences saved.", "success")
            return redirect(url_for("preferences"))
        return render_template(
            "account/preferences.html",
            prefs=g.accounts.get_preferences(g.user.id),
            cities=g.service.available_cities(),
            project_types=g.service.available_project_types(),
            page_title="Preferences",
            seo=_private_seo(g, "Preferences", "Your market and trade preferences."),
        )

    # =====================================================================
    # Dashboard and account working views
    # =====================================================================

    @app.route("/dashboard")
    def dashboard() -> Any:
        """The account's working centre.

        Everything here is a real query over this account's own rows. A signed-out visitor is
        sent to sign-in rather than shown a demo dashboard, because an empty dashboard that
        looks populated is the kind of thing this product exists not to do.
        """
        if not g.user:
            return redirect(url_for("signin", next=url_for("dashboard")))

        summary = g.workflow.summary(g.user.id)
        prefs = g.accounts.get_preferences(g.user.id)

        # New matches: recent, discoverable, pre-filtered by the account's hard preferences,
        # then annotated with the reasons each one matches. This is the personalized feed.
        from .matching import evaluate_match, filter_matches

        candidates = g.service.projects_by_ids(
            g.service.recent_discoverable_ids(limit=200)
        )
        matched = filter_matches(candidates, preferences=prefs)
        for project in matched:
            result = evaluate_match(
                project, trade=g.trade, market=g.market, preferences=prefs
            )
            project["match_reasons"] = result.reasons
            project["match_score"] = result.score
        new_matches = matched[:6]

        # Recently updated: projects with a recorded change in the window, not merely a
        # pipeline re-run, so the section means what it says.
        updated_ids = g.service.changed_project_ids(limit=6, days=30)
        recently_updated = g.service.projects_by_ids(updated_ids)

        watching = g.service.projects_by_ids(g.workflow.watched_ids(g.user.id))[:6]
        saved_ids = g.accounts.saved_project_ids(g.user.id)
        saved = g.service.projects_by_ids(saved_ids)[:6]

        pipeline_rows = g.workflow.pipeline_rows(g.user.id)
        stage_by_id = {int(r["project_id"]): r["stage"] for r in pipeline_rows}
        pipeline_projects = g.service.projects_by_ids(list(stage_by_id))[:8]
        for project in pipeline_projects:
            project["stage"] = stage_by_id.get(int(project["id"]))

        # Needs review: pipeline entries still at the entry stage, which is the account's own
        # backlog rather than a claim about any project.
        review_ids = g.workflow.stage_project_ids(g.user.id, "NEW")
        needs_review = g.service.projects_by_ids(review_ids)[:6]

        return render_template(
            "dashboard.html",
            summary=summary,
            new_matches=new_matches,
            recently_updated=recently_updated,
            watching=watching,
            saved=saved,
            pipeline_projects=pipeline_projects,
            needs_review=needs_review,
            prefs=prefs,
            stages=PIPELINE_STAGES,
            unread_alerts=g.alerts.unread_count(g.user.id),
            page_title="Dashboard",
            seo=_private_seo(g, "Dashboard", "Your opportunity workspace."),
        )

    @app.route("/watching")
    def watching() -> Any:
        """Opportunities the account is monitoring for change."""
        if not g.user:
            return redirect(url_for("signin", next=url_for("watching")))
        ids = g.workflow.watched_ids(g.user.id)
        items = g.service.projects_by_ids(ids)
        change_counts = _change_counts_for(g.db, ids)
        return render_template(
            "account/watching.html",
            items=items,
            change_counts=change_counts,
            page_title="Watching",
            seo=_private_seo(g, "Watching", "Opportunities you are monitoring."),
        )

    @app.route("/my-pipeline")
    def my_pipeline() -> Any:
        """The account's own workflow over opportunities, grouped by stage.

        The page states plainly that a stage is the account's working state and not the
        project's procurement status, because the two vocabularies must never be conflated.
        """
        if not g.user:
            return redirect(url_for("signin", next=url_for("my_pipeline")))
        rows = g.workflow.pipeline_rows(g.user.id)
        stage_by_id = {int(r["project_id"]): r for r in rows}
        projects = g.service.projects_by_ids(list(stage_by_id))
        for project in projects:
            row = stage_by_id.get(int(project["id"]), {})
            project["stage"] = row.get("stage")
            project["follow_up_date"] = row.get("follow_up_date")
            project["assigned_to"] = row.get("assigned_to")

        columns: list[dict[str, Any]] = []
        for key, label in PIPELINE_STAGES:
            stage_items = [p for p in projects if p.get("stage") == key]
            # Named `entries`, not `items`: `items` on a dict resolves to the built-in method
            # inside a Jinja attribute lookup, so `column.items | length` would fail.
            columns.append({"key": key, "label": label, "entries": stage_items})

        return render_template(
            "account/pipeline.html",
            columns=columns,
            total=len(projects),
            peers=g.workflow.peers_for(g.user.id),
            stages=PIPELINE_STAGES,
            page_title="My Pipeline",
            seo=_private_seo(g, "My Pipeline", "Opportunities you are working."),
        )

    @app.route("/pipeline/<int:project_id>", methods=["POST"])
    def pipeline_update(project_id: int) -> Any:
        """Move an opportunity within the account's pipeline, or set a follow-up date."""
        if not g.user:
            return _json_or_redirect_unauth()
        action = request.form.get("action", "stage")
        try:
            if action == "remove":
                g.workflow.remove_from_pipeline(g.user.id, project_id)
            elif action == "follow_up":
                g.workflow.set_follow_up(
                    g.user.id, project_id, request.form.get("follow_up_date") or None
                )
            elif action == "assign":
                raw = request.form.get("assigned_to") or ""
                g.workflow.assign(
                    g.user.id, project_id, int(raw) if raw.isdigit() else None
                )
            else:
                g.workflow.set_stage(
                    g.user.id, project_id,
                    request.form.get("stage") or DEFAULT_STAGE,
                    follow_up_date=request.form.get("follow_up_date") or None,
                )
        except (WorkflowError, ValueError) as exc:
            if _wants_json():
                return {"ok": False, "error": str(exc)}, 400
            flash(str(exc), "error")
            return redirect(request.referrer or url_for("my_pipeline"))

        record_analytics(
            g.db, "pipeline_updated", project_id=project_id,
            market_id=g.market.id, trade_id=g.trade.id,
        )
        if _wants_json():
            return {
                "ok": True,
                "stage": g.workflow.stage_for(g.user.id, project_id),
            }
        return redirect(request.form.get("redirect") or request.referrer or url_for("my_pipeline"))

    @app.route("/watching/<int:project_id>", methods=["POST"])
    def watch_opportunity(project_id: int) -> Any:
        """Start or stop monitoring an opportunity. A POST so a crawler cannot change state."""
        if not g.user:
            return _json_or_redirect_unauth()
        if request.form.get("action") == "remove":
            g.workflow.unwatch(g.user.id, project_id)
            watching_now = False
        else:
            watching_now = g.workflow.watch(g.user.id, project_id)
            if watching_now:
                record_analytics(
                    g.db, "opportunity_watched", project_id=project_id,
                    market_id=g.market.id, trade_id=g.trade.id,
                )
        if _wants_json():
            return {"ok": True, "watching": watching_now}
        return redirect(request.form.get("redirect") or request.referrer or url_for("opportunities"))

    @app.route("/notes/<int:project_id>", methods=["POST"])
    def add_note(project_id: int) -> Any:
        """Attach a private note to an opportunity."""
        if not g.user:
            return _json_or_redirect_unauth()
        try:
            g.workflow.add_note(g.user.id, project_id, request.form.get("body", ""))
        except WorkflowError as exc:
            if _wants_json():
                return {"ok": False, "error": str(exc)}, 400
            flash(str(exc), "error")
            return redirect(request.referrer or url_for("opportunities"))
        if _wants_json():
            return {"ok": True}
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/notes/<int:note_id>/delete", methods=["POST"])
    def delete_note(note_id: int) -> Any:
        """Delete one of the account's own notes."""
        if not g.user:
            return _json_or_redirect_unauth()
        g.workflow.delete_note(g.user.id, note_id)
        if _wants_json():
            return {"ok": True}
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/tags/<int:project_id>", methods=["POST"])
    def tag_opportunity(project_id: int) -> Any:
        """Add or remove one of the account's own tags on an opportunity."""
        if not g.user:
            return _json_or_redirect_unauth()
        tag = request.form.get("tag", "")
        try:
            if request.form.get("action") == "remove":
                g.workflow.remove_tag(g.user.id, project_id, tag)
            else:
                g.workflow.add_tag(g.user.id, project_id, tag)
        except WorkflowError as exc:
            if _wants_json():
                return {"ok": False, "error": str(exc)}, 400
            flash(str(exc), "error")
        if _wants_json():
            return {"ok": True, "tags": g.workflow.tags_for(g.user.id, project_id)}
        return redirect(request.referrer or url_for("opportunities"))

    @app.route("/alerts")
    def alerts() -> Any:
        """Event-driven alerts for this account.

        Every row shown here traces to a recorded change or a first-match event; the page says
        so, and the operations view asserts it independently.
        """
        if not g.user:
            return redirect(url_for("signin", next=url_for("alerts")))
        unread_only = request.args.get("unread") in ("1", "true", "on")
        items = g.alerts.for_user(g.user.id, unread_only=unread_only)
        return render_template(
            "account/alerts.html",
            items=items,
            unread_count=g.alerts.unread_count(g.user.id),
            unread_only=unread_only,
            page_title="Alerts",
            seo=_private_seo(g, "Alerts", "Changes to the opportunities you watch."),
        )

    @app.route("/alerts/<int:alert_id>/read", methods=["POST"])
    def mark_alert_read(alert_id: int) -> Any:
        if not g.user:
            return _json_or_redirect_unauth()
        g.alerts.mark_read(g.user.id, alert_id)
        if _wants_json():
            return {"ok": True, "unread": g.alerts.unread_count(g.user.id)}
        return redirect(request.referrer or url_for("alerts"))

    @app.route("/alerts/read-all", methods=["POST"])
    def mark_all_alerts_read() -> Any:
        if not g.user:
            return _json_or_redirect_unauth()
        g.alerts.mark_all_read(g.user.id)
        if _wants_json():
            return {"ok": True, "unread": 0}
        return redirect(url_for("alerts"))

    # =====================================================================
    # Internal operations. Not linked publicly and marked noindex.
    # =====================================================================

    @app.route("/admin/data")
    @_require_admin
    def admin_data() -> Any:
        """A read-only operations view, restricted to operators.

        Requires an account with the ADMIN access level. An unauthenticated request is sent to
        sign-in; a signed-in non-operator receives 403. The level is granted only through the
        CLI, so no web request can escalate to it.

        The page shows aggregate counts, source coverage, data-quality findings, monitoring and
        alert integrity, and recent application errors. It deliberately exposes no credentials,
        no outbound source URLs, no ingestion or connector endpoints, no database paths and no
        user data — so even an operator cannot read anything here that would be dangerous if
        the page were reached.
        """
        from ..reporting import data_quality_report
        from .search_analytics import analytics_report

        from ..intelligence import integrity_report

        stats = g.service.market_statistics()
        freshness = g.service.data_freshness()
        health = _system_health(g.db)
        return render_template(
            "admin/data.html",
            stats=stats,
            freshness=freshness,
            quality_report=data_quality_report(g.db),
            coverage=all_market_coverage(g.db),
            search_report=analytics_report(g.db),
            health=health,
            intelligence=integrity_report(g.db),
            page_title="Data operations",
            seo=_private_seo(g, "Data operations", "Internal operations view."),
        )

    @app.before_request
    def attach_seo() -> None:
        """Attach per-request configuration and the SEO builder.

        Bound here rather than at app creation, because the market and trade can differ per
        request once multi-market routing exists, and a builder captured at import time would
        describe the wrong market.
        """
        g.seo_builder = SeoBuilder(cfg, current_market(), current_trade())
        g.seo_for_home = g.seo_builder.home
        g.seo_for_directory = g.seo_builder.directory
        g.seo_for_opportunity = g.seo_builder.opportunity
        g.seo_for_market = g.seo_builder.market_page
        g.seo_for_city = g.seo_builder.city_page
        g.seo_for_trade = g.seo_builder.trade_page
        g.seo_for_market_trade = g.seo_builder.market_trade_page
        g.seo_for_simple = g.seo_builder.simple

    # The JSON API is registered after the pages so a route name collision would be caught
    # at import time rather than silently shadowing a page.
    from .api import bp as api_bp

    app.register_blueprint(api_bp)

    # CSRF, rate limiting and response headers. Installed after the routes so every route it
    # protects is already registered, and before the first request either way.
    install_security(app, enabled=cfg.csrf_enabled)

    # Repair a missing/stale stats snapshot once, at boot, outside the request path: a deploy
    # that adds a canonical metric must not make the first visitor's GET perform the write.
    _heal_stat_snapshots_on_startup(cfg)

    _register_cli(app, cfg)
    return app


# --- module helpers -----------------------------------------------------------


def _refresh_stat_snapshots(db: Database) -> dict[str, Any]:
    """Recompute and store the public headline statistics for every active market+trade.

    Called at the end of a refresh (``build-search-index`` is the last pipeline step) so one
    snapshot covers a whole refresh cycle. Runs under the single-writer lock so it cannot race
    a concurrent ingest, and is idempotent: re-running with unchanged data writes the same
    numbers with a new ``computed_at``.
    """
    from .stat_snapshot import write_snapshot
    from oppintel.locks import writer_lock

    written: dict[str, Any] = {}
    with writer_lock(db.path):
        for market in load_markets().values():
            if not market.active:
                continue
            for trade in _trade_map().values():
                if not trade.active:
                    continue
                written[f"{market.id}:{trade.id}"] = write_snapshot(db, market, trade)
    return written


def _heal_stat_snapshots_on_startup(cfg: AppConfig) -> None:
    """Repair a missing or stale stat snapshot at boot, outside the request path.

    A deploy that adds a canonical metric leaves the stored snapshot (written by the previous
    code) missing that key. Healing here — once, under the writer lock — means no visitor's
    page load has to write. Never fatal: a locked or read-only database must not stop the app
    from serving, so a failure is logged and the read path recomputes without persisting.
    """
    from .stat_snapshot import heal_snapshot
    from oppintel.locks import writer_lock

    try:
        db = Database(cfg.database_path)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("startup snapshot heal skipped: %s", exc)
        return
    try:
        db.init_schema()
        db.init_app_schema()
        with writer_lock(cfg.database_path, timeout=60):
            for market in load_markets().values():
                if not market.active:
                    continue
                for trade in _trade_map().values():
                    if not trade.active:
                        continue
                    heal_snapshot(db, market, trade)
    except Exception as exc:  # pragma: no cover - a locked DB must not stop startup
        log.warning("startup snapshot heal skipped: %s", exc)
    finally:
        db.close()


def _service(
    db: Database, market: MarketConfig, trade: TradeConfig, user: Any
) -> OpportunityService:
    """Build the read service for a request, bound to the signed-in account when there is one.

    Centralised so every route constructs the service the same way and a new route cannot
    forget to pass the viewer, which would silently drop the save/watch state from its cards.
    """
    return OpportunityService(
        db, market, trade, viewer_id=getattr(user, "id", None)
    )


def _active(markets: dict[str, MarketConfig]) -> MarketConfig:
    for market in markets.values():
        if market.active:
            return market
    raise RuntimeError("No active market configured")


def _trade_map() -> dict[str, TradeConfig]:
    return load_trades()


def _active_trade() -> TradeConfig:
    for trade in load_trades().values():
        if trade.active:
            return trade
    raise RuntimeError("No active trade configured")


#: Markets are read once at import; `create_app` re-reads so a config change is picked up in
#: tests that patch configuration.
markets = load_markets()


def _interpret_search_query(q: str | None, available_cities: list[str]) -> dict[str, Any] | None:
    """Translate natural language or structured search expressions into explicit filters.

    Every translation is made explicit in the UI, keeping intelligence search grounded
    in the database rather than inventing unstated criteria.
    """
    if not q or not isinstance(q, str):
        return None
    raw = q.strip()
    market = getattr(g, "market", None)
    trade = getattr(g, "trade", None)
    interpreter = StructuredSearchInterpreter(market, trade)
    parsed = interpreter.parse(raw)
    if not parsed.is_structured:
        return None

    res = parsed.to_dict()
    res["has_structured_intent"] = True
    res["city"] = parsed.extracted_filters.get("city")
    res["project_type"] = parsed.extracted_filters.get("project_type")
    res["classification"] = parsed.extracted_filters.get("classification")
    res["mechanical_only"] = parsed.extracted_filters.get("mechanical_only")
    res["freshness_days"] = parsed.extracted_filters.get("freshness_days")
    res["min_value"] = parsed.extracted_filters.get("min_value")
    res["clean_q"] = parsed.clean_keyword
    res["filters"] = {b.label: b.value for b in parsed.badges}
    return res


def _filters_from_request(args: Any) -> OpportunityFilters:
    return OpportunityFilters(
        q=args.get("q"),
        city=args.get("city"),
        project_type=args.get("project_type"),
        classification=args.get("classification"),
        procurement_status=args.get("procurement_status"),
        date_from=args.get("date_from"),
        date_to=args.get("date_to"),
        include_unverified=args.get("include_unverified") in ("1", "true", "on"),
        mechanical_only=args.get("mechanical_only") in ("1", "true", "on"),
        sort=args.get("sort") or DEFAULT_SORT,
        page=_safe_int(args.get("page"), 1),
        page_size=_safe_int(args.get("page_size"), 20),
    ).normalised()


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _session_token() -> str:
    """A random, per-visit analytics token.

    Not a user id and not derived from one: it exists so a sequence of searches in one visit can
    be grouped for funnel measurement. It lives in the signed session cookie and is discarded
    when the browser session ends.
    """
    token = session.get("analytics_session")
    if not token:
        token = secrets.token_urlsafe(12)
        session["analytics_session"] = token
    return token


#: Campaign tags recognised on landing. Anything else is ignored rather than stored, so the
#: column cannot be filled with arbitrary text.
_CAMPAIGN_KEYS = ("utm_campaign", "campaign", "ref")
_CAMPAIGN_MAX = 80


def _campaign_from_request() -> str | None:
    """The campaign a visit arrived under, from a recognised tag, remembered for the session.

    Stored so a search can be attributed to the channel that produced it without a third-party
    tracker. The value is bounded and whitespace-collapsed.
    """
    for key in _CAMPAIGN_KEYS:
        value = request.args.get(key)
        if value:
            cleaned = " ".join(str(value).split())[:_CAMPAIGN_MAX]
            if cleaned:
                session["analytics_campaign"] = cleaned
                return cleaned
    return session.get("analytics_campaign")


def _filter_summary(filters: OpportunityFilters) -> str | None:
    """The names of the filters a search actually applied, sorted, comma-joined.

    Only non-default filters are listed, so the summary reports deliberate narrowing rather than
    the always-present defaults.
    """
    names: list[str] = []
    if filters.q:
        names.append("query")
    if filters.city:
        names.append("city")
    if filters.project_type:
        names.append("project_type")
    if filters.classification:
        names.append("classification")
    if filters.procurement_status:
        names.append("procurement_status")
    if filters.date_from or filters.date_to:
        names.append("date_range")
    if filters.mechanical_only:
        names.append("mechanical_only")
    if filters.include_unverified:
        names.append("include_unverified")
    if filters.min_value or filters.max_value:
        names.append("value_range")
    return ",".join(sorted(names)) or None


def _date_presets() -> dict[str, str]:
    """Native date-input presets for the permit-date range.

    Computed server-side so the preset links work with no JavaScript, and so each preset resolves
    to a real calendar date the database can compare rather than a relative token.
    """
    today = date.today()
    return {
        "last_7": (today - timedelta(days=7)).isoformat(),
        "last_30": (today - timedelta(days=30)).isoformat(),
        "this_year": date(today.year, 1, 1).isoformat(),
    }


def _active_filter_count(filters: OpportunityFilters) -> int:
    """How many deliberate filters a request applied.

    Counts only narrowing choices the visitor made, so the mobile "Filters (n)" button reflects
    the real state of the form. Sort and pagination are excluded because neither narrows results.
    A permit-date range counts once even when both ends are set.
    """
    return sum(
        [
            bool(filters.q),
            bool(filters.city),
            bool(filters.project_type),
            bool(filters.classification),
            bool(filters.procurement_status),
            bool(filters.date_from or filters.date_to),
            bool(filters.mechanical_only),
            bool(filters.include_unverified),
            bool(filters.min_value or filters.max_value),
        ]
    )


def _wants_json() -> bool:
    """Whether the caller expects a JSON reply rather than a redirect.

    A fetch/XHR caller sends an `Accept` header naming JSON; a browser form post does not. The
    decision is made from the header alone, never from a query parameter, so a hostile page
    cannot force a JSON response into a browser navigation.
    """
    return request.headers.get("Accept", "").startswith("application/json")


def _json_or_redirect_unauth() -> Any:
    """Reply to an unauthenticated mutation, matching the caller's expectation."""
    if _wants_json():
        return {"ok": False, "reason": "auth_required"}, 401
    return redirect(url_for("signin", next=request.path))


def _gate_for(page_id: str, stats: dict[str, Any], label: str) -> Any:
    """Evaluate a page's programmatic-SEO quality gate.

    Thresholds come from `config/keywords.yaml`, so tightening or loosening what deserves
    indexing is a configuration change rather than a code change. A page id that is missing
    from the map falls back to the module defaults rather than being treated as ungated, so an
    unregistered page cannot accidentally become indexable.
    """
    from ..config import load_keyword_map
    from .seo_gate import evaluate_gate

    page = load_keyword_map().page(page_id)
    thresholds = page.quality_gate if page else {}
    return evaluate_gate(stats=stats, quality_gate=thresholds, page_label=label)


def _change_counts_for(db: Database, project_ids: list[int]) -> dict[int, int]:
    """Recorded change counts for a set of projects, for the watching view.

    A count of real change rows, so "3 changes" on the page means three detected differences
    and nothing more.
    """
    if not project_ids:
        return {}
    placeholders = ",".join("?" for _ in project_ids)
    rows = db.conn.execute(
        f"""
        SELECT project_id, COUNT(*) AS n FROM project_change
         WHERE project_id IN ({placeholders})
         GROUP BY project_id
        """,
        list(project_ids),
    ).fetchall()
    return {int(r["project_id"]): int(r["n"]) for r in rows}


def _system_health(db: Database) -> dict[str, Any]:
    """Aggregate operational health, from stored rows only.

    Every figure is a count or a timestamp the database already holds. Nothing is estimated,
    and the page renders an explicit "not measured" for a metric the schema cannot support, so
    an operator never reads a plausible-looking number that nothing produced.
    """
    from .alerts import AlertService
    from ..monitoring import monitoring_report

    def scalar(sql: str, params: tuple = ()) -> int:
        return int(db.conn.execute(sql, params).fetchone()[0])

    # Source health: the last run per source, with its outcome.
    runs = [
        dict(r)
        for r in db.conn.execute(
            """
            SELECT r.source_id, s.name, r.status, r.started_at, r.finished_at,
                   r.rows_fetched, r.rows_landed, r.permits_created, r.error
              FROM ingest_run r
              LEFT JOIN source s ON s.id = r.source_id
             WHERE r.id IN (SELECT MAX(id) FROM ingest_run GROUP BY source_id)
             ORDER BY r.source_id
            """
        ).fetchall()
    ]
    failing = [r for r in runs if r["status"] != "ok"]

    issues = db.quality_issues(limit=500)
    by_severity: dict[str, int] = {}
    for issue in issues:
        by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1

    alert_summary = AlertService(db).summary()
    monitoring = monitoring_report(db)

    return {
        "monitoring": monitoring,
        "sources": runs,
        "sources_failing": len(failing),
        "runs_recorded": scalar("SELECT COUNT(*) FROM ingest_run"),
        "changes_total": scalar("SELECT COUNT(*) FROM project_change"),
        "changes_recent": scalar(
            "SELECT COUNT(*) FROM project_change "
            "WHERE detected_at >= datetime('now', '-7 days')"
        ),
        "watched_total": scalar("SELECT COUNT(*) FROM watched_opportunity"),
        "pipeline_total": scalar("SELECT COUNT(*) FROM pipeline_entry"),
        "quality_issues": issues[:50],
        "quality_by_severity": by_severity,
        "quality_total": len(issues),
        "alerts_total": alert_summary["total"],
        "alerts_orphaned": alert_summary["orphaned"],
        "recent_errors": db.recent_app_errors(limit=20),
        "error_count": scalar("SELECT COUNT(*) FROM app_error"),
    }


def _start_session(user_id: int) -> None:
    session.clear()
    session["user_id"] = user_id
    session.permanent = False


def _safe_redirect(target: str | None) -> str:
    """Only allow same-site redirect targets.

    An open redirect that forwards to an attacker's domain is a phishing vector and would let
    a link on this site be used as a credible-looking hop.
    """
    from urllib.parse import urlparse

    if not target:
        return "/"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return "/"
    return target if parsed.path.startswith("/") else "/"


def _slug_for(db: Database, project_id: int) -> str | None:
    from ..slugs import slug_for_project

    return slug_for_project(db, project_id)


def _private_seo(g: Any, title: str, description: str):
    """Metadata for a page that must never be indexed.

    Built here rather than as a kwarg on `render_template`, because a route that already passes
    `seo=` would silently ignore a second `noindex` argument and the page would end up
    indexable despite the intent.
    """
    seo = g.seo_for_simple(title, description)
    seo.noindex = True
    return seo


def _freshness_label(db: Database | None) -> dict[str, Any]:
    if db is None:
        return {"display": "Not verified", "display_time": "Not verified", "retrieval_date": None}
    row = db.conn.execute(
        "SELECT MAX(retrieval_date) AS d, MAX(updated_at) AS u FROM source_coverage"
    ).fetchone()
    value = row["d"] if row else None
    # `updated_at` is the moment the collection run wrote coverage; `retrieval_date` is only the
    # calendar day. Prefer the timestamp so the label can state a time, falling back to the date
    # when a row predates the timestamp column.
    stamped = (row["u"] if row else None) or value
    return {
        "display": OpportunityService.format_month(value),
        "display_time": OpportunityService.format_date_time(stamped),
        "retrieval_date": value,
    }


def _detail_title(project: dict[str, Any], g: Any) -> str:
    name = project.get("project_name") or project.get("address") or "Opportunity"
    city = project.get("city") or ""
    return f"{name[:70]} — {city} {g.trade.short_label} Opportunity".strip()


def _require_admin(view: Any) -> Any:
    """Gate a view to authenticated operators.

    Three distinct outcomes, deliberately:

    * No session at all -> redirect to sign-in with `next` pointing back here, which is the
      behaviour a browser user expects from a private page.
    * Signed in but not an operator -> 403. The distinction matters: a signed-in customer is
      not a stranger to be sent round the sign-in loop again, and telling them plainly that
      the page is not for them is both clearer and more honest than a redirect that appears
      to do nothing.
    * Operator -> the view runs.

    This wraps the view rather than sitting in `before_request` so the check lives with the
    route it protects. A future private route cannot be added without visibly opting in.
    """
    from functools import wraps

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user = getattr(g, "user", None)
        if user is None:
            return redirect(url_for("signin", next=request.path))
        if not user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _register_cli(app: Flask, cfg: AppConfig) -> None:
    """Operational commands. The CLI in `oppintel.cli` remains the primary mechanism."""

    @app.cli.command("monitor")
    @click.option("--alerts/--no-alerts", default=True,
                  help="Also generate alerts for watched projects.")
    def monitor_command(alerts: bool) -> None:
        """Run the monitoring pass: detect changes and optionally raise alerts.

        Intended to run after each ingestion. Change detection also runs inline during
        assembly, so this command is idempotent: a second call with no new data creates
        nothing.
        """
        from .alerts import build_alerts

        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        try:
            if alerts:
                result = build_alerts(db)
                print(f"alerts created: {result['created']} (scanned {result['scanned']})")
            else:
                print("monitoring skipped")
        finally:
            db.close()

    @app.cli.command("seo-report")
    @click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
    def seo_report_command(as_json: bool) -> None:
        """Print the SEO quality audit.

        Reports keyword coverage, indexation decisions and the conversion funnel from stored
        data. It deliberately reports no ranking position: ranking has not been measured, and
        claiming it would be the same class of error as inventing a project value.
        """
        import json as _json

        from .seo_report import full_report

        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        try:
            report = full_report(db)
        finally:
            db.close()

        if as_json:
            print(_json.dumps(report, indent=2, default=str))
            return

        tech = report["technical"]
        print("SEO QUALITY REPORT")
        print("=" * 60)
        print(f"Mapped pages:            {tech['pages_in_map']}")
        print(f"Mapped keywords:         {tech['keywords_in_map']}")
        print(f"Duplicate primary kw:    {len(tech['duplicate_primary_keywords'])}")
        print(f"Duplicate titles:        {len(tech['duplicate_declared_titles'])}")
        print(f"Deferred keywords:       {len(tech['deferred_keywords'])}")
        print()
        print("Programmatic pages")
        print("-" * 60)
        for row in report["programmatic_pages"]:
            decision = "index" if row["indexable"] else "noindex"
            print(
                f"  [{decision:>7}] {row['page']:<52} "
                f"projects={row['observed']['projects']} "
                f"mechanical={row['observed']['mechanical']}"
            )
        print()
        print("Content")
        print("-" * 60)
        print(f"  published guides:      {report['content']['published_count']}")
        missing = report["content"]["topics_without_a_guide"]
        print(f"  mapped, not written:   {len(missing)}")
        for row in missing:
            print(f"    - {row['slug']} ({row['keyword']})")
        print()
        print("Product")
        print("-" * 60)
        print(f"  indexable opportunities: {report['product']['indexable_opportunities']}")
        print(f"  project-type pages:      {report['product']['project_type_count']}")
        print()
        print("Conversion funnel (all-time counts)")
        print("-" * 60)
        for row in report["funnel"]:
            print(f"  {row['label']:<44} {row['count']}")

    @app.cli.command("report-quality")
    def quality_command() -> None:
        """Print the current data-quality findings."""
        from ..quality import detect_quality_issues

        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        try:
            issues = db.quality_issues(limit=200)
            if not issues:
                print("No open data-quality issues.")
                return
            for issue in issues:
                print(f"[{issue['severity']}] {issue['issue_type']}: {issue['detail']}")
        finally:
            db.close()

    @app.cli.command("set-plan")
    @click.argument("email")
    @click.argument("plan_id")
    @click.option("--status", default="ACTIVE", help="Subscription status.")
    @click.option("--external-ref", default=None, help="Reference from an external billing system.")
    def set_plan_command(email: str, plan_id: str, status: str, external_ref: str | None) -> None:
        """Record an account's plan relationship.

        The only mechanism that grants a paid tier. Payment is not implemented, so this command
        is what an operator or an external billing integration calls once a subscription is
        real. There is deliberately no web route that reaches it.
        """
        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        try:
            from .entitlements import SubscriptionService

            service = SubscriptionService(db)
            service.ensure_plans()
            row = db.conn.execute(
                "SELECT id FROM app_user WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
            if row is None:
                print(f"No account found for {email!r}. Create it at /signup first.")
                raise SystemExit(1)
            try:
                service.set_subscription(
                    int(row["id"]), plan_id, status, external_ref=external_ref
                )
            except ValueError as exc:
                print(str(exc))
                raise SystemExit(1)
            print(f"Set {email} to plan {plan_id} ({status})")
        finally:
            db.close()

    @app.cli.command("init-app")
    def init_app_command() -> None:
        """Create the application tables without touching intelligence data."""
        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        from .entitlements import SubscriptionService

        SubscriptionService(db).ensure_plans()
        print(f"Application schema ready at {cfg.database_path}")

    @app.cli.command("build-search-index")
    def build_index_command() -> None:
        """Rebuild the search index and generate any missing slugs."""
        from ..search_index import rebuild_index
        from ..slugs import ensure_slugs

        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        print(f"slugs created: {ensure_slugs(db)}")
        print(f"projects indexed: {rebuild_index(db)}")
        _refresh_stat_snapshots(db)

    @app.cli.command("grant-admin")
    @click.argument("email")
    def grant_admin_command(email: str) -> None:
        """Grant the ADMIN access level to an existing account.

        The only way to become an operator. There is deliberately no web route that sets this
        level, so a request cannot escalate its own privileges and a compromised session
        cannot promote itself. Run it on the host that owns the database.
        """
        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        cursor = db.conn.execute(
            "UPDATE app_user SET access_level = 'ADMIN' WHERE email = ?",
            (email.strip().lower(),),
        )
        db.conn.commit()
        if cursor.rowcount == 0:
            print(f"No account found for {email!r}. Create it at /signup first.")
            raise SystemExit(1)
        print(f"Granted ADMIN to {email}")

    @app.cli.command("revoke-admin")
    @click.argument("email")
    def revoke_admin_command(email: str) -> None:
        """Return an operator account to the FREE level."""
        db = Database(cfg.database_path)
        db.init_schema()
        db.init_app_schema()
        cursor = db.conn.execute(
            "UPDATE app_user SET access_level = 'FREE' WHERE email = ?",
            (email.strip().lower(),),
        )
        db.conn.commit()
        if cursor.rowcount == 0:
            print(f"No account found for {email!r}.")
            raise SystemExit(1)
        print(f"Revoked ADMIN from {email}")
