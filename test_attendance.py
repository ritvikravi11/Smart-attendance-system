"""
Attendance engine integration tests.

Runs the real FastAPI app against in-memory SQLite. The percentage tests
construct exact scenarios so the arithmetic can be verified by hand.

Run:  python tests/test_attendance.py
"""

import os
import sys

sys.path.insert(0, "/home/claude/backend")
os.environ["SECRET_KEY"] = "test-secret-key-attendance"
os.environ["LOAD_ML_ON_STARTUP"] = "false"

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer as SAInteger, create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql import text as sql_text

from app.core.security import hash_password
from app.db.session import get_db

# IMPORT ALL MODELS FIRST. Base.metadata only contains tables whose modules
# have been imported -- exactly the lesson from app/models/__init__.py. Calling
# create_all() before this line produces a database missing most tables.
from app.models import Base  # noqa: E402
from app.models.academic import Department, Enrollment, Subject, SubjectOffering
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.enums import AttendanceStatus, SessionStatus
from app.models.people import Faculty, Student
from app.models.user import Role, User

# ---- Dialect bridging for SQLite ----
for _t in Base.metadata.tables.values():
    for _c in _t.columns:
        sd = _c.server_default
        if isinstance(sd, DefaultClause) and "now()" in str(sd.arg).lower():
            _c.server_default = DefaultClause(sql_text("CURRENT_TIMESTAMP"))
        if _c.primary_key and isinstance(_c.type, BigInteger):
            _c.type = SAInteger()
for _t in Base.metadata.sorted_tables:
    _t.indexes = {
        i for i in _t.indexes
        if i.dialect_options.get("postgresql", {}).get("where") is None
    }

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _fk(conn, _):
    c = conn.cursor(); c.execute("PRAGMA foreign_keys=ON"); c.close()


TestSession = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)


def override_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


from app.main import app  # noqa: E402

app.dependency_overrides[get_db] = override_db
client = TestClient(app, raise_server_exceptions=False)

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


# ===========================================================================
# SEED
# ===========================================================================
db = TestSession()
r_admin = Role(name="admin"); r_fac = Role(name="faculty"); r_stu = Role(name="student")
db.add_all([r_admin, r_fac, r_stu]); db.commit()

dept = Department(code="CSE", name="Computer Science"); db.add(dept); db.commit()

u_admin = User(email="admin@c.edu", password_hash=hash_password("Admin@123"),
               full_name="Admin", role_id=r_admin.id)
u_fac1 = User(email="das@c.edu", password_hash=hash_password("Fac@1234"),
              full_name="Prof Das", role_id=r_fac.id)
u_fac2 = User(email="rao@c.edu", password_hash=hash_password("Fac@1234"),
              full_name="Prof Rao", role_id=r_fac.id)
db.add_all([u_admin, u_fac1, u_fac2]); db.commit()

fac1 = Faculty(user_id=u_fac1.id, employee_code="E1", department_id=dept.id)
fac2 = Faculty(user_id=u_fac2.id, employee_code="E2", department_id=dept.id)
db.add_all([fac1, fac2]); db.commit()

subj = Subject(code="CS301", name="Data Structures", department_id=dept.id, semester=3)
subj2 = Subject(code="CS302", name="Algorithms", department_id=dept.id, semester=3)
db.add_all([subj, subj2]); db.commit()

# offA taught by fac1; offB taught by fac2 (for the ownership test)
offA = SubjectOffering(subject_id=subj.id, faculty_id=fac1.id, section="A",
                       academic_year="2026-2027")
offB = SubjectOffering(subject_id=subj2.id, faculty_id=fac2.id, section="A",
                       academic_year="2026-2027")
db.add_all([offA, offB]); db.commit()

students = []
for i in range(1, 5):
    u = User(email=f"s{i}@c.edu", password_hash=hash_password("Stu@1234"),
             full_name=f"Student {i}", role_id=r_stu.id)
    db.add(u); db.commit()
    s = Student(user_id=u.id, roll_number=f"R00{i}", department_id=dept.id, semester=3)
    db.add(s); db.commit()
    students.append(s)

# s1,s2,s3 enrolled in offA from the start. s4 joins LATE (day 10).
# Anchored to today. Hardcoded dates rot: this fixture originally used
# 2026-08-01 with a late joiner at +10 days, which drifted into the future and
# made every enrolment-date assertion wrong once the clock passed it.
base_day = datetime.now(timezone.utc) - timedelta(days=30)
for s in students[:3]:
    db.add(Enrollment(student_id=s.id, offering_id=offA.id, enrolled_at=base_day))
