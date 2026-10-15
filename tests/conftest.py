"""
Shared pytest fixtures.

WHY conftest.py
---------------
pytest auto-discovers this file and makes every fixture in it available to
every test module below it, with no imports. That is what stops each test file
re-creating its own database, its own users, and its own login helper --
duplication that inevitably drifts until two files disagree about what "a
student" means.

FIXTURE SCOPE, which is the main design decision here
-----------------------------------------------------
    session : built once for the whole run    -- the engine and schema
    function: rebuilt for every single test   -- the data

Tests must not depend on each other. If test A creates a student and test B
counts students, B passes or fails depending on alphabetical ordering -- a
class of bug that is miserable to diagnose because the test that FAILS is
rarely the one that is WRONG.

So each test gets a clean database. Creating the schema is the slow part and
it is done once; wiping the rows is fast and is done constantly.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault("LOAD_ML_ON_STARTUP", "false")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, Integer as SAInteger, create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy.schema import DefaultClause  # noqa: E402
from sqlalchemy.sql import text as sql_text  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.academic import (  # noqa: E402
    Department,
    Enrollment,
    Subject,
    SubjectOffering,
)
from app.models.attendance import AttendanceRecord, AttendanceSession  # noqa: E402
from app.models.enums import SessionStatus  # noqa: E402
from app.models.people import Faculty, Student  # noqa: E402
from app.models.user import Role, User  # noqa: E402


# ---------------------------------------------------------------------------
# DIALECT BRIDGING
#
# The models target PostgreSQL. Three things need translating for SQLite, and
# NONE of them weaken the production schema -- they are test-harness concerns
# only, applied to the in-memory metadata:
#   1. now()               -> CURRENT_TIMESTAMP
#   2. BIGINT primary keys -> INTEGER (SQLite auto-increments only INTEGER PKs)
#   3. partial indexes     -> dropped (SQLite cannot parse the WHERE clause)
#
# The partial indexes and PostgreSQL types are verified separately by
# scripts/dump_ddl.py, which compiles the real PostgreSQL DDL.
# ---------------------------------------------------------------------------
def _adapt_metadata_for_sqlite():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            default = column.server_default
            if isinstance(default, DefaultClause) and "now()" in str(default.arg).lower():
                column.server_default = DefaultClause(sql_text("CURRENT_TIMESTAMP"))
            if column.primary_key and isinstance(column.type, BigInteger):
                column.type = SAInteger()
    for table in Base.metadata.sorted_tables:
        table.indexes = {
            i
            for i in table.indexes
            if i.dialect_options.get("postgresql", {}).get("where") is None
        }


_adapt_metadata_for_sqlite()


@pytest.fixture(scope="session")
def engine():
    """One engine and schema for the whole run. Schema creation is the slow part."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        # StaticPool: ':memory:' gives each CONNECTION its own database. Without
        # this, the fixture seeds one database and the app queries a different,
        # empty one -- producing "no such table" with no obvious cause.
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _):
        # SQLite ignores foreign keys unless told not to. Without this, every
        # FK test would silently pass.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="function")
def db(engine):
    """
    A clean database per test.

    Rows are deleted in reverse dependency order so foreign keys never block
    the wipe. Faster than recreating the schema, and it keeps tests independent.
    """
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.rollback()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


@pytest.fixture(scope="function")
def client(engine, db):
    """
    TestClient with the database dependency overridden.

    `raise_server_exceptions=False` makes the client return a 500 response
    rather than re-raising. That matters: we want to ASSERT that unexpected
    errors produce a clean 500 with no stack trace in the body, which is
    impossible if the exception propagates into the test instead.
    """
    from app.main import app

    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DOMAIN FIXTURES
# ---------------------------------------------------------------------------


@pytest.fixture
def roles(db):
    made = {}
    for name in ("admin", "faculty", "student"):
        role = Role(name=name, description=name)
        db.add(role)
        made[name] = role
    db.commit()
    return made


