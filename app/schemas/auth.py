"""
Pydantic schemas for authentication.

WHY SCHEMAS ARE SEPARATE FROM MODELS
------------------------------------
This is the distinction beginners most often collapse, so it is worth being
blunt about it.

  MODELS  (app/models/)  describe your DATABASE.  What columns exist.
  SCHEMAS (app/schemas/) describe your API.       What JSON is accepted/returned.

They are similar but must never be the same object. The `User` model has a
`password_hash` column. If you returned the model directly from an endpoint,
FastAPI would serialise every attribute -- and you would ship bcrypt hashes to
the browser. Schemas make the exposed surface an explicit, reviewable decision
rather than an accident of table design.

The rule: one schema per direction. `*Create` for input, `*Response` for
output. They differ deliberately -- input has a password, output never does.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# REQUESTS (input)
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """
    Credentials submitted to /auth/login.

    EmailStr does real format validation (via email-validator), not a naive
    regex. Invalid input is rejected by FastAPI with a 422 before your endpoint
    function runs -- so the service layer can assume a syntactically valid
    address and never has to re-check.
    """

    email: EmailStr = Field(
        ...,
        description="Registered email address",
        examples=["das@college.edu"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=72,  # bcrypt's byte ceiling; see core/security.py
        description="Plaintext password. Sent over HTTPS, never stored.",
    )


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token issued at login")


class ChangePasswordRequest(BaseModel):
    """
    WHY THE CURRENT PASSWORD IS REQUIRED
    ------------------------------------
    The user is already authenticated -- so why ask again?

    Because an unattended logged-in session is a real threat. Someone walks up
    to a faculty member's open laptop and changes the password: they now own
    the account permanently, and the legitimate owner is locked out. Requiring
    the current password means possessing the session is not sufficient.

    This is standard practice on every serious platform, for exactly this
    reason.
    """

    current_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """
        Minimum viable strength check.

        DELIBERATELY MODEST. Research (and NIST SP 800-63B) has moved away
        from baroque composition rules -- forcing a symbol and a digit mostly
        produces "Password1!" and a sticky note. Length is the dominant factor.

        We require 8+ characters plus some variety, and stop there. A
        production system should additionally check against a breached-password
        list (Have I Been Pwned's k-anonymity API is free and does not transmit
        the password); that is noted as a Phase 2 item.
        """
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password exceeds bcrypt's 72-byte limit")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


# ---------------------------------------------------------------------------
# RESPONSES (output)
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    """
    Issued on successful login or refresh.

    `token_type: "bearer"` follows RFC 6750. The client sends the access token
    back as:  Authorization: Bearer <token>

    `expires_in` is seconds, not a timestamp -- so the client does not need its
    clock to agree with the server's. A well-built frontend uses it to refresh
    proactively (say at 80% of lifetime) rather than waiting for a 401 and
    retrying.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class UserResponse(BaseModel):
    """
    Safe representation of a user.

    Compare this field list against the `User` model. Absent here, present
    there: password_hash, failed_login_count, locked_until.

    That omission is the entire point of having a separate schema. It is not
    an oversight -- it is the security boundary, made explicit and reviewable.

    `from_attributes=True` lets Pydantic read from a SQLAlchemy object rather
    than requiring a dict, so an endpoint can `return user` and Pydantic
    extracts exactly these fields and no others.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: RoleResponse
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class MessageResponse(BaseModel):
    """Generic acknowledgement for operations with nothing else to return."""

    message: str


class ErrorResponse(BaseModel):
    """
    The single error shape every failure uses.

    One consistent envelope means the frontend needs exactly one error handler
    rather than a per-endpoint guess at what came back.
    """

    detail: str = Field(..., description="Human-readable message")
    error_code: str = Field(..., description="Stable machine-readable code")
    field: str | None = Field(None, description="Offending input field, if any")
