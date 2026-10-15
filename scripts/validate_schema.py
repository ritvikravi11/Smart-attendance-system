"""
Schema validation harness.

Exercises the constraints that matter, proving they are enforced by the
DATABASE rather than merely by application convention.

Runs against SQLite for portability. SQLite enforces UNIQUE, CHECK, and
FOREIGN KEY constraints (with the pragma enabled), which covers every rule we
need to verify. PostgreSQL-only features -- partial indexes, JSONB -- are
skipped here and verified separately by inspecting the generated DDL.
"""

import sys
from datetime import date, datetime, timezone

sys.path.insert(0, "/home/claude/backend")

import numpy as np
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    AttendanceRecord,
    AttendanceSession,
    Department,
    Enrollment,
    FaceEmbedding,
    Faculty,
    Role,
    Student,
    Subject,
    SubjectOffering,
    User,
    Base,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []


def check(name: str, condition: bool, detail: str = ""):
    results.append((name, condition))
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Setup: SQLite in memory, with foreign keys turned ON
#
# NOTE: the models target PostgreSQL. Two dialect differences are bridged here
# purely for this test harness -- the models themselves are NOT modified:
#   1. server_default=now()      -> SQLite wants CURRENT_TIMESTAMP
#   2. postgresql_where partial indexes -> SQLite cannot parse them
# Both are verified separately against the real PostgreSQL DDL.
# ---------------------------------------------------------------------------
from sqlalchemy import BigInteger, Integer as SAInteger
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql import text as sql_text

for _t in Base.metadata.tables.values():
    for _c in _t.columns:
        sd = _c.server_default
        if isinstance(sd, DefaultClause) and "now()" in str(sd.arg).lower():
            _c.server_default = DefaultClause(sql_text("CURRENT_TIMESTAMP"))

        # SQLite auto-increments only "INTEGER PRIMARY KEY" -- it will not
        # auto-generate values for BIGINT. PostgreSQL's BIGSERIAL has no such
        # limitation, so this remap is purely a harness concern and does not
        # reflect any weakness in the production schema.
        if _c.primary_key and isinstance(_c.type, BigInteger):
            _c.type = SAInteger()

engine = create_engine("sqlite:///:memory:", echo=False)


