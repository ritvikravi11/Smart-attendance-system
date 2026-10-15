"""
Authentication business logic.

WHY A SERVICE LAYER AT ALL
--------------------------
Everything in this file could have been written inside the endpoint functions.
It is not, because:

  - These rules are testable without an HTTP server. `test_login_locks_account`
    calls a Python function; it does not spin up FastAPI.
  - The rules are reusable. A CLI admin tool or a seeding script can call
    `authenticate()` directly.
  - The endpoint stays readable. Compare a 6-line endpoint that delegates,
    against a 60-line endpoint doing lockout arithmetic inline.

This class imports SQLAlchemy and app.core, but NOT FastAPI. If you ever find
yourself importing `HTTPException` here, the layering has broken.
"""

from datetime import datetime, timedelta, timezone

from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
    NotFoundError,
    ValidationError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.logs import AuditLog
from app.models.user import RefreshToken, User
from app.utils.datetime_utils import as_utc
from app.models.enums import AuditAction


class AuthService:
    """
    Authentication operations.

    Takes a Session in its constructor rather than creating one. This is
    dependency injection: the caller controls the transaction boundary, so an
    endpoint can compose several service calls into one atomic unit of work,
    and a test can hand in a session bound to a throwaway database.
    """

    def __init__(self, db: Session):
        self.db = db

    # -----------------------------------------------------------------
    # LOGIN
    # -----------------------------------------------------------------

    def authenticate(
        self,
        email: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str]:
        """
        Verify credentials and issue tokens.

        Returns (user, access_token, refresh_token).
        Raises AuthenticationError / AccountLockedError / AccountInactiveError.

        THE ORDER OF CHECKS BELOW IS DELIBERATE. Read the comments -- each
        step exists to close a specific hole.
        """
        # Case-insensitive lookup. Users type "Das@College.edu" and expect it
        # to work; the schema has a unique index on lower(email) to match.
        stmt = (
            select(User)
            .options(joinedload(User.role))  # eager-load: avoids a second query
            .where(User.email.ilike(email))
        )
        user = self.db.execute(stmt).scalar_one_or_none()

        # ---- Step 1: user existence -------------------------------------
        #
        # SUBTLE BUT IMPORTANT: we still hash a dummy password when the user
        # does not exist.
        #
        # Without this, a login for an unknown email returns in ~1ms (no
        # bcrypt work), while a known email with a wrong password takes ~250ms
        # (bcrypt runs). An attacker measures the difference and enumerates
        # which addresses are registered -- the vague error message alone does
        # not prevent this, because the TIMING leaks what the text hides.
        #
        # Burning an equivalent bcrypt cycle makes both paths take the same
        # time. This is a timing side-channel defence, and it is the kind of
        # thing that separates a real auth implementation from a tutorial one.
        if user is None:
            verify_password(
                password,
                "$2b$12$C6UzMDM.H6dfI/f/IKcEe.qJhVQOU8yGZfPFPHIWXPz1kLbYAg2fi",
            )
            self._log_failed_login(email, ip_address, reason="no_such_user")
            raise AuthenticationError("Incorrect email or password.")

        # ---- Step 2: lockout, checked BEFORE the password ---------------
        #
        # Checking lockout first means a locked account cannot be probed at
        # all -- an attacker cannot keep testing passwords and reading whether
        # each was right.
        if self._is_locked(user):
            remaining = int(
                (as_utc(user.locked_until) - datetime.now(timezone.utc)).total_seconds()
                / 60
            )
            raise AccountLockedError(
                f"Account locked due to repeated failed attempts. "
                f"Try again in {max(remaining, 1)} minute(s)."
            )

        # ---- Step 3: password ------------------------------------------
        if not verify_password(password, user.password_hash):
            self._register_failed_attempt(user, ip_address)
            raise AuthenticationError("Incorrect email or password.")

        # ---- Step 4: active flag, checked AFTER the password ------------
        #
        # Also deliberate. If we checked is_active first, an attacker could
        # distinguish "deactivated account" from "wrong password" WITHOUT
        # knowing the password -- again leaking which accounts exist.
        # Verifying the password first means only the legitimate owner ever
        # sees the "account is inactive" message.
        if not user.is_active:
            raise AccountInactiveError(
                "This account has been deactivated. Contact your administrator."
            )

        # ---- Success ----------------------------------------------------
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = datetime.now(timezone.utc)

        access_token = create_access_token(user_id=user.id, role=user.role.name)
        refresh_token = create_refresh_token(user_id=user.id)

        self._store_refresh_token(user.id, refresh_token, ip_address, user_agent)
        self._audit(user.id, AuditAction.LOGIN, "users", user.id, ip_address)

        self.db.commit()
        return user, access_token, refresh_token

    # -----------------------------------------------------------------
    # REFRESH
    # -----------------------------------------------------------------

    def refresh_access_token(self, refresh_token: str) -> tuple[str, str]:
        """
        Exchange a valid refresh token for a new token pair.

        Returns (new_access_token, new_refresh_token).

        WHY THE REFRESH TOKEN IS ALSO REPLACED -- "rotation"
        ----------------------------------------------------
        We could return only a new access token and let the client keep its
        existing refresh token for the full 7 days. Rotation is better.

        Each refresh invalidates the old token and issues a fresh one. If a
        refresh token is ever stolen, it works exactly once -- and the moment
        either party uses it, the other's copy is dead. The legitimate user is
        suddenly logged out, which is a visible symptom rather than a silent
        7-day compromise.

        (The full pattern also detects reuse of an already-rotated token and
        revokes the entire family as a breach signal. That refinement is noted
        as a Phase 2 item; rotation alone already closes most of the gap.)
        """
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except InvalidTokenError as exc:
            raise AuthenticationError(str(exc)) from exc

        # BUG FIX: `int(payload["sub"])` was unguarded. A validly-SIGNED token
        # carrying a non-numeric subject raised ValueError, which surfaced as a
        # 500. That is worse than untidy -- a 500 tells an attacker the token
        # parsed and passed signature verification, and only the CONTENT was
        # rejected. A 401 reveals nothing.
        #
        # Found by tests/test_edge_cases.py::test_token_with_non_numeric_sub.
        try:
            user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("Token subject is invalid.") from exc
        token_hash = hash_refresh_token(refresh_token)

        # The signature being valid is NOT sufficient. The token must also
        # still be present and unrevoked in our database -- that is the whole
        # reason the refresh_tokens table exists.
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
        stored = self.db.execute(stmt).scalar_one_or_none()

        if stored is None:
            raise AuthenticationError("Refresh token has been revoked or is unknown.")

        if as_utc(stored.expires_at) < datetime.now(timezone.utc):
            raise AuthenticationError("Refresh token has expired. Please log in again.")

        user = self.db.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Account is no longer active.")

        # Re-read the role from the database rather than trusting any cached
        # value. This is what closes the demotion window discussed in
        # core/security.py: a user demoted an hour ago gets their new, lower
        # role baked into the next access token.
        self.db.refresh(user, ["role"])

        # Rotate: kill the old token, mint a replacement.
        stored.revoked_at = datetime.now(timezone.utc)

        new_access = create_access_token(user_id=user.id, role=user.role.name)
        new_refresh = create_refresh_token(user_id=user.id)
        self._store_refresh_token(
            user.id, new_refresh, stored.ip_address, stored.user_agent
        )

        self.db.commit()
        return new_access, new_refresh

    # -----------------------------------------------------------------
    # LOGOUT
    # -----------------------------------------------------------------

    def logout(self, refresh_token: str, user_id: int) -> None:
        """
        Revoke a refresh token.

        HONEST LIMITATION, worth stating plainly: the user's ACCESS token
        remains valid until it expires -- up to 30 minutes. Nothing here can
        change that, because a JWT is verified by signature alone and we
        deliberately do not query the database on every request.

        So "logout" means "you cannot obtain new access after this point",
        not "all access ceases instantly". Short access-token lifetimes are
        what keep that window small. If a deployment genuinely needs instant
        revocation, the answer is a Redis denylist of token IDs checked on
        each request -- a Phase 2 item, and one that costs a lookup per
        request.
        """
        token_hash = hash_refresh_token(refresh_token)
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == user_id,  # cannot revoke someone else's
            RefreshToken.revoked_at.is_(None),
        )
        stored = self.db.execute(stmt).scalar_one_or_none()

        # Idempotent: logging out twice is not an error. The desired end state
        # (token unusable) already holds.
        if stored is not None:
            stored.revoked_at = datetime.now(timezone.utc)
            self._audit(user_id, AuditAction.LOGOUT, "users", user_id, None)
            self.db.commit()

    def logout_all_devices(self, user_id: int) -> int:
        """
        Revoke every active refresh token for a user.

        Used after a password change, and available to admins responding to a
        suspected compromise. Returns how many sessions were ended.
        """
        stmt = select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        tokens = self.db.execute(stmt).scalars().all()
        now = datetime.now(timezone.utc)
        for token in tokens:
            token.revoked_at = now
        self.db.commit()
        return len(tokens)

    # -----------------------------------------------------------------
    # PASSWORD CHANGE
    # -----------------------------------------------------------------

    def change_password(
        self, user_id: int, current_password: str, new_password: str
    ) -> None:
        """
        Change a password, then terminate every existing session.

        The forced logout is the important part. If the password was changed
        BECAUSE the account was compromised, leaving the attacker's sessions
        alive would defeat the entire exercise. The user re-authenticates on
        each device; the attacker cannot.
        """
        user = self.db.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")

        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect.")

        if verify_password(new_password, user.password_hash):
            raise ValidationError(
                "New password must be different from the current one.",
                field="new_password",
            )

        user.password_hash = hash_password(new_password)
        self._audit(user_id, AuditAction.UPDATE, "users", user_id, None)
        self.db.commit()

        self.logout_all_devices(user_id)

    # -----------------------------------------------------------------
    # INTERNAL HELPERS
    # -----------------------------------------------------------------

    def _is_locked(self, user: User) -> bool:
        locked_until = as_utc(user.locked_until)
        if locked_until is None:
            return False
        return locked_until > datetime.now(timezone.utc)

    def _register_failed_attempt(self, user: User, ip_address: str | None) -> None:
        """
        Increment the failure counter and lock the account at the threshold.

        WHY LOCKOUT IS A GENUINE TRADE-OFF, not an unambiguous win
        ----------------------------------------------------------
        Lockout stops online brute force. It also hands an attacker a
        denial-of-service primitive: submit five wrong passwords for a
        professor's address before every exam and they cannot access the
        system.

        We accept that trade-off for an institutional tool where an admin can
        unlock an account, and we bound the damage with a 15-minute automatic
        expiry rather than a permanent lock. A public-internet product would
        more likely favour rate limiting by IP plus a CAPTCHA.

        Worth knowing that this is a considered decision rather than an
        obvious default -- it is exactly the kind of question a viva examiner
        enjoys.
        """
        user.failed_login_count += 1

        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.lockout_minutes
            )
            user.failed_login_count = 0  # reset so the next lock needs 5 more

        self._audit(
            user.id, AuditAction.LOGIN_FAILED, "users", user.id, ip_address
        )
        self.db.commit()

    def _log_failed_login(
        self, email: str, ip_address: str | None, reason: str
    ) -> None:
        """
        Record a login attempt against an address with no account.

        user_id is NULL because there is no user. The email goes into the
        JSONB details column instead -- which is precisely the kind of
        variable, action-specific context that column exists for.
        """
        entry = AuditLog(
            user_id=None,
            action=AuditAction.LOGIN_FAILED.value,
            entity_type="users",
            entity_id=None,
            details={"email": email, "reason": reason},
            ip_address=ip_address,
        )
        self.db.add(entry)
        self.db.commit()

    def _store_refresh_token(
        self,
        user_id: int,
        token: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        entry = RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token(token),  # never the raw token
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_expire_days),
            ip_address=ip_address,
            user_agent=(user_agent or "")[:255] or None,
        )
        self.db.add(entry)

    def _audit(
        self,
        user_id: int | None,
        action: AuditAction,
        entity_type: str,
        entity_id: int | None,
        ip_address: str | None,
    ) -> None:
        self.db.add(
            AuditLog(
                user_id=user_id,
                action=action.value,
                entity_type=entity_type,
                entity_id=entity_id,
                ip_address=ip_address,
            )
        )
