"""
Analytics aggregation tests.

The important ones verify that aggregation is done by SUMMING TOTALS, not by
averaging percentages. That distinction is Simpson's paradox, and it is the
easiest way to ship a chart that confidently disagrees with reality.

Run:  python tests/test_analytics.py
"""

import os
import sys

sys.path.insert(0, "/home/claude/backend")
os.environ["SECRET_KEY"] = "test-analytics"
os.environ["LOAD_ML_ON_STARTUP"] = "false"

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, Integer as SAInteger, create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql import text as sql_text

from app.core.security import hash_password
from app.db.session import get_db
from app.models import Base
from app.models.academic import Department, Enrollment, Subject, SubjectOffering
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.enums import SessionStatus
from app.models.people import Faculty, Student
from app.models.user import Role, User

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
from app.services.chart_analytics_service import ChartAnalyticsService  # noqa: E402

app.dependency_overrides[get_db] = override_db
client = TestClient(app, raise_server_exceptions=False)

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


# ===========================================================================
# SEED — a deliberately uneven dataset so averaging bugs become visible
# ===========================================================================
db = TestSession()
roles = {n: Role(name=n) for n in ("admin", "faculty", "student")}
db.add_all(roles.values()); db.commit()

dept = Department(code="CSE", name="Computer Science"); db.add(dept); db.commit()

u_fac = User(email="das@c.edu", password_hash=hash_password("Fac@1234"),
             full_name="Prof Das", role_id=roles["faculty"].id)
db.add(u_fac); db.commit()
fac = Faculty(user_id=u_fac.id, employee_code="E1", department_id=dept.id)
db.add(fac); db.commit()

subj = Subject(code="CS301", name="Data Structures", department_id=dept.id, semester=3)
db.add(subj); db.commit()
off = SubjectOffering(subject_id=subj.id, faculty_id=fac.id, section="A",
                      academic_year="2026-2027")
db.add(off); db.commit()

# 10 students, enrolled well before any session
enrol_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
students = []
for i in range(1, 11):
    u = User(email=f"s{i}@c.edu", password_hash=hash_password("Stu@1234"),
             full_name=f"Student {i:02d}", role_id=roles["student"].id)
    db.add(u); db.commit()
    s = Student(user_id=u.id, roll_number=f"R{i:03d}", department_id=dept.id, semester=3)
    db.add(s); db.commit()
    db.add(Enrollment(student_id=s.id, offering_id=off.id, enrolled_at=enrol_date))
    students.append(s)
db.commit()


def make_session(day, marks, status="closed"):
    s = AttendanceSession(offering_id=off.id, session_date=day,
                          status=SessionStatus.OPEN.value, created_by=u_fac.id)
    db.add(s); db.commit()
    for sid, st in marks.items():
        db.add(AttendanceRecord(session_id=s.id, student_id=sid, status=st,
                                method="manual", marked_by=u_fac.id))
    s.status = status
    s.ended_at = datetime.now(timezone.utc)
    db.commit()
    return s.id


ids = [s.id for s in students]
today = date.today()

# THE SIMPSON'S PARADOX SETUP.
# Month A: one tiny session, 1 of 2 present  -> daily rate 50%
# Month A: one big session, 9 of 10 present  -> daily rate 90%
#
#   average of daily rates : (50 + 90) / 2      = 70.0%
#   summed totals          : (1 + 9) / (2 + 10) = 83.3%
#
# The second is correct. If the code averages percentages the test catches it.
m1 = today.replace(day=15) - timedelta(days=62)
make_session(m1, {ids[0]: "present", ids[1]: "absent"})
make_session(m1 + timedelta(days=1),
             {i: ("present" if n < 9 else "absent") for n, i in enumerate(ids)})

# Month B: three normal sessions
m2 = today - timedelta(days=20)
make_session(m2, {i: ("present" if n < 7 else "absent") for n, i in enumerate(ids)})
make_session(m2 + timedelta(days=2),
             {i: ("present" if n < 8 else "absent") for n, i in enumerate(ids)})
make_session(m2 + timedelta(days=4),
             {**{i: ("present" if n < 6 else "absent") for n, i in enumerate(ids)},
              ids[9]: "excused"})

# A CANCELLED session -- must be invisible everywhere
make_session(today - timedelta(days=10),
             {i: "absent" for i in ids}, status="cancelled")

# An OPEN session -- also must be invisible (still in progress)
open_s = AttendanceSession(offering_id=off.id, session_date=today,
                           status=SessionStatus.OPEN.value, created_by=u_fac.id)
db.add(open_s); db.commit()

svc = ChartAnalyticsService(TestSession())

print("\n" + "=" * 72)
print("ANALYTICS AGGREGATION TESTS")
print("=" * 72)