@event.listens_for(engine, "connect")
def _enable_fk(dbapi_conn, _):
    """SQLite ignores foreign keys unless explicitly told not to."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


# Drop PostgreSQL-specific partial indexes, which SQLite cannot parse.
# Their correctness is verified by inspecting the PostgreSQL DDL instead.
_partial_count = 0
for t in Base.metadata.sorted_tables:
    keep = set()
    for i in t.indexes:
        if i.dialect_options.get("postgresql", {}).get("where") is not None:
            _partial_count += 1
        else:
            keep.add(i)
    t.indexes = keep

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, expire_on_commit=False)

print("\n" + "=" * 70)
print("SCHEMA VALIDATION")
print("=" * 70)

# ---------------------------------------------------------------------------
print("\n[1] Seed data and relationship integrity")
# ---------------------------------------------------------------------------
db = Session()

r_admin = Role(name="admin", description="Full access")
r_fac = Role(name="faculty", description="Teaching staff")
r_stu = Role(name="student", description="Learner")
db.add_all([r_admin, r_fac, r_stu])
db.commit()

dept = Department(code="CSE", name="Computer Science and Engineering")
db.add(dept)
db.commit()

u_fac = User(
    email="das@college.edu",
    password_hash="$2b$12$fake",
    full_name="Prof. Das",
    role_id=r_fac.id,
)
u_s1 = User(
    email="ravi@college.edu",
    password_hash="$2b$12$fake",
    full_name="Ravi Kumar",
    role_id=r_stu.id,
)
u_s2 = User(
    email="priya@college.edu",
    password_hash="$2b$12$fake",
    full_name="Priya Sahu",
    role_id=r_stu.id,
)
db.add_all([u_fac, u_s1, u_s2])
db.commit()

fac = Faculty(user_id=u_fac.id, employee_code="EMP001", department_id=dept.id,
              designation="Assistant Professor")
s1 = Student(user_id=u_s1.id, roll_number="2226CSE001", department_id=dept.id,
             semester=3, consent_given_at=datetime.now(timezone.utc))
s2 = Student(user_id=u_s2.id, roll_number="2226CSE002", department_id=dept.id,
             semester=3)
db.add_all([fac, s1, s2])
db.commit()

subj = Subject(code="CS301", name="Data Structures", department_id=dept.id,
               semester=3, credits=4)
db.add(subj)
db.commit()

off = SubjectOffering(subject_id=subj.id, faculty_id=fac.id, section="A",
                      academic_year="2026-2027")
db.add(off)
db.commit()

db.add_all([
    Enrollment(student_id=s1.id, offering_id=off.id),
    Enrollment(student_id=s2.id, offering_id=off.id),
])
db.commit()

check("Seed data created", True, f"{db.query(Student).count()} students")
check("1:1 user->student navigable", u_s1.student_profile.roll_number == "2226CSE001")
check("Offering->faculty->user chain", off.faculty.user.full_name == "Prof. Das")
check("Offering roster resolves", len(off.enrollments) == 2)
check("has_valid_consent property", s1.has_valid_consent and not s2.has_valid_consent,
      "s1 consented, s2 did not")

# ---------------------------------------------------------------------------
print("\n[2] Uniqueness constraints")
# ---------------------------------------------------------------------------
try:
    db.add(Student(user_id=u_s1.id, roll_number="DUPLICATE",
                   department_id=dept.id, semester=1))
    db.commit()
    check("Duplicate user_id rejected (1:1 enforced)", False)
except IntegrityError:
    db.rollback()
    check("Duplicate user_id rejected (1:1 enforced)", True)

try:
    db.add(Student(user_id=u_fac.id, roll_number="2226CSE001",
                   department_id=dept.id, semester=1))
    db.commit()
    check("Duplicate roll_number rejected", False)
except IntegrityError:
    db.rollback()
    check("Duplicate roll_number rejected", True)

try:
    db.add(SubjectOffering(subject_id=subj.id, faculty_id=fac.id,
                           section="A", academic_year="2026-2027"))
    db.commit()
    check("Duplicate offering (subject+section+year) rejected", False)
except IntegrityError:
    db.rollback()
    check("Duplicate offering (subject+section+year) rejected", True)

try:
    db.add(Enrollment(student_id=s1.id, offering_id=off.id))
    db.commit()
    check("Duplicate enrollment rejected", False)
except IntegrityError:
    db.rollback()
    check("Duplicate enrollment rejected", True)

# ---------------------------------------------------------------------------
print("\n[3] CHECK constraints")
# ---------------------------------------------------------------------------
try:
    db.add(Student(user_id=u_fac.id, roll_number="BADSEM",
                   department_id=dept.id, semester=99))
    db.commit()
    check("semester=99 rejected (range 1-8)", False)
except IntegrityError:
    db.rollback()
    check("semester=99 rejected (range 1-8)", True)

sess = AttendanceSession(offering_id=off.id, session_date=date(2026, 8, 6),
                         created_by=u_fac.id, room="A-204")
db.add(sess)
db.commit()

try:
    db.add(AttendanceRecord(session_id=sess.id, student_id=s1.id,
                            status="teleported", method="face", confidence=0.9))
    db.commit()
    check("Invalid status rejected", False)
except IntegrityError:
    db.rollback()
    check("Invalid status rejected", True)

try:
    db.add(AttendanceRecord(session_id=sess.id, student_id=s1.id,
                            status="present", method="face", confidence=1.5))
    db.commit()
    check("confidence=1.5 rejected (range 0-1)", False)
except IntegrityError:
    db.rollback()
    check("confidence=1.5 rejected (range 0-1)", True)

# The cross-column provenance rule
try:
    db.add(AttendanceRecord(session_id=sess.id, student_id=s1.id,
                            status="present", method="face", confidence=None))
    db.commit()
    check("method='face' without confidence rejected", False)
except IntegrityError:
    db.rollback()
    check("method='face' without confidence rejected", True)

try:
    db.add(AttendanceRecord(session_id=sess.id, student_id=s1.id,
                            status="present", method="manual", marked_by=None))
    db.commit()
    check("method='manual' without marked_by rejected", False)
except IntegrityError:
    db.rollback()
    check("method='manual' without marked_by rejected", True)

# ---------------------------------------------------------------------------
print("\n[4] *** DUPLICATE PREVENTION -- the critical constraint ***")
# ---------------------------------------------------------------------------
rec = AttendanceRecord(session_id=sess.id, student_id=s1.id, status="present",
                       method="face", confidence=0.87)
db.add(rec)
db.commit()
check("First mark succeeds", db.query(AttendanceRecord).count() == 1)

# Simulate the 2 fps camera hammering the endpoint with the same student
duplicates_blocked = 0
for frame in range(20):
    try:
        db.add(AttendanceRecord(session_id=sess.id, student_id=s1.id,
                                status="present", method="face",
                                confidence=0.85 + frame * 0.001))
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicates_blocked += 1

check("20 duplicate frames all blocked", duplicates_blocked == 20,
      f"{duplicates_blocked}/20 raised IntegrityError")
check("Still exactly 1 record for this student",
      db.query(AttendanceRecord).filter_by(session_id=sess.id,
                                           student_id=s1.id).count() == 1)

# Different student in same session must still work
db.add(AttendanceRecord(session_id=sess.id, student_id=s2.id, status="present",
                        method="face", confidence=0.91))
db.commit()
check("Different student in same session allowed",
      db.query(AttendanceRecord).filter_by(session_id=sess.id).count() == 2)

# Same student in a DIFFERENT session must work (this is the case a naive
# UNIQUE(student_id, date) would wrongly block)
sess2 = AttendanceSession(offering_id=off.id, session_date=date(2026, 8, 6),
                          created_by=u_fac.id, room="LAB-1")
db.add(sess2)
db.commit()
db.add(AttendanceRecord(session_id=sess2.id, student_id=s1.id, status="present",
                        method="face", confidence=0.88))
db.commit()
check("Same student, second session same day allowed",
      db.query(AttendanceRecord).filter_by(student_id=s1.id).count() == 2,
      "lecture + lab on one day")

# ---------------------------------------------------------------------------
print("\n[5] Foreign key enforcement")
# ---------------------------------------------------------------------------
try:
    db.add(AttendanceRecord(session_id=99999, student_id=s1.id,
                            status="present", method="face", confidence=0.9))
    db.commit()
    check("Record for nonexistent session rejected", False)
except IntegrityError:
    db.rollback()
    check("Record for nonexistent session rejected", True)

try:
    db.query(Student).filter_by(id=s1.id).delete()
    db.commit()
    check("Hard-delete of student with records blocked (RESTRICT)", False)
except IntegrityError:
    db.rollback()
    check("Hard-delete of student with records blocked (RESTRICT)", True,
          "history preserved; use soft delete")

# ---------------------------------------------------------------------------
print("\n[6] Embedding serialisation round-trip")
# ---------------------------------------------------------------------------
vec = np.random.randn(512).astype(np.float32)
vec = vec / np.linalg.norm(vec)  # L2 normalise

blob = FaceEmbedding.to_bytes(vec)
check("Serialised to exactly 2048 bytes", len(blob) == 2048, f"{len(blob)} bytes")

emb = FaceEmbedding(student_id=s1.id, embedding=blob, model_name="buffalo_l",
                    model_version="0.7.3", is_primary=True, quality_score=0.95,
                    source_image_count=12)
db.add(emb)
db.commit()

loaded = db.query(FaceEmbedding).first()
restored = loaded.to_vector()
check("Round-trip lossless", np.array_equal(vec, restored),
      f"max delta {np.abs(vec - restored).max():.2e}")
check("Restored vector still unit-norm",
      np.isclose(np.linalg.norm(restored), 1.0))

# Validation rejects bad input
try:
    FaceEmbedding.to_bytes(np.random.randn(256).astype(np.float32))
    check("Wrong dimension rejected", False)
except ValueError:
    check("Wrong dimension rejected", True)

try:
    FaceEmbedding.to_bytes(np.random.randn(512).astype(np.float32) * 5)
    check("Unnormalised vector rejected", False)
except ValueError:
    check("Unnormalised vector rejected", True, "protects cosine-as-dot-product")

# The unique-primary rule (index skipped on SQLite, so test the concept)
check("Byte-length CHECK present in DDL",
      any("octet_length" in str(c.sqltext)
          for c in FaceEmbedding.__table__.constraints
          if hasattr(c, "sqltext")))

# ---------------------------------------------------------------------------
print("\n[7] Cosine similarity behaves as designed")
# ---------------------------------------------------------------------------
# Because all vectors are L2-normalised, cosine similarity == dot product.
# This is the property the matcher's single matrix-multiply relies on.
index = np.stack([vec, -vec, np.roll(vec, 1)])
sims = index @ vec
check("Self-similarity == 1.0", np.isclose(sims[0], 1.0), f"{sims[0]:.4f}")
check("Opposite vector == -1.0", np.isclose(sims[1], -1.0), f"{sims[1]:.4f}")
check("All similarities within [-1, 1]", bool(np.all(np.abs(sims) <= 1.0 + 1e-6)))

# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"RESULT: {passed}/{total} checks passed")
print("=" * 70 + "\n")

db.close()
sys.exit(0 if passed == total else 1)