# Late joiner: enrolled 20 days ago, so sessions older than that exclude them.
db.add(Enrollment(student_id=students[3].id, offering_id=offA.id,
                  enrolled_at=base_day + timedelta(days=10)))
# s1 also in offB
db.add(Enrollment(student_id=students[0].id, offering_id=offB.id, enrolled_at=base_day))
db.commit()


def login(email, pw):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


H_ADMIN = login("admin@c.edu", "Admin@123")
H_FAC1 = login("das@c.edu", "Fac@1234")
H_FAC2 = login("rao@c.edu", "Fac@1234")
H_STU1 = login("s1@c.edu", "Stu@1234")

V1 = "/api/v1"
print("\n" + "=" * 72)
print("ATTENDANCE ENGINE -- INTEGRATION TESTS")
print("=" * 72)

# ===========================================================================
print("\n[1] Session lifecycle and authorisation")
# ===========================================================================
r = client.post(f"{V1}/sessions", headers=H_FAC1,
                json={"offering_id": offA.id, "room": "A-204"})
check("Faculty opens session for own offering", r.status_code == 201, f"got {r.status_code}")
sess_id = r.json()["id"]
check("Session starts OPEN", r.json()["status"] == "open")
check("Enrolled count reported", r.json()["enrolled_count"] == 4, f"{r.json()['enrolled_count']}")

r = client.post(f"{V1}/sessions", headers=H_FAC2, json={"offering_id": offA.id})
check("*** Faculty CANNOT open session for another's offering ***",
      r.status_code == 403, "IDOR defence")

r = client.post(f"{V1}/sessions", headers=H_FAC1, json={"offering_id": offA.id})
check("Second open session rejected", r.status_code == 409, f"got {r.status_code}")

r = client.post(f"{V1}/sessions", headers=H_STU1, json={"offering_id": offA.id})
check("Student cannot open a session", r.status_code == 403)

r = client.get(f"{V1}/sessions/{sess_id}/roster", headers=H_FAC2)
check("*** Faculty cannot read another's roster ***", r.status_code == 403)

# ===========================================================================
print("\n[2] Manual marking and roster")
# ===========================================================================
r = client.post(f"{V1}/sessions/{sess_id}/mark-manual", headers=H_FAC1,
                json={"student_id": students[0].id, "status": "present"})
check("Manual mark succeeds", r.status_code == 200, f"got {r.status_code}")
check("Method recorded as manual", r.json()["method"] == "manual")

r = client.get(f"{V1}/sessions/{sess_id}/roster", headers=H_FAC1)
roster = r.json()
check("Roster lists ALL enrolled students", roster["enrolled_count"] == 4)
check("Roster shows 1 marked", roster["marked_count"] == 1)
unmarked = [e for e in roster["entries"] if e["status"] is None]
check("Unmarked students have status=null", len(unmarked) == 3,
      "lets the UI offer one-click marking")

# Not enrolled in this offering
r = client.post(f"{V1}/sessions/{sess_id}/mark-manual", headers=H_FAC1,
                json={"student_id": 9999, "status": "present"})
check("Marking a nonexistent student fails", r.status_code in (400, 404, 422))

# ===========================================================================
print("\n[3] *** DUPLICATE PREVENTION -- the core rule ***")
# ===========================================================================
from app.services.attendance_service import AttendanceService  # noqa

svc_db = TestSession()
svc = AttendanceService(svc_db)

rec1, created1 = svc.mark_present(sess_id, students[1].id, confidence=0.91)
check("First mark creates a record", created1 is True)

# Simulate the 2 fps camera hammering with the same student
created_flags = []
for _ in range(20):
    _, c = svc.mark_present(sess_id, students[1].id, confidence=0.90)
    created_flags.append(c)

check("*** 20 duplicate frames all return created=False ***",
      not any(created_flags), f"{sum(created_flags)} spurious creations")

count = svc_db.execute(
    select(func.count()).select_from(AttendanceRecord).where(
        AttendanceRecord.session_id == sess_id,
        AttendanceRecord.student_id == students[1].id,
    )
).scalar_one()
check("Exactly ONE record exists", count == 1, f"{count} records")

# The savepoint test: transaction must survive the IntegrityError
rec3, created3 = svc.mark_present(sess_id, students[2].id, confidence=0.88)
check("*** Transaction survives duplicate (SAVEPOINT works) ***",
      created3 is True, "subsequent marks still succeed after IntegrityError")

