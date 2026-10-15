# Database — Setup, Normalization Proof, and Verification

**Status:** Complete and validated — 30/30 checks passing
**Tables:** 14 · **Indexes:** 35 (14 partial) · **Foreign keys:** 21 · **CHECK constraints:** 17 · **UNIQUE constraints:** 13

---

## 1. Why Each Table Exists — Quick Reference

| # | Table | Exists because… | Remove it and… |
|---|-------|-----------------|----------------|
| 1 | `roles` | Authorisation needs a canonical, queryable list of role types | Role strings drift ("faculty"/"Faculty"), silently breaking permission checks |
| 2 | `users` | One authentication identity for every human, whatever their role | Auth logic gets written 2–3 times and the copies drift apart |
| 3 | `refresh_tokens` | JWTs are self-validating and cannot otherwise be revoked | Logout becomes impossible; a stolen token works until expiry |
| 4 | `departments` | "Computer Science and Engineering" stored once, not 1,000 times | Update anomalies; typo'd variants silently break filters |
| 5 | `subjects` | The catalogue definition: code, title, credits, semester | No stable identity for a course across years |
| 6 | `subject_offerings` | **Binds subject + faculty + section + year into one teaching instance** | **Faculty authorisation becomes unwritable; sections collide; history corrupts on reassignment** |
| 7 | `enrollments` | Resolves many-to-many students ↔ offerings; defines the recognition search space | Cannot express "who is in this class"; matcher must search all 1,000 students |
| 8 | `students` | Learner-specific attributes + biometric consent state | `users` fills with NULLs; consent has nowhere to live (legally required) |
| 9 | `faculty` | Staff-specific attributes; anchors `offerings.faculty_id` | The authorisation chain has no link |
| 10 | `face_embeddings` | The 512-d vectors recognition compares against | No recognition possible |
| 11 | `attendance_sessions` | Bounds one class occurrence; enables the "mark rest absent" set operation | Absence undefined; two classes same day collide; no open/closed lifecycle |
| 12 | `attendance_records` | **The point of the entire system** | There is no system |
| 13 | `recognition_logs` | Measurement instrument for threshold calibration and accuracy evidence | Threshold stays a guess; accuracy claims unprovable |
| 14 | `audit_logs` | Answers "who changed this record and when?" | Attendance disputes are unresolvable |

---

## 2. Normalization Proof

### First Normal Form — atomic values, no repeating groups ✅

Every column holds a single value. There is no `subjects_taken` column containing a comma-separated list — that relationship lives in `enrollments`, one row per pair.

**Apparent exception:** `face_embeddings.embedding` stores 512 floats as `BYTEA`, and `audit_logs.details` stores JSONB.

These do not violate 1NF, because 1NF concerns values the *database* must decompose and query independently. An embedding is a single opaque mathematical object — you never query "the 87th dimension". Splitting it into 512 columns would be absurd. Likewise `details` is unstructured context, never joined on. The test for 1NF is whether the application ever needs to address components separately; here it does not.

### Second Normal Form — no partial dependency on a composite key ✅

2NF applies only to tables with composite keys. Every table here uses a single-column surrogate primary key, so partial dependency is structurally impossible.

The two junction tables that *could* have used composite keys — `enrollments` and `attendance_records` — instead use surrogate `id` columns with the natural pair enforced as a `UNIQUE` constraint. Both approaches satisfy 2NF; surrogate keys were chosen so foreign keys pointing at these rows stay single-column.

### Third Normal Form — no transitive dependency ✅

No non-key column determines another non-key column.

Worked example — the trap that catches most student projects:

```
students(id, roll_number, department_id, semester)
```

If we had stored `department_name` here instead of `department_id`, then `department_name` would depend on `department_id`, which depends on `id` — a transitive dependency, and a 3NF violation. Storing only the FK eliminates it. The same discipline is applied throughout: `subjects` stores `department_id` not the name, `attendance_records` stores `session_id` not the date.

### Boyce–Codd Normal Form ✅

Every determinant is a candidate key. Check the multi-attribute uniqueness rules:

