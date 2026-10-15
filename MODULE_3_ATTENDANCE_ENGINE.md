# Module 3 — Attendance Engine

**Status:** Complete — 59/59 checks passing
**Endpoints:** 15 new (26 total) · **Services:** 3 · No regressions in prior modules

Business rules are specified in **`ATTENDANCE_BUSINESS_LOGIC.md`** — read that first. This document covers what was built, what was measured, and what broke.

---

## 1. Requirements Coverage

| Requirement | Implementation | Verified by |
|-------------|----------------|-------------|
| Mark attendance automatically | `RecognitionService.identify_and_mark()` | 9-step decision chain |
| Prevent duplicate attendance | `UNIQUE(session_id, student_id)` + insert-and-catch + SAVEPOINT | 20 duplicate frames → 1 record |
| Subject-wise attendance | `AnalyticsService.student_summary()` | 2 subjects, per-offering breakdown |
| Faculty-wise attendance | `AnalyticsService.faculty_summary()` | Class averages, defaulter counts |
| Date and time recording | `session_date` (local DATE) + `TIMESTAMPTZ` | Late-joiner exclusion |
| Percentage calculation | `calculate_offering_attendance()` | Exact arithmetic verified by hand |

---

## 2. The Two Rules That Matter Most

### 2.1 Duplicate prevention needs a SAVEPOINT, not just a constraint

The database constraint alone isn't enough. In PostgreSQL an `IntegrityError` **aborts the entire transaction** — every subsequent statement fails with `current transaction is aborted`. When you're marking six faces from one frame, one duplicate would poison the other five.

```python
try:
    with self.db.begin_nested():   # SAVEPOINT
        self.db.add(record)
    self.db.commit()
except IntegrityError:
    self.db.rollback()             # only the savepoint unwinds
    return existing_record, False  # not an error — end state already holds
```

**Measured:** 20 consecutive duplicate marks → 0 spurious creations, exactly 1 record, and the transaction still healthy enough that the *next* student marks successfully.

That last part is the whole point. Without the savepoint the test passes on duplicate prevention and then fails on the very next insert.

### 2.2 Absence is derived, never observed

You cannot detect a student who isn't there. At session close:

```
absentees = (students enrolled on or before session_date) − (students with a record)
```

One bulk insert, not a loop. Auto-generated rows carry `method='manual'` because no face was recognised — a confidence score would be meaningless, and the CHECK constraint would reject `method='face'` with a NULL confidence anyway.

**Measured:** 4 eligible students, 1 marked present → 3 auto-absent.

---

## 3. The Percentage Formula, Verified

```
                    present + late
percentage = ───────────────────────────── × 100
              eligible_closed − excused
```

Test scenario, arithmetic checked by hand:

```
eligible closed sessions: 6      (cancelled session correctly excluded)
present=4  late=0  absent=1  excused=1
attended    = 4                  (present + late)
denominator = 6 − 1 = 5          (excused removed, not counted as absent)
percentage  = 4/5 = 80.0%
```

Three edge cases the tests pin down:

| Case | Behaviour | Why |
|------|-----------|-----|
| Cancelled session | Excluded from eligible entirely | The class never happened |
| Excused absence | Removed from **denominator** | Neither helps nor hurts the student |
| Zero eligible sessions | `percentage = None`, not `0%` | 0% would flag every student as a defaulter on day one |
| `LATE` status | Counts as **attended** | Punctuality signal, not absence |

**Overall percentage sums numerators and denominators across subjects** — it does not average the per-subject percentages. Averaging would weight a 5-session subject equally with a 40-session one. Summing gives each *session* equal weight, which is what "overall attendance" means.

### The shortfall calculation

Defaulters get `shortfall_sessions` — how many consecutive future classes are needed to climb back above threshold. Solving `(attended + n)/(denominator + n) ≥ target`:

```
n ≥ (target × denominator − attended) / (1 − target)
```

**Verified:** a student at 0% with 4 sessions needs 12 consecutive classes → (0+12)/(4+12) = 75.0%. Exactly the threshold.

That number is what students actually want to know, and it's more actionable than a bare percentage.

---

## 4. A Real Bug: String Booleans

`server_default="true"` renders as **`DEFAULT 'true'`** — a string literal, not a boolean.

PostgreSQL happens to cast it, so it works there. SQLite stores the literal text `'true'`, so `WHERE is_active IS true` matched **nothing**. Every enrolment lookup returned empty; marking failed with "not enrolled"; rosters showed zero students.

Fixed across 9 columns in 6 files by using `sqlalchemy.true()` / `false()`, which emit a proper boolean in every dialect.

```sql
-- before:  is_active BOOLEAN DEFAULT 'true' NOT NULL
-- after:   is_active BOOLEAN DEFAULT true   NOT NULL
```

This is the **third** instance of the same root cause in this project — after `section='A'` and `status='open'` in the database module. **The lesson worth internalising: SQLAlchemy's `server_default` takes a literal value or a SQL expression, never a pre-quoted string.** If you find yourself typing quotes inside `server_default`, stop.