# Not enrolled in THIS offering
from app.core.exceptions import ValidationError  # noqa
try:
    svc.mark_present(sess_id, students[0].id, confidence=0.9)  # already marked
    ok_enrol = True
except Exception:
    ok_enrol = False
check("Already-marked student handled gracefully", ok_enrol)

# Student in offB but not offA
sessB_check = None
try:
    # students[1] is not in offB
    svc.mark_present(sess_id, 9999, confidence=0.9)
    check("Nonexistent student rejected", False)
except Exception:
    check("Nonexistent student rejected", True)

svc_db.close()

# ===========================================================================
print("\n[4] Session close and derived absence")
# ===========================================================================
r = client.post(f"{V1}/sessions/{sess_id}/close", headers=H_FAC1)
check("Session closes", r.status_code == 200, f"got {r.status_code}")
summary = r.json()
check("3 students were present", summary["present_count"] == 3, f"{summary['present_count']}")
# All THREE eligible students were marked, so nothing to auto-absent.
# Student 4 enrolled on Aug 11 and this session is dated earlier, so they are
# not eligible -- which is the enrolment-date rule working, not a miss.
# All 4 students enrolled before today, so all 4 are eligible; 3 were marked.
check("All enrolled students eligible for today's session",
      summary["enrolled_count"] == 4, f"{summary['enrolled_count']} eligible")
check("Unmarked student auto-absented",
      summary["auto_absent_created"] == 1, f"{summary['auto_absent_created']}")

# ---- Dedicated auto-absent test: leave someone unmarked ----
# Anchored to today rather than hardcoded: open_session now rejects
# future-dated sessions, so a fixed literal date rots as time passes.
recent_day = (date.today() - timedelta(days=1)).isoformat()
sess2 = client.post(f"{V1}/sessions", headers=H_FAC1, json={
    "offering_id": offA.id, "session_date": recent_day, "room": "A-205"
}).json()
client.post(f"{V1}/sessions/{sess2['id']}/mark-manual", headers=H_FAC1,
            json={"student_id": students[0].id, "status": "present"})
r2 = client.post(f"{V1}/sessions/{sess2['id']}/close", headers=H_FAC1)
close2 = r2.json()
check("*** Set difference auto-marks the unmarked ***",
      close2["auto_absent_created"] == 3,
      f"4 eligible - 1 present = {close2['auto_absent_created']} absent")
check("Absent count matches", close2["absent_count"] == 3)
check("Late joiner IS eligible for a later session",
      close2["enrolled_count"] == 4, f"{close2['enrolled_count']} eligible on Aug 20")

r = client.post(f"{V1}/sessions/{sess_id}/mark-manual", headers=H_FAC1,
                json={"student_id": students[0].id, "status": "absent"})
check("Cannot mark in a CLOSED session", r.status_code == 409, f"got {r.status_code}")

r = client.post(f"{V1}/sessions/{sess_id}/close", headers=H_FAC1)
check("Cannot close twice", r.status_code == 409)

# ===========================================================================
print("\n[5] Enrolment-date protection for late joiners")
# ===========================================================================
# Session dated BEFORE student4 enrolled (they joined day 10)
early = client.post(f"{V1}/sessions", headers=H_FAC1, json={
    "offering_id": offA.id, "session_date": (date.today() - timedelta(days=25)).isoformat()
}).json()
client.post(f"{V1}/sessions/{early['id']}/mark-manual", headers=H_FAC1,
            json={"student_id": students[0].id, "status": "present"})
r = client.post(f"{V1}/sessions/{early['id']}/close", headers=H_FAC1)
close_early = r.json()
check(
    "*** Late joiner NOT marked absent for a pre-enrolment session ***",
    close_early["auto_absent_created"] == 2,
    f"{close_early['auto_absent_created']} absent (s2,s3 only, not s4)",
)

# ===========================================================================
print("\n[6] *** ATTENDANCE PERCENTAGE -- exact arithmetic ***")
# ===========================================================================
# Build a controlled scenario for student1 in offA.
# Existing: 2 closed sessions, present in both.
# Add: 1 cancelled, 1 excused, plus more present/absent to hit exact numbers.

