"""Accounts, sessions and saved opportunities.

Deliberately minimal. The MVP needs a free account that can save opportunities and hold
preferences; it does not need roles, teams, invitations or password reset flows.

Security decisions worth stating:

* Passwords are hashed with `werkzeug.security` (PBKDF2-SHA256, per-password salt). A plain
  digest or a shared salt would be a real vulnerability in a system that stores email
  addresses.
* A password is never logged, echoed into a template, or written to the database in any form
  other than its hash.
* Session state holds only the user id. Everything else is re-read per request, so a revoked
  or deactivated account cannot keep acting on a stale session.
* Login failure messages are identical for "no such user" and "wrong password", so the form
  cannot be used to enumerate registered addresses.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from ..config import MarketConfig, TradeConfig
from ..db import Database

#: Minimum password length. Kept low because this is a free research tool, but not zero.
MIN_PASSWORD_LENGTH = 8

#: Deliberately permissive: enough to catch a typo, not enough to reject valid addresses.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """A user-facing authentication problem with a safe message."""


@dataclass
class User:
    id: int
    email: str
    display_name: str | None
    access_level: str
    created_at: str
    last_login_at: str | None
    google_id: str | None = None
    company: str | None = None
    role: str | None = None
    onboarding_completed: bool = False

    @property
    def is_pro(self) -> bool:
        """Whether this user holds a paid tier. Payment is not implemented, so this is
        currently always False; it exists so authorization checks do not need rewriting."""
        return self.access_level in ("PRO", "TEAM")

    @property
    def is_admin(self) -> bool:
        """Whether this user may reach the internal operations view.

        ADMIN is deliberately separate from the paid tiers: an operator is not a customer, and
        a subscription must never imply access to internal data. The level is set only by the
        CLI, so no web request can grant it.
        """
        return self.access_level == "ADMIN"

    @property
    def display(self) -> str:
        return self.display_name or self.email.split("@")[0]

    @property
    def initials(self) -> str:
        """Get 2-letter uppercase initials for profile avatars."""
        name = (self.display_name or self.email.split("@")[0]).strip()
        parts = name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        if len(name) >= 2:
            return name[:2].upper()
        return (name[:1] or "U").upper()


class AccountService:
    """Account creation, authentication and preferences."""

    def __init__(self, db: Database):
        self.db = db

    # --- validation -----------------------------------------------------------

    @staticmethod
    def validate_email(email: str) -> str:
        cleaned = (email or "").strip().lower()
        if not _EMAIL_RE.match(cleaned):
            raise AuthError("Enter a valid email address.")
        if len(cleaned) > 254:
            raise AuthError("Enter a valid email address.")
        return cleaned

    @staticmethod
    def validate_password(password: str) -> str:
        if not password or len(password) < MIN_PASSWORD_LENGTH:
            raise AuthError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        if len(password) > 256:
            raise AuthError("Password is too long.")
        return password

    # --- lookup ---------------------------------------------------------------

    def _row_to_user(self, row: Any) -> User:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        return User(
            id=int(row["id"]),
            email=row["email"],
            display_name=row["display_name"],
            access_level=row["access_level"],
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
            google_id=row["google_id"] if "google_id" in keys else None,
            company=row["company"] if "company" in keys else None,
            role=row["role"] if "role" in keys else None,
            onboarding_completed=bool(row["onboarding_completed"]) if "onboarding_completed" in keys else False,
        )

    def get_user(self, user_id: int | None) -> User | None:
        if not user_id:
            return None
        row = self.db.conn.execute(
            "SELECT * FROM app_user WHERE id = ? AND is_active = 1", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def email_exists(self, email: str) -> bool:
        return bool(
            self.db.conn.execute(
                "SELECT 1 FROM app_user WHERE email = ?", (email,)
            ).fetchone()
        )

    # --- lifecycle & authentication -------------------------------------------

    def authenticate_or_link_google(
        self,
        email: str,
        google_id: str,
        display_name: str | None,
        *,
        market: MarketConfig,
        trade: TradeConfig,
    ) -> tuple[User, bool]:
        """Sign in with a *verified* Google/Firebase identity.

        The caller is responsible for verifying the ID token; `google_id` must be the identity
        provider's own subject (`sub` / `localId`) and `email` the address from that verified
        token. This method never trusts an email on its own:

        * An account is matched by its linked `google_id` first, so a returning Google user
          signs in to the account they linked.
        * An existing **password** account is never claimed by email. Knowing a victim's address
          must not be enough to take over their account, so this raises `AuthError` instead of
          silently attaching the Google identity to it. Linking to an existing account is an
          authenticated action and is out of scope for this flow.
        * If an account already holds a *different* Google identity, it is refused rather than
          overwritten.
        """
        email = self.validate_email(email)
        google_id = (google_id or "").strip()
        if not google_id:
            raise AuthError("Google authentication did not provide an identity.")
        now = datetime.now(timezone.utc).isoformat()

        # 1. Match by the linked provider identity (the trusted key).
        row = self.db.conn.execute(
            "SELECT * FROM app_user WHERE google_id = ?", (google_id,)
        ).fetchone()

        # 2. No identity match: consider an account with the same email, but never claim it.
        if row is None:
            existing = self.db.conn.execute(
                "SELECT * FROM app_user WHERE email = ?", (email,)
            ).fetchone()
            if existing is not None:
                raise AuthError(
                    "An account already exists for that email. Sign in with your password, "
                    "or contact support to link Google to your account."
                )
            # Create a fresh account for this verified Google identity. An account that has no
            # password yet is not an existing credential account, so it is safe to create.
            return self._create_google_account(email, google_id, display_name, market, trade)

        user_id = int(row["id"])
        if not row["is_active"]:
            raise AuthError("This account is not active.")
        self.db.conn.execute(
            """
            UPDATE app_user
            SET display_name = COALESCE(display_name, ?),
                last_login_at = ?
            WHERE id = ?
            """,
            ((display_name or "").strip()[:80] or None, now, user_id),
        )
        self.db.conn.commit()
        user = self.get_user(user_id)
        return user, False  # type: ignore[return-value]

    def _create_google_account(
        self,
        email: str,
        google_id: str,
        display_name: str | None,
        market: MarketConfig,
        trade: TradeConfig,
    ) -> tuple[User, bool]:
        """Create an account backed only by a verified Google identity."""
        now = datetime.now(timezone.utc).isoformat()

        secure_placeholder_hash = generate_password_hash(secrets.token_urlsafe(32))
        cursor = self.db.conn.execute(
            """
            INSERT INTO app_user (email, password_hash, display_name, access_level,
                                  is_active, google_id, onboarding_completed, created_at, last_login_at)
            VALUES (?, ?, ?, 'FREE', 1, ?, 0, ?, ?)
            """,
            (
                email,
                secure_placeholder_hash,
                (display_name or "").strip()[:80] or None,
                google_id,
                now,
                now,
            ),
        )
        user_id = int(cursor.lastrowid)
        self.db.conn.execute(
            """
            INSERT INTO user_preference (user_id, market_id, trade_id, cities, project_types,
                                         notify_in_app, notify_email, updated_at)
            VALUES (?, ?, ?, '[]', '[]', 1, 0, ?)
            """,
            (user_id, market.id, trade.id, now),
        )
        self.db.conn.commit()
        user = self.get_user(user_id)
        return user, True  # type: ignore[return-value]

    def complete_onboarding(
        self,
        user_id: int,
        *,
        company: str | None = None,
        role: str | None = None,
        market_id: str | None = None,
        trade_id: str | None = None,
    ) -> User:
        """Mark onboarding as completed and record initial user profile."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute(
            """
            UPDATE app_user
            SET company = ?,
                role = ?,
                onboarding_completed = 1
            WHERE id = ?
            """,
            ((company or "").strip()[:100] or None, (role or "").strip()[:80] or None, user_id),
        )
        if market_id or trade_id:
            self.db.conn.execute(
                """
                UPDATE user_preference
                SET market_id = COALESCE(?, market_id),
                    trade_id = COALESCE(?, trade_id),
                    updated_at = ?
                WHERE user_id = ?
                """,
                (market_id, trade_id, now, user_id),
            )
        self.db.conn.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    def create_password_reset_token(self, email: str) -> tuple[str, User] | None:
        """Generate a secure token for password reset with 1 hour expiration."""
        try:
            cleaned = self.validate_email(email)
        except AuthError:
            return None
        row = self.db.conn.execute(
            "SELECT * FROM app_user WHERE email = ? AND is_active = 1", (cleaned,)
        ).fetchone()
        if not row:
            return None
        user = self._row_to_user(row)
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.db.conn.execute(
            "UPDATE app_user SET reset_token = ?, reset_token_expires_at = ? WHERE id = ?",
            (token, expires_at, user.id),
        )
        self.db.conn.commit()
        return token, user

    def verify_reset_token(self, token: str) -> User | None:
        """Check if reset token exists and has not expired."""
        if not token:
            return None
        row = self.db.conn.execute(
            "SELECT * FROM app_user WHERE reset_token = ? AND is_active = 1", (token,)
        ).fetchone()
        if not row:
            return None
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        expires_at_str = row["reset_token_expires_at"] if "reset_token_expires_at" in keys else None
        if not expires_at_str:
            return None
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.now(timezone.utc) > expires_at:
                return None
        except Exception:
            return None
        return self._row_to_user(row)

    def reset_password_with_token(self, token: str, new_password: str) -> User:
        """Validate token and set a new password."""
        user = self.verify_reset_token(token)
        if not user:
            raise AuthError("Password reset link is invalid or has expired.")
        password = self.validate_password(new_password)
        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute(
            """
            UPDATE app_user
            SET password_hash = ?,
                reset_token = NULL,
                reset_token_expires_at = NULL,
                last_login_at = ?
            WHERE id = ?
            """,
            (generate_password_hash(password), now, user.id),
        )
        self.db.conn.commit()
        return self.get_user(user.id)  # type: ignore[return-value]

    # --- lifecycle ------------------------------------------------------------

    def create_account(
        self,
        email: str,
        password: str,
        display_name: str | None,
        *,
        market: MarketConfig,
        trade: TradeConfig,
    ) -> User:
        """Create a free account and its default preferences.

        Preferences are seeded from the active market and trade rather than a literal, so a
        new user starts on whatever the product is currently configured to serve.
        """
        email = self.validate_email(email)
        password = self.validate_password(password)
        if self.email_exists(email):
            raise AuthError("An account with that email already exists.")

        now = datetime.now(timezone.utc).isoformat()
        cursor = self.db.conn.execute(
            """
            INSERT INTO app_user (email, password_hash, display_name, access_level,
                                  is_active, created_at)
            VALUES (?, ?, ?, 'FREE', 1, ?)
            """,
            (
                email,
                generate_password_hash(password),
                (display_name or "").strip()[:80] or None,
                now,
            ),
        )
        user_id = int(cursor.lastrowid)
        self.db.conn.execute(
            """
            INSERT INTO user_preference (user_id, market_id, trade_id, cities, project_types,
                                         notify_in_app, notify_email, updated_at)
            VALUES (?, ?, ?, '[]', '[]', 1, 0, ?)
            """,
            (user_id, market.id, trade.id, now),
        )
        self.db.conn.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    def authenticate(self, email: str, password: str) -> User:
        """Verify credentials, or raise a single generic error.

        The same message is used for an unknown address and a wrong password so the form
        cannot be used to discover which emails are registered.
        """
        generic = AuthError("Email or password is incorrect.")
        try:
            cleaned = self.validate_email(email)
        except AuthError:
            raise generic from None

        row = self.db.conn.execute(
            "SELECT * FROM app_user WHERE email = ?", (cleaned,)
        ).fetchone()
        if row is None or not check_password_hash(row["password_hash"], password or ""):
            raise generic
        if not row["is_active"]:
            raise AuthError("This account is not active.")

        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute(
            "UPDATE app_user SET last_login_at = ? WHERE id = ?", (now, row["id"])
        )
        self.db.conn.commit()
        return self._row_to_user(row)

    # --- preferences ----------------------------------------------------------

    def get_preferences(self, user_id: int) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT * FROM user_preference WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return {}
        prefs = dict(row)
        for key in ("cities", "project_types"):
            try:
                prefs[key] = json.loads(prefs.get(key) or "[]")
            except (TypeError, ValueError):
                prefs[key] = []
        return prefs

    def update_preferences(
        self, user_id: int, *, market_id: str, trade_id: str,
        cities: list[str], project_types: list[str],
        notify_in_app: bool, notify_email: bool,
        min_value: float | None = None, max_value: float | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute(
            """
            INSERT INTO user_preference (user_id, market_id, trade_id, cities, project_types,
                                         notify_in_app, notify_email, min_value, max_value,
                                         updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                market_id = excluded.market_id,
                trade_id = excluded.trade_id,
                cities = excluded.cities,
                project_types = excluded.project_types,
                notify_in_app = excluded.notify_in_app,
                notify_email = excluded.notify_email,
                min_value = excluded.min_value,
                max_value = excluded.max_value,
                updated_at = excluded.updated_at
            """,
            (
                user_id, market_id, trade_id,
                json.dumps(sorted(set(cities))), json.dumps(sorted(set(project_types))),
                1 if notify_in_app else 0, 1 if notify_email else 0,
                min_value, max_value, now,
            ),
        )
        self.db.conn.commit()

    # --- saved opportunities --------------------------------------------------

    def save_opportunity(self, user_id: int, project_id: int) -> bool:
        """Save the relationship only. Returns True when it was newly saved.

        No project fields are copied, so a re-ingest cannot leave a saved record holding a
        stale or contradictory fact.
        """
        exists = self.db.conn.execute(
            "SELECT 1 FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        if not exists:
            return False
        cursor = self.db.conn.execute(
            """
            INSERT OR IGNORE INTO saved_opportunity (user_id, project_id, saved_at)
            VALUES (?, ?, ?)
            """,
            (user_id, project_id, datetime.now(timezone.utc).isoformat()),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def unsave_opportunity(self, user_id: int, project_id: int) -> bool:
        cursor = self.db.conn.execute(
            "DELETE FROM saved_opportunity WHERE user_id = ? AND project_id = ?",
            (user_id, project_id),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def saved_project_ids(self, user_id: int) -> list[int]:
        rows = self.db.conn.execute(
            "SELECT project_id FROM saved_opportunity WHERE user_id = ? ORDER BY saved_at DESC",
            (user_id,),
        ).fetchall()
        return [int(r["project_id"]) for r in rows]

    # --- alerts (architecture only) -------------------------------------------

    def record_alert(self, user_id: int, project_id: int, kind: str = "new_match") -> None:
        """Record a matching event. Delivery is intentionally not implemented."""
        self.db.conn.execute(
            """
            INSERT OR IGNORE INTO alert_event (user_id, project_id, kind, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, project_id, kind, datetime.now(timezone.utc).isoformat()),
        )
        self.db.conn.commit()

    def unread_alert_count(self, user_id: int) -> int:
        return int(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM alert_event WHERE user_id = ? AND read_at IS NULL",
                (user_id,),
            ).fetchone()[0]
        )


def record_analytics(
    db: Database, event_name: str, *, project_id: int | None = None,
    market_id: str | None = None, trade_id: str | None = None,
) -> None:
    """Record a product event.

    Intentionally narrow: an event name, an optional project, and the market/trade. No IP
    address, no user agent, no free-text payload, and no link to a user account, so the
    analytics table cannot become a record of who looked at what.
    """
    db.conn.execute(
        """
        INSERT INTO analytics_event (event_name, project_id, market_id, trade_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_name, project_id, market_id, trade_id, datetime.now(timezone.utc).isoformat()),
    )
    db.conn.commit()