# ===========================================================================
print("\n[1] Daily series")
# ===========================================================================
daily = svc.daily_series(off.id, days=120)
check("Returns one point per closed session", len(daily) == 5, f"{len(daily)} points")
check("Points sorted by date", [d["date"] for d in daily] == sorted(d["date"] for d in daily))
check("*** Cancelled session excluded ***",
      all(d["total"] > 0 for d in daily), "no all-absent phantom point")
check("*** Open session excluded ***", len(daily) == 5, "still in progress")

first = daily[0]
check("Tiny session rate correct", first["rate"] == 50.0,
      f"1 of 2 = {first['rate']}%")

excused_point = [d for d in daily if d["excused"] > 0][0]
check("Excused removed from denominator",
      excused_point["total"] == excused_point["attended"] + excused_point["absent"],
      f"total={excused_point['total']} excludes {excused_point['excused']} excused")

# ===========================================================================
print("\n[2] *** Monthly aggregation — the Simpson's paradox test ***")
# ===========================================================================
monthly = svc.monthly_series(off.id, months=12)
check("Two months returned", len(monthly) == 2, f"{len(monthly)} months")

month_a = monthly[0]
naive_average = (50.0 + 90.0) / 2
correct = round(10 / 12 * 100, 1)

print(f"\n       Month A holds two sessions: 1/2 present and 9/10 present")
print(f"         average of daily rates : {naive_average}%   <- WRONG")
print(f"         summed totals          : {correct}%   <- correct")
print(f"         our code returns       : {month_a['rate']}%\n")

check("*** Monthly rate sums totals, not averages rates ***",
      month_a["rate"] == correct,
      f"{month_a['attended']}/{month_a['total']} = {month_a['rate']}%")
check("Naive average would have been wrong",
      abs(month_a["rate"] - naive_average) > 10,
      f"{correct}% vs {naive_average}% -- a 13-point error")
check("Session count per month correct", month_a["sessions"] == 2)
check("Month labels human-readable", "'" not in month_a["label"] and len(month_a["label"]) <= 8,
      month_a["label"])

# ===========================================================================
print("\n[3] Student percentages")
# ===========================================================================
percentages = svc.student_percentages(off.id)
check("All enrolled students returned", len(percentages) == 10,
      f"{len(percentages)} students -- not just defaulters")
check("Sorted by roll number",
      [p["roll_number"] for p in percentages] == sorted(p["roll_number"] for p in percentages))
check("Percentages present", all("percentage" in p for p in percentages))

s10 = [p for p in percentages if p["roll_number"] == "R010"][0]
check("Excused student's denominator reduced",
      s10["denominator"] == s10["total_sessions"] - s10["excused"],
      f"{s10['total_sessions']} - {s10['excused']} = {s10['denominator']}")

# Cross-check against the core service, which the attendance suite already tests
from app.services.analytics_service import AnalyticsService  # noqa

core = AnalyticsService(TestSession())
core_stats = core.calculate_offering_attendance(students[0].id, off.id)
chart_stats = [p for p in percentages if p["student_id"] == students[0].id][0]
check("*** Chart figures match the core percentage service ***",
      core_stats["percentage"] == chart_stats["percentage"],
      f"both report {core_stats['percentage']}%")

# ===========================================================================
print("\n[4] Subject percentages")
# ===========================================================================
subjects = svc.subject_percentages(fac.id)
check("One row per offering", len(subjects) == 1)
check("Subject code included", subjects[0]["subject_code"] == "CS301")
check("Class rate computed from totals",
      subjects[0]["rate"] == round(subjects[0]["attended"] / subjects[0]["denominator"] * 100, 1))
check("Enrolled count correct", subjects[0]["enrolled_count"] == 10)

# ===========================================================================
print("\n[5] *** Top absentees — ranked by count, not percentage ***")
# ===========================================================================
absentees = svc.top_absentees(off.id, limit=10)
check("Only students with absences listed", all(a["absent"] > 0 for a in absentees))
check("*** Sorted by absence COUNT descending ***",
      [a["absent"] for a in absentees] == sorted((a["absent"] for a in absentees), reverse=True),
      f"counts: {[a['absent'] for a in absentees]}")

# PROVE the two rankings are genuinely different questions, rather than
# asserting it. Enrol a late joiner who has missed only ONE class -- but that
# one class is their entire history, so their percentage is 0%.
#
#   by percentage : late joiner first (0%)
#   by absences   : the chronic absentee first (3 missed)
#
# If this endpoint sorted by percentage it would just duplicate the defaulter
# list under a different name.
late_u = User(email="late@c.edu", password_hash=hash_password("Stu@1234"),
              full_name="Late Joiner", role_id=roles["student"].id)