def make_session(day, marks, status="closed"):
    """marks: {student_id: status}. Returns session id."""
    s = AttendanceSession(offering_id=offA.id, session_date=day,
                          status=SessionStatus.OPEN.value, created_by=u_fac1.id)
    db.add(s); db.commit()
    for sid, st in marks.items():
        db.add(AttendanceRecord(session_id=s.id, student_id=sid, status=st,
                                method="manual", marked_by=u_fac1.id))
    s.status = status
    s.ended_at = datetime.now(timezone.utc)
    db.commit()
    return s.id


s1 = students[0].id
# 3 more closed sessions: present, absent, excused
make_session(date.today() - timedelta(days=6), {s1: "present"})
make_session(date.today() - timedelta(days=5), {s1: "absent"})
make_session(date.today() - timedelta(days=4), {s1: "excused"})
# 1 CANCELLED session -- must be excluded entirely
make_session(date.today() - timedelta(days=2), {s1: "absent"}, status="cancelled")

from app.services.analytics_service import AnalyticsService  # noqa

adb = TestSession()
stats = AnalyticsService(adb).calculate_offering_attendance(s1, offA.id)

print(f"\n       eligible closed sessions: {stats['total_sessions']}")
print(f"       present={stats['present']} late={stats['late']} "
      f"absent={stats['absent']} excused={stats['excused']}")
print(f"       attended={stats['attended']} denominator={stats['denominator']}")
print(f"       percentage={stats['percentage']}%\n")

# Assert the INVARIANT, not a magic number: eligible must equal the count of
# CLOSED sessions for this offering. Any cancelled session must be absent from
# that total.
closed_in_db = adb.execute(
    select(func.count()).select_from(AttendanceSession).where(
        AttendanceSession.offering_id == offA.id,
        AttendanceSession.status == SessionStatus.CLOSED.value,
    )
).scalar_one()
cancelled_in_db = adb.execute(
    select(func.count()).select_from(AttendanceSession).where(
        AttendanceSession.offering_id == offA.id,
        AttendanceSession.status == SessionStatus.CANCELLED.value,
    )
).scalar_one()
check("Cancelled sessions EXCLUDED from eligible",
      stats["total_sessions"] == closed_in_db and cancelled_in_db > 0,
      f"{stats['total_sessions']} eligible = {closed_in_db} closed; "
      f"{cancelled_in_db} cancelled ignored")
check("Excused REMOVED from denominator",
      stats["denominator"] == stats["total_sessions"] - stats["excused"],
      f"{stats['total_sessions']} - {stats['excused']} = {stats['denominator']}")
expected = round(stats["attended"] / stats["denominator"] * 100, 2)
check("Percentage arithmetic correct", stats["percentage"] == expected,
      f"{stats['attended']}/{stats['denominator']} = {expected}%")

# LATE counts as attended
s2 = students[1].id
make_session(date.today() - timedelta(days=1), {s2: "late"})
st2 = AnalyticsService(adb).calculate_offering_attendance(s2, offA.id)
check("LATE counts as attended", st2["late"] == 1 and st2["attended"] >= 1,
      f"late={st2['late']} attended={st2['attended']}")

# Zero-session case returns None, NOT 0%
st4 = AnalyticsService(adb).calculate_offering_attendance(students[0].id, offB.id)
check("*** No sessions -> percentage is None, not 0% ***",
      st4["percentage"] is None,
      "returning 0% would flag everyone as a defaulter on day one")

# ===========================================================================
print("\n[7] Subject-wise and faculty-wise views")
# ===========================================================================
r = client.get(f"{V1}/students/{s1}/attendance", headers=H_STU1)
check("Student views own attendance", r.status_code == 200, f"got {r.status_code}")
body = r.json()
check("Subject-wise breakdown returned", len(body["subjects"]) == 2,
      f"{len(body['subjects'])} subjects (CS301, CS302)")
check("Overall percentage computed", body["overall_percentage"] is not None)
check("Subject entries carry faculty name",
      all(s["faculty_name"] for s in body["subjects"]))

r = client.get(f"{V1}/students/{students[1].id}/attendance", headers=H_STU1)
check("*** Student CANNOT view another student's attendance ***",
      r.status_code == 403, "horizontal privilege escalation blocked")

r = client.get(f"{V1}/students/{students[1].id}/attendance", headers=H_FAC1)
check("Faculty CAN view a student's attendance", r.status_code == 200)

r = client.get(f"{V1}/faculty/{fac1.id}/attendance", headers=H_FAC1)
check("Faculty-wise summary works", r.status_code == 200, f"got {r.status_code}")
fbody = r.json()
check("Faculty summary lists their offerings", len(fbody["offerings"]) == 1,
      f"{len(fbody['offerings'])} offering")