@pytest.fixture
def department(db):
    dept = Department(code="CSE", name="Computer Science and Engineering")
    db.add(dept)
    db.commit()
    return dept


@pytest.fixture
def make_user(db, roles):
    """Factory fixture. Returns a function so a test can make as many as it needs."""

    def _make(email, role, name=None, password="Passw0rd!", active=True):
        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name=name or email.split("@")[0].title(),
            role_id=roles[role].id,
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def admin_user(make_user):
    return make_user("admin@college.edu", "admin", "System Admin")


@pytest.fixture
def faculty(db, make_user, department):
    user = make_user("das@college.edu", "faculty", "Prof Das")
    profile = Faculty(user_id=user.id, employee_code="EMP001", department_id=department.id)
    db.add(profile)
    db.commit()
    return profile


@pytest.fixture
def other_faculty(db, make_user, department):
    """A second faculty member. Every ownership test needs someone to be denied."""
    user = make_user("rao@college.edu", "faculty", "Prof Rao")
    profile = Faculty(user_id=user.id, employee_code="EMP002", department_id=department.id)
    db.add(profile)
    db.commit()
    return profile


@pytest.fixture
def subject(db, department):
    subj = Subject(
        code="CS301", name="Data Structures", department_id=department.id, semester=3
    )
    db.add(subj)
    db.commit()
    return subj


@pytest.fixture
def offering(db, subject, faculty):
    off = SubjectOffering(
        subject_id=subject.id,
        faculty_id=faculty.id,
        section="A",
        academic_year="2026-2027",
    )
    db.add(off)
    db.commit()
    return off


@pytest.fixture
def make_student(db, make_user, department):
    def _make(roll, name=None, enrolled_in=None, enrolled_at=None):
        user = make_user(f"{roll.lower()}@college.edu", "student", name or f"Student {roll}")
        student = Student(
            user_id=user.id,
            roll_number=roll,
            department_id=department.id,
            semester=3,
        )
        db.add(student)
        db.commit()
        if enrolled_in is not None:
            db.add(
                Enrollment(
                    student_id=student.id,
                    offering_id=enrolled_in.id,
                    enrolled_at=enrolled_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
            db.commit()
        return student

    return _make


@pytest.fixture
def students(make_student, offering):
    """Five students enrolled from the start of the year."""
    return [make_student(f"R{i:03d}", enrolled_in=offering) for i in range(1, 6)]


@pytest.fixture
def make_session_row(db, offering, faculty):
    """
    Create a session with records already in place.

    Bypasses the service layer deliberately: these are ARRANGE steps, and
    driving them through the API would make every test depend on the session
    endpoints working. A unit test of percentage maths should not fail because
    a route changed.
    """

    def _make(day, marks, status="closed", offering_obj=None):
        off = offering_obj or offering
        session = AttendanceSession(
            offering_id=off.id,
            session_date=day,
            status=SessionStatus.OPEN.value,
            created_by=faculty.user_id,
        )
        db.add(session)
        db.commit()
        for student_id, status_value in marks.items():
            db.add(
                AttendanceRecord(
                    session_id=session.id,
                    student_id=student_id,
                    status=status_value,
                    method="manual",
                    marked_by=faculty.user_id,
                )
            )
        session.status = status
        if status != SessionStatus.OPEN.value:
            session.ended_at = datetime.now(timezone.utc)
        db.commit()
        return session

    return _make


@pytest.fixture
def login(client):
    """Return auth headers for a set of credentials."""

    def _login(email, password="Passw0rd!"):
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, f"login failed: {response.text}"
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
def faculty_headers(login, faculty):
    return login("das@college.edu")


@pytest.fixture
def admin_headers(login, admin_user):
    return login("admin@college.edu")


@pytest.fixture
def student_headers(login, students):
    return login("r001@college.edu")


V1 = "/api/v1"