It also explains why the bug survived the database module's tests: those tests never filtered on a boolean column. A bug is only found by a test that would notice it.

---

## 5. Authorisation — Three Levels, All Tested

| Check | Blocked | Test |
|-------|---------|------|
| Role | Student opening a session | 403 |
| **Ownership** | Faculty A opening a session for Faculty B's offering | 403 |
| **Ownership** | Faculty A reading Faculty B's roster | 403 |
| **Horizontal** | Student A viewing Student B's attendance | 403 |
| **Horizontal** | Faculty A viewing Faculty B's summary | 403 |
| Admin-only | Faculty reopening a closed session | 403 |

The ownership checks are the ones that matter. Checking only "is this user faculty?" leaves an **IDOR** hole — faculty reading each other's classes by guessing session IDs. The role check is necessary and not sufficient.

---

## 6. Session Lifecycle

```
open() ──▶ OPEN ──┬── close()  ──▶ CLOSED ──┐
                  │                          │ reopen() [admin, audited]
                  └── cancel() ──▶ CANCELLED │
                                    ◀────────┘
```

**Reopen preserves manual marks and deletes only auto-generated absences.** Verified: after reopen, 3 hand-marked records survived and 0 auto-absent rows remained. Deleting a faculty member's deliberate correction would be a data-loss bug.

**Only one open session per offering.** A second attempt returns 409. If two were open, a recognised student would be ambiguous — rather than inventing a tie-break, the situation is forbidden.

---

## 7. Files Delivered

```
backend/app/
├── services/
│   ├── attendance_service.py    Session lifecycle, race-safe marking, roster
│   ├── analytics_service.py     Percentage, subject-wise, faculty-wise, defaulters
│   └── recognition_service.py   CV pipeline → attendance bridge
├── schemas/attendance.py        15 request/response contracts
├── api/v1/endpoints/attendance.py   15 endpoints
└── main.py                      Model + index loading wired into lifespan

backend/tests/test_attendance.py  59 checks
docs/ATTENDANCE_BUSINESS_LOGIC.md Business rules specification
```

---

## 8. How to Run and Test

```
cd backend
alembic revision --autogenerate -m "boolean default fix"
alembic upgrade head
python -m app.db.init_db --demo
uvicorn app.main:app --reload
```

Startup now loads the face model (~3s) and builds the embedding index. Set `LOAD_ML_ON_STARTUP=false` in `.env` to skip it when working on API-only changes.

**A failed model load does not stop the server.** Recognition endpoints return 503; manual marking is unaffected. That's the graceful degradation from NFR-4.2, and it's why a camera failure never blocks a class.

### Automated

```
python tests/test_attendance.py     # 59/59
```

### By hand, through `/docs`

1. Log in as `das@college.edu` / `Faculty@123`, click **Authorize**
2. `POST /sessions` with `{"offering_id": 1, "room": "A-204"}` → note the session id
3. `GET /sessions/{id}/roster` → all enrolled students, `status: null`
4. `POST /sessions/{id}/mark-manual` → `{"student_id": 1, "status": "present"}`
5. **Run step 4 again** → still one record, now flagged as an override
6. `POST /sessions/{id}/close` → the rest are auto-marked absent
7. `GET /students/1/attendance` → subject-wise breakdown with percentages

Then try to break it:

- Log in as `rao@college.edu` and open a session for offering 1 → **403**
- Log in as a student and request another student's attendance → **403**
- Close the session twice → **409**
- Mark attendance in a closed session → **409**

---

## 9. Definition of Done

- [ ] Migration applied for the boolean default fix
- [ ] `python tests/test_attendance.py` reports 59/59
- [ ] Prior suites still pass (auth 61/61, ML 144/144, schema 30/30)
- [ ] Server starts and logs "Embedding index ready: N students"
- [ ] Session opens, roster shows all enrolled students
- [ ] Marking twice produces one record
- [ ] Close auto-marks the unmarked as absent
- [ ] Cross-faculty access returns 403
- [ ] Committed: `feat(attendance): session lifecycle, race-safe marking, analytics [10.1-10.5]`

---

## 10. What's Next

The engine is complete and tested, but **face enrolment has no endpoint yet** — `face_embeddings` rows can only be created directly in the database. So `mark-face` works mechanically while the index is empty, meaning every face returns UNKNOWN.

**Module 4 — Face Enrolment API** closes that gap:
- `POST /students/{id}/faces` — consent check, strict quality gate, 5–15 images, intra-set consistency, averaging, raw-image deletion, index refresh
- `DELETE /students/{id}/faces` — right to erasure
- `POST /students/{id}/consent` — record biometric consent

After that the full loop works end to end: enrol a student's face, open a session, point the camera, and watch attendance mark itself. That's the demo.

Then Module 5 is the React frontend.