| Table | Determinant | Is it a candidate key? |
|-------|-------------|------------------------|
| `subject_offerings` | (subject_id, section, academic_year) | ✅ Yes — enforced UNIQUE |
| `enrollments` | (student_id, offering_id) | ✅ Yes — enforced UNIQUE |
| `attendance_records` | (session_id, student_id) | ✅ Yes — enforced UNIQUE |

No overlapping candidate keys exist anywhere, so BCNF holds.

### Deliberate denormalization — one instance, documented

`students.face_enrolled` duplicates a fact derivable from `face_embeddings`.

**Justification:** the admin roster lists 1,000 students with an "enrolled?" badge. Deriving it means an `EXISTS` subquery per row or a `LEFT JOIN … GROUP BY` on every page load. This column turns that into a plain read.

**Safety rule:** exactly one component may write it — `EnrollmentService`. An undocumented denormalization is a bug waiting to happen; a documented one with a single writer is an optimisation.

---

## 3. Setup Instructions (Windows)

### Step 1 — Install PostgreSQL 16

Download from <https://www.postgresql.org/download/windows/>. During installation:
- Set a password for the `postgres` superuser — **write it down**
- Keep the default port `5432`
- Install pgAdmin 4 when offered (a GUI helps enormously while learning)

### Step 2 — Create the database and a dedicated user

Open **SQL Shell (psql)** from the Start menu, press Enter through the prompts, and enter your superuser password:

```sql
CREATE DATABASE attendance_db;
CREATE USER attendance_user WITH PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE attendance_db TO attendance_user;

\c attendance_db
GRANT ALL ON SCHEMA public TO attendance_user;
ALTER DATABASE attendance_db OWNER TO attendance_user;
```

> **Why a separate user rather than `postgres`?** Principle of least privilege. If the application is ever compromised, the attacker holds an account scoped to one database — not one that can drop every database on the server. This is standard practice and costs nothing.

### Step 3 — Create `backend/.env`

```env
ENVIRONMENT=development
DEBUG=true

POSTGRES_USER=attendance_user
POSTGRES_PASSWORD=your_secure_password_here
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=attendance_db
SQL_ECHO=true

SECRET_KEY=generate_this_in_step_4
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

FACE_MODEL_NAME=buffalo_l
FACE_MATCH_THRESHOLD=0.50
FACE_REVIEW_THRESHOLD=0.40
```

Confirm `.env` is listed in `.gitignore` before your next commit.

### Step 4 — Generate a real secret key

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste the output as `SECRET_KEY`. Never reuse an example key — it is what signs every authentication token in your system.

### Step 5 — Install dependencies

```
cd backend
venv\Scripts\activate
pip install sqlalchemy==2.0.31 psycopg2-binary==2.9.9 alembic==1.13.2 pydantic-settings==2.3.4 numpy==1.26.4
```

### Step 6 — Initialise Alembic

```
alembic init alembic
```

Then edit two files:

**`alembic.ini`** — comment out the hard-coded URL so it comes from `.env` instead:

```ini
# sqlalchemy.url = driver://user:pass@localhost/dbname
```

**`alembic/env.py`** — replace the `target_metadata` line and add the URL wiring:

```python
from app.models import Base          # imports ALL models — critical
from app.core.config import settings

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.database_url)
```

> **Why the `from app.models import Base` import matters:** Alembic compares `Base.metadata` against the live database. A model class only registers in that metadata when its module is imported. Miss an import and Alembic will not create the table — or worse, will generate a migration to **drop** it.

### Step 7 — Generate and apply the migration

```
alembic revision --autogenerate -m "initial schema"
```

**Read the generated file in `alembic/versions/` before applying it.** Autogenerate is good but not infallible; confirm it creates 14 tables and does not drop anything. Then:

```
alembic upgrade head
```

### Step 8 — Verify

```sql
\c attendance_db
\dt
```

You should see 14 tables plus `alembic_version`. Confirm the critical constraint exists:

```sql
\d attendance_records
```

Look for `"uq_attendance_session_student" UNIQUE CONSTRAINT, btree (session_id, student_id)`.

---

## 4. Verification Results

