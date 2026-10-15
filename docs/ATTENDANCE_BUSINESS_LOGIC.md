# Attendance Engine — Business Logic Specification

**Read this before the code.** Every rule below is a decision with consequences, and several are places where the obvious implementation is wrong.

---

## 1. The Session Lifecycle

Attendance is not a flat list of (student, subject, date) rows. It is organised into **sessions** — one occurrence of one class. The session is what makes the rest of the logic well-defined.

```
                    ┌──────────┐
      open() ──────▶│   OPEN   │
                    └────┬─────┘
                         │
              ┌──────────┴──────────┐
              │                     │
         close()               cancel()
              │                     │
              ▼                     ▼
        ┌──────────┐          ┌───────────┐
        │  CLOSED  │◀────────▶│ CANCELLED │
        └──────────┘  admin   └───────────┘
              ▲        only
              │
         reopen() — admin only, audit-logged
```

**Rules:**

| Rule | Statement |
|------|-----------|
| L-1 | Only faculty who **teach the offering** may open a session for it |
| L-2 | Only **one open session** per offering at a time |
| L-3 | Attendance may be marked **only** while status is `open` |
| L-4 | Closing marks every unrecognised enrolled student **absent** |
| L-5 | Closed sessions are immutable except to an Admin |
| L-6 | Cancelled sessions are **excluded from percentage calculations entirely** |

**Why L-6 matters:** a class cancelled for a holiday must not count against students. Without a session entity there is nothing to mark cancelled — the absence of records is indistinguishable from everyone being absent.

---

## 2. Duplicate Prevention — The Core Rule

**Requirement:** one attendance record per student per session.

### The naive implementation, and why it fails

```python
existing = db.query(AttendanceRecord).filter_by(
    session_id=sid, student_id=stid
).first()
if existing:
    return existing
db.add(AttendanceRecord(...))   # ← gap between check and insert
db.commit()
```

There is a window between the SELECT and the INSERT. Two requests that both pass the check before either commits will both insert. This is the classic **time-of-check-to-time-of-use race**.

**That window is not theoretical here.** The camera streams at 2 fps. A student standing in frame for ten seconds generates twenty recognition events. Network jitter routinely delivers two concurrently. The race *will* fire, probably in your first real class, and it silently inflates attendance counts.

### The correct implementation

```python
try:
    db.add(record)
    db.commit()
except IntegrityError:
    db.rollback()
    return existing_record        # someone else won the race — fine
```

PostgreSQL evaluates `UNIQUE (session_id, student_id)` atomically. Exactly one INSERT succeeds.

### The critical subtlety: SAVEPOINT

In PostgreSQL, an `IntegrityError` **aborts the entire transaction**. Every subsequent statement fails with `current transaction is aborted` until you roll back. If marking one student is part of a larger unit of work, one duplicate poisons everything.

The fix is a nested transaction — a SAVEPOINT:

```python
try:
    with db.begin_nested():        # SAVEPOINT
        db.add(record)
    db.commit()
except IntegrityError:
    # only the savepoint rolled back; the outer transaction survives
    return existing_record
```

This is the detail that separates code that works in a unit test from code that works under a live camera stream.

### Idempotency

Marking is **idempotent**: calling it twenty times produces the same result as calling it once, and every call returns success. The duplicate is not an error — the desired end state already holds.

Without this, nineteen of twenty frames produce errors that must be handled, logged, and suppressed in the UI. With it, they are no-ops.

---

## 3. Automatic Marking — The Decision Chain

A frame arrives. Each detected face runs this gauntlet, and **any step may reject it**:

```
1. Session open?              → no: 409 Conflict
2. Faculty owns offering?     → no: 403 Forbidden
3. Face passes quality gate?  → no: skip silently (too small/blurry/profile)
4. Similarity ≥ 0.40?         → no: UNKNOWN, log it, write nothing
5. Similarity ≥ 0.50?         → no: mark present + needs_review = true
6. Student enrolled here?     → no: reject ("not in this class")
7. Already marked?            → yes: return existing, no duplicate
8. Past late threshold?       → yes: status = LATE, else PRESENT
9. Write record + log
```

**Step 3 rejects silently, by design.** At 2 fps, surfacing "face too small" as an error would strobe the UI. Quality failures are normal, not exceptional.

**Step 6 is easy to omit and important.** A student from Section B wandering past Section A's camera must not be marked into Section A. Recognition identifies *who*; enrolment determines *whether they belong here*.

---

## 4. Absence Is Derived, Never Observed

**You cannot detect a student who is not there.** Absence is computed at session close as a set difference:

