"""
Cryptographic primitives: password hashing and JWT token handling.

WHY THIS FILE EXISTS
--------------------
Every security-sensitive operation in the system funnels through here. Keeping
them in one small, carefully-reviewed module means there is exactly one place
to audit -- rather than hashing scattered across three services, each with a
slightly different (and slightly wrong) implementation.

This module deliberately knows NOTHING about the database, FastAPI, or your
business rules. It takes strings and returns strings. That makes it trivially
unit-testable and means it cannot accidentally leak a database session into a
token.

TWO DEVIATIONS FROM THE ORIGINAL DESIGN DOCUMENT -- both deliberate
-------------------------------------------------------------------
1. PyJWT instead of python-jose.
   The design doc specified python-jose. Its last release was in 2021 and it
   is effectively unmaintained. PyJWT is actively maintained, has a smaller
   API surface, and is the de-facto standard. Using an unmaintained library
   for the component that guards every endpoint is a bad trade. Same JWTs,
   same algorithm, better maintenance.

2. bcrypt directly instead of passlib[bcrypt].
   passlib 1.7.4 reads `bcrypt.__about__.__version__` at import time. bcrypt
   4.1+ removed that attribute, so the combination raises an AttributeError
   or emits noisy warnings. passlib's last release was 2020. Calling bcrypt
   directly is about fifteen lines and removes a dead dependency from the
   security path.

Both changes are recorded here rather than silently applied, because a design
document that quietly diverges from the code is worse than no document.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
import secrets

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import settings

# ---------------------------------------------------------------------------
# PASSWORD HASHING
# ---------------------------------------------------------------------------

# bcrypt truncates input at 72 BYTES and silently ignores everything beyond.
# That means "long_password_A" and "long_password_B" can hash identically if
# they share the first 72 bytes -- a real (if obscure) vulnerability. We reject
# over-length passwords explicitly rather than silently truncating them.
# Note BYTES, not characters: a password of emoji or Devanagari hits this limit
# far sooner than 72 keystrokes.
BCRYPT_MAX_BYTES = 72


def hash_password(plain_password: str) -> str:
    """
    Hash a password for storage.

    WHY BCRYPT AND NOT SHA-256
    --------------------------
    This is the single most misunderstood point in application security, so it
    is worth being precise.

    SHA-256 is a *fast* hash -- that is its design goal. Modern hardware
    computes billions of SHA-256 hashes per second. If an attacker steals your
    `users` table, they can test the entire English dictionary against every
    row in seconds.

    bcrypt is *deliberately slow*. The `rounds` parameter is an exponent: 12
    rounds means 2^12 = 4096 internal iterations, taking roughly 250ms. That
    delay is invisible to a legitimate user logging in once, but it reduces an
    attacker from billions of guesses per second to about four.

    bcrypt also salts automatically. Every call generates a fresh random salt
    and embeds it in the output, so two users with the identical password get
    completely different hashes. This defeats rainbow tables and means an
    attacker cannot tell that two accounts share a password.

    The stored value looks like:
        $2b$12$Xk9pQ...........salt+hash...........
         |  |  \\-- 22-char salt, then 31-char hash
         |  \\----- cost factor (12)
         \\-------- algorithm identifier
    """
    password_bytes = plain_password.encode("utf-8")

    if len(password_bytes) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password exceeds bcrypt's {BCRYPT_MAX_BYTES}-byte limit. "
            "Anything beyond would be silently ignored."
        )

    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check a password against its stored hash.

    `bcrypt.checkpw` extracts the salt and cost factor from the stored hash,
    re-hashes the candidate with those parameters, and compares the results in
    CONSTANT TIME.

    Constant-time comparison matters. A naive `==` on strings short-circuits at
    the first differing byte, so a wrong guess that shares a longer prefix
    takes measurably longer to reject. Timing that difference across many
    requests leaks the hash byte by byte. This is a genuine attack class, and
    it is why you never hand-roll hash comparison.

    Returns False rather than raising on malformed input: a corrupt hash in the
    database should deny access, not crash the endpoint with a 500 that reveals
    internal state.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT TOKENS
# ---------------------------------------------------------------------------

TokenType = Literal["access", "refresh"]


def _create_token(
    subject: str | int,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Build and sign a JWT.

    ANATOMY OF THE CLAIMS
    ---------------------
    sub  -- "subject": who this token is about. We store the user ID.
            MUST be a string. The JWT spec says so, and PyJWT enforces it on
            decode. Passing an int here produces tokens that fail validation
            with a confusing error.
    exp  -- expiry timestamp. PyJWT checks this automatically on decode and
            raises ExpiredSignatureError.
    iat  -- issued-at. Useful for "invalidate all tokens issued before X",
            which is how you force a global logout after a breach.
    type -- OUR OWN claim, and the important one. See below.

    WHY THE `type` CLAIM IS SECURITY-CRITICAL
    -----------------------------------------
    Access and refresh tokens are signed with the same secret, so structurally
    they are interchangeable. Without a type claim, an attacker who obtains a
    refresh token could present it as an access token and get 7 days of API
    access instead of 30 minutes.

    Every decode call therefore asserts the expected type. This is a small
    check that closes a real hole.
    """
    now = datetime.now(timezone.utc)

    claims: dict[str, Any] = {
        "sub": str(subject),  # MUST be a string per RFC 7519
        "exp": now + expires_delta,
        "iat": now,
        "type": token_type,
        # jti = "JWT ID", a unique identifier for this specific token.
        #
        # WHY THIS IS NOT OPTIONAL -- found by the test suite:
        # JWT timestamps have SECOND resolution. Two tokens minted for the
        # same user within the same second have byte-identical payloads, so
        # they produce byte-identical tokens -- and therefore identical
        # SHA-256 hashes, which violates the UNIQUE constraint on
        # refresh_tokens.token_hash. Login twice quickly and the second
        # request 500s.
        #
        # A random jti guarantees every token is distinct. It also gives us
        # the identifier a Redis denylist would key on if we later add
        # instant access-token revocation.
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        claims.update(extra_claims)

    return jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(
    user_id: int,
    role: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Short-lived token presented on every API request.

    WHY THE ROLE IS EMBEDDED IN THE TOKEN
    -------------------------------------
    Authorisation checks happen on every single request. Embedding the role
    means `require_role("faculty")` reads it straight from the decoded token
    with no database query. Across a 2 fps recognition stream, that saves
    thousands of pointless SELECTs.

    THE TRADE-OFF, stated honestly: the role is a snapshot from login time. If
    an admin demotes a user, that user keeps their old role until their access
    token expires -- up to 30 minutes. This is the standard, accepted cost of
    stateless auth, and it is why access tokens are short-lived. If your threat
    model cannot tolerate a 30-minute window, the fix is to look the role up
    per request; ours can.

    WHAT NEVER GOES IN A TOKEN
    --------------------------
    A JWT is signed, NOT encrypted. Anyone holding it can read every claim --
    paste one into jwt.io and see. Signing guarantees the contents were not
    *altered*; it does nothing to hide them. So: no passwords, no face
    embeddings, no personal data beyond an ID and a role.
    """
    return _create_token(
        subject=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims={"role": role, **(extra_claims or {})},
    )


def create_refresh_token(user_id: int) -> str:
    """
    Long-lived token used only to obtain new access tokens.

    Deliberately carries NO role claim. Its single purpose is proving identity
    at the refresh endpoint, where the role is re-read fresh from the database.
    That re-read is what stops a demoted user from renewing elevated access
    indefinitely -- the 30-minute window closes rather than repeating forever.
    """
    return _create_token(
        subject=user_id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """
    Verify and decode a JWT, asserting its type.

    Raises InvalidTokenError on: bad signature, expiry, malformed structure,
    or type mismatch. The caller converts that into a 401.

    WHY algorithms= IS PINNED TO A LIST
    -----------------------------------
    This single argument prevents the classic JWT algorithm-confusion attack.
    Historically, libraries would trust the `alg` header inside the token
    itself. An attacker could set `alg: none`, strip the signature, and the
    library would happily accept an unsigned, attacker-authored token.

    Pinning the accepted algorithms means we decide, not the token. Never pass
    a token's own header value here.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],  # NEVER read this from the token
        )
    except InvalidTokenError as exc:
        raise InvalidTokenError(f"Token validation failed: {exc}") from exc

    token_type = payload.get("type")
    if token_type != expected_type:
        raise InvalidTokenError(
            f"Expected a {expected_type!r} token but received {token_type!r}."
        )

    if "sub" not in payload:
        raise InvalidTokenError("Token is missing the required 'sub' claim.")

    return payload


# ---------------------------------------------------------------------------
# REFRESH TOKEN STORAGE
# ---------------------------------------------------------------------------


def hash_refresh_token(token: str) -> str:
    """
    Hash a refresh token before storing it in the database.

    WHY HASH AT ALL, given the token is already signed?
    ---------------------------------------------------
    Because a signed token is a *usable credential*. If someone dumps the
    `refresh_tokens` table, plaintext rows are immediately-usable sessions for
    every logged-in user in the institution. Hashes are worthless to them.

    Same reasoning as passwords -- but note the different algorithm choice.

    WHY SHA-256 HERE AND BCRYPT FOR PASSWORDS
    -----------------------------------------
    bcrypt's slowness exists to defeat brute force against LOW-ENTROPY inputs.
    Humans pick "password123", so guessing is viable and must be made
    expensive.

    A refresh token is a signed JWT containing high-entropy machine-generated
    material. There is no dictionary to try. Brute force is not the threat, so
    bcrypt's 250ms buys nothing -- and would be paid on every token refresh.
    SHA-256 is fast, sufficient, and correct here.

    Matching the algorithm to the threat, rather than reflexively reaching for
    the "most secure" option, is the actual skill.
    """
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
