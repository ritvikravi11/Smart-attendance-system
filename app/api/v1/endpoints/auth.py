"""
Authentication endpoints.

NOTICE HOW THIN THESE FUNCTIONS ARE. Each one:
  1. receives validated input (Pydantic did that),
  2. confirms authorisation (a dependency did that),
  3. calls exactly one service method,
  4. shapes the response.

No business rules live here. That is the point of the layering -- and it is
what makes these endpoints reviewable at a glance.
"""

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, DbSession, get_client_ip
from app.core.config import settings
from app.schemas.auth import (
    ChangePasswordRequest,
    ErrorResponse,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Log in and obtain tokens",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        423: {"model": ErrorResponse, "description": "Account locked"},
    },
)
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    """
    Exchange email and password for an access/refresh token pair.

    The access token is short-lived and sent on every subsequent request as
    `Authorization: Bearer <token>`. The refresh token is long-lived and used
    only against `/auth/refresh`.

    Note the `responses=` argument above: it documents the failure cases in
    the generated OpenAPI spec, so the interactive docs at /docs show clients
    exactly what can go wrong. This is your API documentation requirement
    being satisfied automatically rather than written by hand.
    """
    service = AuthService(db)
    user, access_token, refresh_token = service.authenticate(
        email=payload.email,
        password=payload.password,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new token pair",
    responses={401: {"model": ErrorResponse}},
)
def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    """
    Obtain a fresh access token without re-entering credentials.

    Returns a NEW refresh token as well -- the old one is revoked. See
    AuthService.refresh_access_token for why rotation matters.

    This endpoint is deliberately unauthenticated: the refresh token IS the
    credential. Requiring a valid access token here would defeat the purpose,
    since the whole reason to call it is that the access token has expired.
    """
    service = AuthService(db)
    access_token, refresh_token = service.refresh_access_token(payload.refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Revoke the current refresh token",
)
def logout(
    payload: RefreshRequest, user: CurrentUser, db: DbSession
) -> MessageResponse:
    """
    End the session by revoking its refresh token.

    Requires BOTH a valid access token (proving who you are) and the refresh
    token to revoke. Without the access token requirement, anyone holding a
    stolen refresh token could... log you out. Minor, but there is no reason
    to allow it.
    """
    service = AuthService(db)
    service.logout(payload.refresh_token, user.id)
    return MessageResponse(message="Logged out successfully.")


@router.post(
    "/logout-all",
    response_model=MessageResponse,
    summary="Revoke every active session for the current user",
)
def logout_all(user: CurrentUser, db: DbSession) -> MessageResponse:
    """Log out of every device. Useful when a device is lost or stolen."""
    service = AuthService(db)
    count = service.logout_all_devices(user.id)
    return MessageResponse(message=f"Revoked {count} active session(s).")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current user's profile",
)
def read_current_user(user: CurrentUser) -> UserResponse:
    """
    Return the authenticated user.

    The frontend calls this on page load to restore session state -- it holds
    a token in storage but needs the user's name and role to render the right
    dashboard.

    No database call in the endpoint body: `get_current_user` already loaded
    and validated the user. Note that `UserResponse` strips `password_hash`
    even though the model carries it.
    """
    return UserResponse.model_validate(user)


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Change your own password",
    responses={401: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
)
def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, db: DbSession
) -> MessageResponse:
    """
    Change the current user's password and terminate all sessions.

    Every device must log in again, including this one -- deliberate, since a
    password change is often a response to compromise.
    """
    service = AuthService(db)
    service.change_password(user.id, payload.current_password, payload.new_password)
    return MessageResponse(
        message="Password changed. All sessions have been logged out."
    )