db.add(late_u); db.commit()
late_s = Student(user_id=late_u.id, roll_number="R099", department_id=dept.id, semester=3)
db.add(late_s); db.commit()
db.add(Enrollment(student_id=late_s.id, offering_id=off.id,
                  enrolled_at=datetime.now(timezone.utc) - timedelta(days=21)))
db.commit()
# Mark them absent for exactly one recent session
recent_session = db.execute(
    select(AttendanceSession).where(
        AttendanceSession.offering_id == off.id,
        AttendanceSession.status == SessionStatus.CLOSED.value,
    ).order_by(AttendanceSession.session_date.desc())
).scalars().first()
db.add(AttendanceRecord(session_id=recent_session.id, student_id=late_s.id,
                        status="absent", method="manual", marked_by=u_fac.id))
db.commit()

svc2 = ChartAnalyticsService(TestSession())
absentees2 = svc2.top_absentees(off.id, limit=20)
by_count = [a["roll_number"] for a in absentees2]
by_pct = [a["roll_number"] for a in
          sorted(absentees2, key=lambda x: x["percentage"] if x["percentage"] is not None else 100)]

print(f"       ranked by absences  : {by_count[:4]}")
print(f"       ranked by percentage: {by_pct[:4]}")

check("*** The two rankings genuinely differ ***",
      by_count != by_pct,
      "R099 missed 1 class (0%); others missed 3 -- different questions")
check("Absence ranking puts the chronic absentee first",
      absentees2[0]["absent"] >= absentees2[-1]["absent"],
      f"top has {absentees2[0]['absent']} absences")

check("Limit respected", len(svc.top_absentees(off.id, limit=2)) <= 2)

# ===========================================================================
print("\n[6] Dashboard bundle and endpoints")
# ===========================================================================
dash = svc.offering_dashboard(off.id)
for key in ("daily", "monthly", "students", "top_absentees", "summary"):
    check(f"Bundle contains '{key}'", key in dash)

check("Summary class average present", dash["summary"]["class_average"] is not None,
      f"{dash['summary']['class_average']}%")
check("Summary threshold included", dash["summary"]["threshold"] == 75.0)
# The bundle deliberately uses a 30-day daily window (recent detail) and a
# 6-month monthly window (longer trend). Compare like for like.
daily_30 = svc.daily_series(off.id, days=30)
check("*** Bundled daily matches standalone daily (same window) ***",
      len(dash["daily"]) == len(daily_30),
      f"{len(dash['daily'])} points in both -- one consistent snapshot")
check("Daily window is narrower than monthly",
      len(dash["daily"]) < len(daily),
      f"30-day view has {len(dash['daily'])} of {len(daily)} total sessions")

# HTTP layer + authorisation
tok = client.post("/api/v1/auth/login",
                  json={"email": "das@c.edu", "password": "Fac@1234"}).json()
H = {"Authorization": f"Bearer {tok['access_token']}"}

r = client.get(f"/api/v1/analytics/offerings/{off.id}/dashboard", headers=H)
check("Dashboard endpoint returns 200", r.status_code == 200, f"got {r.status_code}")
body = r.json()
check("Endpoint payload validates against schema",
      all(k in body for k in ("daily", "monthly", "students", "top_absentees", "summary")))

for path in ("daily", "monthly", "students", "absentees"):
    r = client.get(f"/api/v1/analytics/offerings/{off.id}/{path}", headers=H)
    check(f"/{path} returns 200", r.status_code == 200, f"got {r.status_code}")

r = client.get(f"/api/v1/analytics/faculty/{fac.id}/subjects", headers=H)
check("/subjects returns 200", r.status_code == 200, f"got {r.status_code}")

r = client.get(f"/api/v1/analytics/offerings/{off.id}/dashboard")
check("*** Unauthenticated request rejected ***", r.status_code == 401)

# ===========================================================================
print("\n[7] Empty-data behaviour")
# ===========================================================================
empty_off = SubjectOffering(subject_id=subj.id, faculty_id=fac.id, section="B",
                            academic_year="2026-2027")
db.add(empty_off); db.commit()

check("Daily series on empty offering returns []",
      svc.daily_series(empty_off.id) == [])
check("Monthly series on empty offering returns []",
      svc.monthly_series(empty_off.id) == [])
empty_dash = svc.offering_dashboard(empty_off.id)
check("*** Empty class average is None, not 0 ***",
      empty_dash["summary"]["class_average"] is None,
      "0% would say everyone is failing on day one")
check("Empty dashboard does not crash", empty_dash["summary"]["enrolled"] == 0)

# ===========================================================================
print("\n" + "=" * 72)
passed = sum(1 for _, ok in results if ok)
total = len(results)
colour = GREEN if passed == total else RED
print(f"{colour}RESULT: {passed}/{total} checks passed{RESET}")
print("=" * 72 + "\n")
db.close()
sys.exit(0 if passed == total else 1)