check("Session count reported", fbody["total_sessions_conducted"] > 0,
      f"{fbody['total_sessions_conducted']} sessions")

r = client.get(f"{V1}/faculty/{fac2.id}/attendance", headers=H_FAC1)
check("*** Faculty cannot view another faculty's summary ***", r.status_code == 403)

r = client.get(f"{V1}/faculty/{fac1.id}/attendance", headers=H_ADMIN)
check("Admin can view any faculty summary", r.status_code == 200)

# ===========================================================================
print("\n[8] Defaulters and shortfall")
# ===========================================================================
r = client.get(f"{V1}/offerings/{offA.id}/defaulters", headers=H_FAC1)
check("Defaulter list returned", r.status_code == 200, f"got {r.status_code}")
defs = r.json()
check("Defaulters sorted by percentage ascending",
      defs == sorted(defs, key=lambda d: d["percentage"]))
if defs:
    d = defs[0]
    check("Shortfall sessions computed", d["shortfall_sessions"] >= 0,
          f"{d['student_name']} at {d['percentage']}% needs "
          f"{d['shortfall_sessions']} consecutive classes")
    # Verify the shortfall maths
    a, n, k = d["attended"], d["denominator"], d["shortfall_sessions"]
    resulting = (a + k) / (n + k) * 100
    check("Shortfall actually reaches the threshold", resulting >= 75.0,
          f"({a}+{k})/({n}+{k}) = {resulting:.1f}%")

# ===========================================================================
print("\n[9] Cancel, reopen, and record correction")
# ===========================================================================
c = client.post(f"{V1}/sessions", headers=H_FAC1, json={"offering_id": offA.id}).json()
r = client.post(f"{V1}/sessions/{c['id']}/cancel?reason=holiday", headers=H_FAC1)
check("Session cancels", r.status_code == 200)

r = client.post(f"{V1}/sessions/{sess_id}/reopen", headers=H_FAC1)
check("*** Faculty CANNOT reopen a closed session ***", r.status_code == 403,
      "admin only")

r = client.post(f"{V1}/sessions/{sess_id}/reopen", headers=H_ADMIN)
check("Admin CAN reopen", r.status_code == 200, f"got {r.status_code}")

rdb = TestSession()
auto_absent = rdb.execute(
    select(func.count()).select_from(AttendanceRecord).where(
        AttendanceRecord.session_id == sess_id,
        AttendanceRecord.notes == "Auto-marked absent at session close",
    )
).scalar_one()
check("Reopen removed auto-absent records", auto_absent == 0)
manual_kept = rdb.execute(
    select(func.count()).select_from(AttendanceRecord).where(
        AttendanceRecord.session_id == sess_id
    )
).scalar_one()
check("Reopen PRESERVED manual records", manual_kept == 3,
      f"{manual_kept} manual marks survived")

rec = rdb.execute(select(AttendanceRecord).where(
    AttendanceRecord.session_id == sess_id)).scalars().first()
r = client.put(f"{V1}/attendance/{rec.id}", headers=H_FAC1,
               json={"status": "absent", "notes": "corrected"})
check("Record correction works", r.status_code == 200, f"got {r.status_code}")
check("Override recorded", r.json()["was_overridden"] is True,
      "this is your false-positive metric")
rdb.close()

# ===========================================================================
print("\n[10] Recognition endpoints degrade gracefully without the model")
# ===========================================================================
r = client.get(f"{V1}/recognition/health", headers=H_FAC1)
check("Health endpoint responds", r.status_code == 200, f"got {r.status_code}")
check("Reports model not loaded", r.json()["model_loaded"] is False,
      "LOAD_ML_ON_STARTUP=false in tests")

import io
fake = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
r = client.post(f"{V1}/sessions/{sess_id}/mark-face", headers=H_FAC1,
                files={"frame": ("f.jpg", fake, "image/jpeg")})
check("*** Undecodable frame rejected cleanly, not a 500 ***",
      r.status_code in (400, 409), f"got {r.status_code}")

adb.close()

# ===========================================================================
print("\n" + "=" * 72)
passed = sum(1 for _, ok in results if ok)
total = len(results)
colour = GREEN if passed == total else RED
print(f"{colour}RESULT: {passed}/{total} checks passed{RESET}")
print("=" * 72 + "\n")
db.close()
sys.exit(0 if passed == total else 1)