`scripts/validate_schema.py` exercises the schema against a live database. All 30 checks pass:

| Group | Checks | Result |
|-------|--------|--------|
| Relationship integrity | 5 | ✅ |
| Uniqueness constraints | 4 | ✅ |
| CHECK constraints | 5 | ✅ |
| **Duplicate prevention** | **5** | ✅ |
| Foreign key enforcement | 2 | ✅ |
| Embedding round-trip | 6 | ✅ |
| Cosine similarity properties | 3 | ✅ |

### The duplicate-prevention test, specifically

The suite simulates a camera at 2 fps hammering the endpoint with the same student for ten seconds:

```
[PASS] First mark succeeds
[PASS] 20 duplicate frames all blocked — 20/20 raised IntegrityError
[PASS] Still exactly 1 record for this student
[PASS] Different student in same session allowed
[PASS] Same student, second session same day allowed — lecture + lab on one day
```

That last check matters more than it looks. A naive `UNIQUE (student_id, session_date)` would have blocked it, breaking any subject with both a lecture and a lab on the same day. Keying on `session_id` instead is what makes multiple daily sessions work.

### Two real bugs the harness caught

Worth recording, because both would have shipped silently:

1. **`server_default="'A'"` rendered as `DEFAULT '''A'''`** — storing the literal three-character value `'A'` including quote marks. Every filter on `section = 'A'` would have missed every row. SQLAlchemy's `server_default` takes the literal value, not a quoted SQL fragment. The same bug was present on `attendance_sessions.status`.

2. **PostgreSQL's `~` regex operator is non-portable** — replaced with a SQL-standard `LIKE '____-____'` pattern, which enforces the same `2026-2027` shape while remaining testable on other dialects.

Neither would have appeared in code review. Both appeared instantly when the schema was actually built and exercised. This is the argument for running your validation before writing the service layer, not after.

---

## 5. Files Delivered

```
backend/
├── app/
│   ├── core/config.py          Typed settings from .env, fails fast on misconfiguration
│   ├── db/
│   │   ├── base_class.py       Declarative base + TimestampMixin + SoftDeleteMixin
│   │   └── session.py          Engine, pool tuning, get_db() dependency
│   └── models/
│       ├── __init__.py         Imports every model — required for Alembic discovery
│       ├── enums.py            Status/method/role enums with CHECK-constraint backing
│       ├── user.py             Role, User, RefreshToken
│       ├── academic.py         Department, Subject, SubjectOffering, Enrollment
│       ├── people.py           Student, Faculty
│       ├── face.py             FaceEmbedding + NumPy serialisation helpers
│       ├── attendance.py       AttendanceSession, AttendanceRecord
│       └── logs.py             RecognitionLog, AuditLog
└── scripts/
    ├── validate_schema.py      30-check verification suite
    └── dump_ddl.py             Emits the full PostgreSQL DDL

docs/
└── alembic/versions/           Versioned database migrations
```

---

## 6. Definition of Done — Milestone Checklist

- [ ] PostgreSQL 16 installed and running
- [ ] `attendance_db` created with a dedicated non-superuser account
- [ ] `.env` populated with a real generated `SECRET_KEY`
- [ ] `.env` confirmed present in `.gitignore`
- [ ] Alembic initialised and `env.py` wired to `app.models.Base`
- [ ] Initial migration generated, **read**, and applied
- [ ] `\dt` shows 14 tables
- [ ] `\d attendance_records` shows `uq_attendance_session_student`
- [ ] `python scripts/validate_schema.py` reports 30/30
- [ ] Committed with message: `feat(db): initial schema with 14 tables [2.1-3.3]`

---

## 7. Next Milestone

Database is complete. Task 3.4 is the seed script — roles, a default admin account, and a sample department — which needs password hashing, and that belongs with authentication. So the natural next step is **Day 6: Auth Foundation** (`security.py`, JWT utilities, login endpoint, `require_role` dependency), with the seed script written alongside it.

Alternatively, per the reordered roadmap, we jump to **Day 4: Face Detection** and get a webcam window drawing bounding boxes — the risky, unfamiliar part, tackled while there is still time to adapt.

Your call.