```
absentees = (students enrolled in offering) − (students with a record)
```

One bulk INSERT, not a loop. Properties of these auto-generated rows:

- `status = 'absent'`
- `method = 'manual'` — no face was recognised, so a confidence score would be meaningless
- `marked_by` = the closing user

**Enrolment date is respected.** A student who joined in week 6 is not marked absent for weeks 1–5. The `enrollments.enrolled_at` column exists for exactly this.

---

## 5. Late Marking

If `late_threshold_minutes` is set on the session and a student is first recognised after `started_at + threshold`, status is `LATE` rather than `PRESENT`.

`LATE` **counts as attended** for percentage purposes. It is a punctuality signal, not an absence. Institutions that want stricter treatment can change one line in `AttendanceStatus.counts_as_attended()` — the rule lives in exactly one place.

---

## 6. Attendance Percentage — Precise Definition

Ambiguity here produces disputes, so the formula is exact:

```
                    (sessions PRESENT) + (sessions LATE)
percentage = ─────────────────────────────────────────────── × 100
              (eligible closed sessions) − (excused sessions)
```

**Eligible sessions** are those where:
- `status = 'closed'` — open sessions are still in progress; cancelled ones never happened
- `session_date >= enrollment date` — mid-semester joiners are not penalised

**Excused absences are removed from the denominator**, not counted as attended. A student with medical leave neither gains nor loses — the session simply does not apply to them.

**When the denominator is zero, the percentage is `None`**, not 0% and not 100%. No sessions have occurred, so no percentage exists. The UI shows "—". Returning 0% would flag every student as a defaulter on day one; returning 100% would be a lie.

### Worked example

| Sessions | Count |
|----------|-------|
| Total sessions for the offering | 20 |
| — cancelled (holiday) | −2 |
| — before student's enrolment | −3 |
| **Eligible** | **15** |
| — excused (medical) | −1 |
| **Denominator** | **14** |
| Present | 10 |
| Late | 1 |
| **Numerator** | **11** |

**Result: 11 / 14 = 78.6%** — above the 75% threshold, so not flagged.

Note the naive calculation would give 10/20 = 50% and wrongly flag this student.

---

## 7. Subject-wise and Faculty-wise Views

The same records aggregate three ways. The `subject_offerings` table from the database module is what makes all three possible:

| View | Grouping | Who uses it |
|------|----------|-------------|
| **Subject-wise** | Per student, per offering | Student dashboard: "which subject am I failing?" |
| **Faculty-wise** | Per faculty, across their offerings | Faculty dashboard: "how are my classes doing?" |
| **Session-wise** | Per session | Faculty: "who was in Tuesday's lecture?" |

**Authorisation is different for each.** A student sees only their own rows. Faculty see only offerings they teach. Admin sees everything. This is enforced in the service layer — filtering in the query, not hiding in the UI.

---

## 8. The Authorisation Rule That Needs Care

> A faculty member may access attendance **only for offerings they teach.**

Resolving this is a join:

```
session → offering → faculty_id → user_id  ==  current_user.id
```

Checking only "is this user a faculty member?" leaves you open to **IDOR** (Insecure Direct Object Reference) — faculty A reading faculty B's class by guessing a session ID. The role check is necessary and not sufficient; ownership must be verified per resource.

---

## 9. Timezone Handling

- Stored as `TIMESTAMPTZ` in **UTC**, always
- `session_date` is stored **separately** as a local `DATE`

**Why both?** "Which day was this class?" is a local question. A 23:45 IST class is 18:15 UTC *the same day*, but a 00:30 IST class is 19:00 UTC the *previous* day. Deriving the date from a UTC timestamp gives the wrong answer for late-evening classes — and produces attendance filed under the wrong date in a way that is miserable to debug weeks later.

---

## 10. Failure Modes and Their Handling

| Scenario | Behaviour |
|----------|-----------|
| Camera fails mid-session | Session stays open; faculty mark manually |
| Model fails to load | Recognition returns 503; manual marking unaffected |
| Student recognised twice in one frame | Second is a no-op (idempotent) |
| Faculty closes session by accident | Admin can reopen; audit-logged |
| Student enrolled after session opened | Not marked absent — `enrolled_at` filter |
| Two faculty open sessions for one offering | Second gets 409 — only one open session allowed |
| Student appears in wrong classroom | Rejected at the enrolment check, not marked |

**Every automatic path has a manual fallback.** The system assists faculty; it never replaces their judgement. That principle is why manual override is available at every point, and it is what makes the system safe to deploy despite imperfect recognition.
