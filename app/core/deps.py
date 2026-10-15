"""
FastAPI dependencies: authentication and role-based access control.

WHAT A "DEPENDENCY" IS, IF YOU HAVE NOT MET THE PATTERN
--------------------------------------------------------
A FastAPI dependency is a function that runs BEFORE your endpoint and supplies
it with something. Declaring:

    def read_students(user: User = Depends(get_current_user)):

means FastAPI calls `get_current_user` first. If it raises, the endpoint never
executes. If it returns, the endpoint receives the value.

This is why authorisation lives here rather than as the first four lines of
every endpoint: it is impossible to forget. An endpoint either declares the
dependency or it does not, and that declaration is visible in the signature
during code review.

THE SECURITY PRINCIPLE THIS FILE IMPLEMENTS
-------------------------------------------
Authorisation is enforced SERVER-SIDE, always (NFR-2.3). The frontend hiding
an "Admin" menu item is a usability feature, not a security control -- anyone
can open devtools, or simply call the API with curl. The check that matters is
the one in this file.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import (
    AccountInactiveError,
    AuthenticationError,
    PermissionDeniedError,
)
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import RoleName
from app.models.user import User

# auto_error=False so a missing header reaches OUR handler rather than
# FastAPI's generic one. That way every 401 in the system has the same
# response shape, which is what makes a single frontend error handler viable.
bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_client_ip(request: Request) -> str | None:
    """
    Best-effort client IP.

    Behind a reverse proxy, request.client.host is the PROXY's address, not
    the user's. X-Forwarded-For carries the original -- but note it is a
    client-supplied header and therefore SPOOFABLE unless your proxy
    overwrites it. Fine for audit logging; never use it for access control.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    """
    Resolve the authenticated user from the Authorization header.

    This is the gate every protected endpoint passes through.

    WHY WE HIT THE DATABASE HERE, having just said tokens avoid that
    ----------------------------------------------------------------
    A fair objection. The token already carries the user ID and role, so we
    could skip the query entirely and build a user object from claims alone.
    Faster, and genuinely stateless.

    We query anyway, for one reason: DEACTIVATION MUST TAKE EFFECT IMMEDIATELY.
    If a student is caught proxying attendance and is deactivated, a
    claims-only check would let them keep operating for up to 30 minutes on
    their existing token. For a system whose entire purpose is preventing
    attendance fraud, that is the wrong trade.

    Cost is one indexed primary-key lookup -- sub-millisecond, and the
    connection is already open. Worth it.

    The 2 fps recognition endpoint pays this too, at ~2 lookups/second/client.
    Still negligible next to ~150ms of face inference.
    """
    if credentials is None:
        raise AuthenticationError("Not authenticated. Provide a Bearer token.")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except InvalidTokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Token contains an invalid subject.") from exc

    stmt = select(User).options(joinedload(User.role)).where(User.id == user_id)
    user = db.execute(stmt).scalar_one_or_none()

    if user is None:
        # Valid signature, but the user is gone. Treat as auth failure, not
        # 404 -- the caller should not learn whether a given ID ever existed.
        raise AuthenticationError("User no longer exists.")

    if not user.is_active:
        raise AccountInactiveError("This account has been deactivated.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


class RequireRole:
    """
    Dependency factory producing a role check.

    Usage:
        @router.post("/students", dependencies=[Depends(RequireRole("admin"))])

        # or, when you also need the user object:
        def create_student(user: User = Depends(RequireRole("admin"))):

    WHY A CLASS RATHER THAN A FUNCTION
    ----------------------------------
    Dependencies must be callables taking only what FastAPI can inject. To
    parameterise one by role, you need a callable that already knows the role
    -- either a closure or a class with __call__. The class version is easier
    to read and gives a useful repr in error messages.

    This is the "dependency factory" pattern: calling RequireRole("admin")
    returns a NEW dependency specialised to that role.
    """

    def __init__(self, *allowed_roles: str):
        if not allowed_roles:
            raise ValueError("RequireRole needs at least one role.")
        self.allowed_roles = {
            r.value if isinstance(r, RoleName) else str(r) for r in allowed_roles
        }

    async def __call__(self, user: CurrentUser) -> User:
        # Note the chaining: this dependency depends on get_current_user, so
        # authentication is guaranteed to have run first. You cannot
        # accidentally check a role on an unauthenticated request.
        if user.role.name not in self.allowed_roles:
            raise PermissionDeniedError(
                f"This action requires one of: {', '.join(sorted(self.allowed_roles))}. "
                f"Your role is '{user.role.name}'."
            )
        return user

    def __repr__(self) -> str:
        return f"RequireRole({', '.join(sorted(self.allowed_roles))})"


# Pre-built dependencies for the common cases. Defining them once means an
# endpoint cannot typo "admni" and silently create an unguarded route.
require_admin = RequireRole(RoleName.ADMIN)
require_faculty = RequireRole(RoleName.FACULTY)
require_student = RequireRole(RoleName.STUDENT)
require_staff = RequireRole(RoleName.ADMIN, RoleName.FACULTY)

AdminUser = Annotated[User, Depends(require_admin)]
FacultyUser = Annotated[User, Depends(require_faculty)]
StaffUser = Annotated[User, Depends(require_staff)]
