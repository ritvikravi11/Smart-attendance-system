"""
Application exception hierarchy.

WHY CUSTOM EXCEPTIONS RATHER THAN RAISING HTTPException IN SERVICES
-------------------------------------------------------------------
It is tempting to write `raise HTTPException(404)` directly inside
`StudentService`. Do not. Three reasons:

1. It couples business logic to HTTP. Your service layer is supposed to be
   framework-agnostic (NFR-5.1) -- callable from a CLI script, a background
   job, or a test with no web server running. `HTTPException` drags FastAPI
   into all of them.

2. It scatters status-code decisions. "Is a duplicate roll number a 400 or a
   409?" gets answered differently in five places by five past versions of
   you.

3. It makes services hard to test. Asserting `pytest.raises(NotFoundError)`
   reads clearly; asserting on an HTTPException's status_code attribute does
   not.

The pattern instead: services raise DOMAIN exceptions ("this student does not
exist"). A single exception handler in main.py translates each domain
exception into the right HTTP response. Business logic stays clean; status
codes are decided in exactly one place.
"""


class AppException(Exception):
    """
    Base for every application-level exception.

    Carrying `error_code` alongside the message gives the frontend something
    stable to branch on. A human-readable message may be reworded or
    translated; `DUPLICATE_ROLL_NUMBER` will not. Frontend code that switches
    on message text breaks the first time you fix a typo.
    """

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        field: str | None = None,
    ):
        self.message = message
        self.field = field  # which input field caused this, for form UIs
        if error_code:
            self.error_code = error_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# 400 / 422 -- the caller sent something wrong
# ---------------------------------------------------------------------------


class ValidationError(AppException):
    status_code = 400
    error_code = "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 401 -- we do not know who you are
# ---------------------------------------------------------------------------


class AuthenticationError(AppException):
    """
    Credentials missing, malformed, expired, or wrong.

    NOTE ON THE DELIBERATELY VAGUE DEFAULT MESSAGE
    ----------------------------------------------
    "Incorrect email or password" -- never "no account with that email" or
    "wrong password for this account".

    A specific message turns your login form into an account enumeration
    oracle: an attacker submits a list of email addresses and learns which
    ones are registered, purely from the difference in error text. That list
    then feeds credential-stuffing or phishing.

    The vagueness is mildly annoying for legitimate users and genuinely
    valuable against attackers. Standard practice.
    """

    status_code = 401
    error_code = "AUTHENTICATION_FAILED"


class AccountLockedError(AuthenticationError):
    """
    Too many failed attempts.

    This one IS specific, and the asymmetry is intentional: it only fires
    after correct identification of an existing account under attack, and the
    user genuinely needs to know why waiting is required rather than assuming
    the system is broken.
    """

    status_code = 423  # HTTP 423 Locked
    error_code = "ACCOUNT_LOCKED"


class AccountInactiveError(AuthenticationError):
    status_code = 403
    error_code = "ACCOUNT_INACTIVE"


# ---------------------------------------------------------------------------
# 403 -- we know who you are, and you may not do this
# ---------------------------------------------------------------------------


class PermissionDeniedError(AppException):
    """
    Authenticated but not authorised.

    The 401/403 distinction matters and is routinely muddled:
      401 = "who are you?"        -> the client should log in
      403 = "I know who you are,   -> logging in again will not help
             and the answer is no"

    Returning 401 for an authorisation failure makes well-behaved clients
    retry the login flow pointlessly.
    """

    status_code = 403
    error_code = "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# 404 / 409 -- resource state
# ---------------------------------------------------------------------------


class NotFoundError(AppException):
    status_code = 404
    error_code = "NOT_FOUND"


class ConflictError(AppException):
    """
    The request is valid but conflicts with current state.

    Duplicate roll number, duplicate enrolment, marking an already-marked
    student. 409 rather than 400: the request itself is well-formed, so the
    client should not "fix" it and retry -- the world is simply already in a
    state that excludes it.
    """

    status_code = 409
    error_code = "CONFLICT"


class ServiceUnavailableError(AppException):
    """A configured external dependency cannot currently serve the request."""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"
