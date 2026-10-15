"""
Identity and access models: roles, users, refresh tokens.

TABLES IN THIS MODULE AND WHY EACH EXISTS
-----------------------------------------
roles          -- Defines *what kinds of user exist*. A lookup table rather
                  than an enum because adding an "HOD" or "Parent" role should
                  be a data change (INSERT one row), not a code deployment.

users          -- The single authentication identity for every human in the
                  system, regardless of whether they are a student, faculty
                  member, or admin. This is the table-per-type inheritance
                  pattern: shared authentication lives here, role-specific
                  attributes live in `students` / `faculty` profile tables
                  linked 1:1.

refresh_tokens -- Persists issued refresh tokens so they can be *revoked*.
                  Without this table, logging out would be impossible: a JWT
                  is self-validating and stays valid until it expires, no
                  matter what the server wants.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    false as sa_false,
    true as sa_true,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, BigIntPK, TimestampMixin

if TYPE_CHECKING:
    from app.models.logs import AuditLog
    from app.models.people import Faculty, Student


class Role(Base):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    Authorisation needs a canonical, queryable list of roles. Storing the role
    as a free-text column on `users` would allow "faculty", "Faculty", and
    "facutly" to coexist, silently breaking permission checks.

    WHY A TABLE RATHER THAN A PostgreSQL ENUM
    -----------------------------------------
    The set of roles is the kind of thing that grows. A college may later want
    "HOD" (sees their whole department), "Parent" (sees one student), or
    "Registrar" (read-only across everything). With a table, that is an INSERT.
    With a native PostgreSQL ENUM, it is an ALTER TYPE migration and a
    redeployment.

    The cost is one JOIN on login. That JOIN happens once per session, is
    indexed, and returns three rows -- entirely negligible.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
        comment="Canonical role key: admin | faculty | student",
    )

    description: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Human-readable explanation shown in admin UI",
    )

    # ---- Relationships -------------------------------------------------
    # back_populates creates a two-way link: role.users and user.role both
    # work, and SQLAlchemy keeps them in sync in memory. The alternative,
    # `backref`, generates the reverse side implicitly -- which is less code
    # but makes the relationship invisible when reading the other model.
    # Explicit is better.
    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="role",
        # NO cascade delete: deleting a role must never delete its users.
        # If someone tries to delete a role that is in use, the foreign key
        # will (correctly) refuse.
    )

    def __repr__(self) -> str:
        return f"<Role id={self.id} name={self.name!r}>"


class User(Base, TimestampMixin):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    Every person who logs in needs credentials, a role, and an active flag.
    Those needs are identical whether the person is a student or a professor,
    so they belong in one table.

    WHY NOT SEPARATE `student_logins` AND `faculty_logins` TABLES
    ------------------------------------------------------------
    Because then every authentication feature must be built twice: password
    hashing, password reset, account lockout, refresh token rotation, last-login
    tracking, audit logging. Two implementations inevitably drift apart, and
    the security-critical one will be the one that drifts.

    With a single `users` table, `AuthService.login()` is written once and works
    for every role forever, including roles that do not exist yet.

    WHY NOT PUT roll_number / employee_code DIRECTLY HERE
    -----------------------------------------------------
    Because `roll_number` is meaningless for faculty and `employee_code` is
    meaningless for students. Putting both here produces a table where most
    columns are NULL for most rows -- unnormalised, and it gets worse with
    every role added. Role-specific attributes live in the profile tables
    (`students`, `faculty`) linked 1:1. This is table-per-type inheritance.

    SECURITY NOTE
    -------------
    `password_hash` stores a bcrypt hash, never a password. bcrypt is chosen
    deliberately over SHA-256: bcrypt is *intentionally slow* and salts each
    password individually. A fast hash like SHA-256 is the wrong tool here --
    its speed is exactly what makes brute-forcing a stolen hash dump feasible.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    email: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        unique=True,
        comment="Login identifier. Unique across ALL roles.",
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="bcrypt hash (cost factor 12). NEVER the plaintext password.",
    )

    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
        comment="Display name shown throughout the UI",
    )

    role_id: Mapped[int] = mapped_column(
        Integer,
        # ondelete="RESTRICT" is the default behaviour but we state it
        # explicitly: PostgreSQL will refuse to delete a role that still has
        # users attached. That refusal is correct -- silently orphaning users
        # would be far worse.
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        comment="FK to roles. Exactly one role per user.",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_true(),
        comment="FALSE blocks login without deleting history",
    )

    # ---- Brute-force protection (FR-1.7) --------------------------------
    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="Consecutive failed attempts; reset to 0 on success",
    )

    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="If set and in the future, login is refused",
    )

    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Supports 'dormant account' auditing",
    )

    # ---- Relationships -------------------------------------------------
    role: Mapped["Role"] = relationship("Role", back_populates="users")

    # uselist=False makes this a scalar (one object) rather than a list.
    # This is what expresses the 1:1 relationship on the Python side; the
    # UNIQUE constraint on students.user_id is what enforces it in the database.
    # BOTH are needed: the ORM hint alone would not stop a raw SQL insert.
    student_profile: Mapped[Optional["Student"]] = relationship(
        "Student",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    faculty_profile: Mapped[Optional["Faculty"]] = relationship(
        "Faculty",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken",
        back_populates="user",
        # Deleting a user SHOULD delete their tokens -- an orphaned token is
        # a security hole, not history worth preserving.
        cascade="all, delete-orphan",
    )

    audit_entries: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="user",
        # NO cascade: audit logs must survive user deletion. That is the
        # entire point of an audit log. The FK is nullable so the log row
        # remains valid with user_id set to NULL.
    )

    __table_args__ = (
        # Partial index: only active users are ever looked up by role for
        # things like "list all faculty". Indexing the deactivated rows too
        # would waste space and slow writes for no benefit.
        Index(
            "idx_users_role_active",
            "role_id",
            postgresql_where=text("is_active = true"),
        ),
        # Case-insensitive uniqueness on email. Without this, "Ravi@x.com"
        # and "ravi@x.com" are two different accounts -- a genuine source of
        # duplicate-account bugs and a minor account-takeover vector.
        Index(
            "idx_users_email_lower",
            text("lower(email)"),
            unique=True,
        ),
        {"comment": "Authentication identity for every human in the system"},
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role_id={self.role_id}>"


class RefreshToken(Base):
    """
    WHY THIS TABLE EXISTS
    ---------------------
    This is the table beginners omit, and its absence creates a real security
    hole. Here is the reasoning.

    A JWT is *self-validating*: the server verifies it by checking the
    signature, without consulting any database. That is what makes JWTs fast
    and horizontally scalable -- any server instance can validate any token.

    But it is also what makes them impossible to revoke. If a user clicks
    "log out", or an admin disables a compromised account, the already-issued
    token keeps working until it expires. There is nothing to delete.

    The standard solution is a two-token scheme:
      - ACCESS token: short-lived (30 min), never stored server-side. If
        stolen, the damage window is 30 minutes.
      - REFRESH token: long-lived (7 days), stored HERE. Used only to obtain
        new access tokens.

    Because refresh tokens are stored, they can be revoked. Logout sets
    `revoked_at`. Compromised account? Revoke every token for that user. The
    access token still works for up to 30 more minutes, but the attacker
    cannot renew it, so the session dies.

    WHY WE STORE A HASH, NOT THE TOKEN
    ----------------------------------
    Exactly the same reasoning as passwords. If someone dumps this table, raw
    tokens would be immediately usable session credentials for every logged-in
    user. Storing SHA-256 hashes means a stolen dump is worthless: verification
    hashes the incoming token and compares.

    (SHA-256 is correct *here*, unlike for passwords, because a refresh token
    is 256 bits of cryptographic randomness. It has no low-entropy structure to
    brute-force, so the deliberate slowness of bcrypt buys nothing.)
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Owner of this token",
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        comment="SHA-256 of the token. The raw token is never stored.",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Hard expiry, independent of revocation",
    )

    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="NULL = valid. Set on logout or forced revocation.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user_agent: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Device fingerprint, so users can see active sessions",
    )

    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),  # 45 chars accommodates a full IPv6 address
        nullable=True,
        comment="Issuing IP, for anomaly detection",
    )

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")

    __table_args__ = (
        # The hot query is "find this user's valid tokens". A partial index on
        # non-revoked rows keeps the index small -- revoked tokens accumulate
        # forever but are never searched.
        Index(
            "idx_refresh_user_valid",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # Supports the cleanup job that purges expired rows.
        Index("idx_refresh_expires", "expires_at"),
        {"comment": "Revocable refresh tokens; enables real logout"},
    )

    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} user_id={self.user_id}>"
