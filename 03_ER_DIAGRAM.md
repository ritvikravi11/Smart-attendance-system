# Database Design and ER Diagram
## AI-Powered Attendance Management System

**Version 1.0 · 14 tables · 35 indexes · 21 foreign keys · 17 CHECK constraints · 13 UNIQUE constraints**

The full generated DDL is in `schema.sql`.

---

## 1. Entity-Relationship Diagram

Paste into <https://mermaid.live> to render and export for your report.

```mermaid
erDiagram
    ROLES ||--o{ USERS : "categorises"
    USERS ||--o| STUDENTS : "is-profiled-as"
    USERS ||--o| FACULTY : "is-profiled-as"
    USERS ||--o{ REFRESH_TOKENS : "owns"
    USERS ||--o{ AUDIT_LOGS : "performs"

    DEPARTMENTS ||--o{ STUDENTS : "admits"
    DEPARTMENTS ||--o{ FACULTY : "employs"
    DEPARTMENTS ||--o{ SUBJECTS : "offers"

    STUDENTS ||--o{ FACE_EMBEDDINGS : "is-represented-by"
    STUDENTS ||--o{ ENROLLMENTS : "registers-via"
    STUDENTS ||--o{ ATTENDANCE_RECORDS : "receives"
    STUDENTS ||--o{ RECOGNITION_LOGS : "is-matched-in"

    SUBJECTS ||--o{ SUBJECT_OFFERINGS : "is-instantiated-as"
    FACULTY ||--o{ SUBJECT_OFFERINGS : "teaches"

    SUBJECT_OFFERINGS ||--o{ ENROLLMENTS : "accepts"
    SUBJECT_OFFERINGS ||--o{ ATTENDANCE_SESSIONS : "is-conducted-as"

    ATTENDANCE_SESSIONS ||--o{ ATTENDANCE_RECORDS : "contains"
    ATTENDANCE_SESSIONS ||--o{ RECOGNITION_LOGS : "produces"

    ROLES {
        int id PK
        varchar name UK
        varchar description
    }
    USERS {
        bigint id PK
        varchar email UK
        varchar password_hash
        varchar full_name
        int role_id FK
        boolean is_active
        int failed_login_count
        timestamptz locked_until
        timestamptz last_login_at
    }
    STUDENTS {
        bigint id PK
        bigint user_id FK-UK
        varchar roll_number UK
        int department_id FK
        smallint semester
        varchar phone
        timestamptz consent_given_at
        timestamptz consent_revoked_at
        boolean face_enrolled
        boolean is_active
    }
    FACULTY {
        bigint id PK
        bigint user_id FK-UK
        varchar employee_code UK
        int department_id FK
        varchar designation
        boolean is_active
    }
    DEPARTMENTS {
        int id PK
        varchar code UK
        varchar name UK
    }
    SUBJECTS {
        int id PK
        varchar code UK
        varchar name
        int department_id FK
        smallint semester
        smallint credits
    }
    SUBJECT_OFFERINGS {
        bigint id PK
        int subject_id FK
        bigint faculty_id FK
        varchar section
        varchar academic_year
        boolean is_active
    }
    ENROLLMENTS {
        bigint id PK
        bigint student_id FK
        bigint offering_id FK
        timestamptz enrolled_at
        boolean is_active
    }
    FACE_EMBEDDINGS {
        bigint id PK
        bigint student_id FK
        bytea embedding
        varchar model_name
        varchar model_version
        real quality_score
        boolean is_primary
        boolean is_active
    }
    ATTENDANCE_SESSIONS {
        bigint id PK
        bigint offering_id FK
        date session_date
        timestamptz started_at
        timestamptz ended_at
        varchar status
        varchar room
        int late_threshold_minutes
        bigint created_by FK
    }
    ATTENDANCE_RECORDS {
        bigint id PK
        bigint session_id FK
        bigint student_id FK
        varchar status
        varchar method
        real confidence
        boolean needs_review
        timestamptz marked_at
        bigint marked_by FK
        bigint overridden_by FK
    }
    RECOGNITION_LOGS {
        bigint id PK
        bigint session_id FK
        bigint matched_student_id FK
        real confidence
        boolean is_unknown
        boolean was_marked
        int face_width_px
        int processing_ms
    }
    AUDIT_LOGS {
        bigint id PK
        bigint user_id FK
        varchar action
        varchar entity_type
        bigint entity_id
        jsonb details
        varchar ip_address
    }
    REFRESH_TOKENS {
        bigint id PK
        bigint user_id FK
        varchar token_hash UK
        timestamptz expires_at
        timestamptz revoked_at
    }
```

---

## 2. Why Each Table Exists

| # | Table | Exists because… | Remove it and… |
|---|-------|-----------------|----------------|
| 1 | `roles` | Authorisation needs a canonical, queryable list | Role strings drift ("faculty"/"Faculty"), breaking permission checks |
| 2 | `users` | One authentication identity for every human | Auth logic gets written 2–3 times and the copies drift |
| 3 | `refresh_tokens` | JWTs are self-validating and otherwise unrevocable | Logout becomes impossible; a stolen token works until expiry |
| 4 | `departments` | "Computer Science" stored once, not 1,000 times | Update anomalies; typo'd variants break filters |
| 5 | `subjects` | The catalogue definition | No stable identity for a course across years |
| 6 | **`subject_offerings`** | **Binds subject + faculty + section + year** | **Faculty authorisation becomes unwritable** |
| 7 | `enrollments` | Resolves many-to-many; defines the recognition search space | Cannot express "who is in this class" |
| 8 | `students` | Learner attributes + biometric consent state | `users` fills with NULLs; consent has nowhere to live |
| 9 | `faculty` | Staff attributes; anchors `offerings.faculty_id` | The authorisation chain has no link |
| 10 | `face_embeddings` | The 512-d vectors recognition compares | No recognition possible |
| 11 | `attendance_sessions` | Bounds one class; enables the "mark rest absent" set operation | Absence undefined; two classes a day collide |
| 12 | **`attendance_records`** | **The point of the entire system** | There is no system |
| 13 | `recognition_logs` | Measurement instrument for calibration and accuracy claims | Threshold stays a guess; accuracy unprovable |
| 14 | `audit_logs` | Answers "who changed this and when?" | Attendance disputes unresolvable |

---

## 3. The Three Decisions Worth Defending

### 3.1 `subject_offerings` — the entity most projects omit

The naive design links attendance directly to `subjects`. It breaks in four ways:

1. **Authorisation becomes impossible.** "May Prof. Sharma view this session?" has no answer — the session points to CS301, and nothing records who teaches CS301. Faculty can read each other's data. This is an **IDOR** vulnerability, not a theoretical concern.
2. **Sections collide.** Section A (60 students) and Section B (55 students) both study CS301; their attendance lands in one unseparable bucket.
3. **History corrupts on reassignment.** A new lecturer takes over, and last year's attendance now appears to belong to them.
4. **Years run together.**

The fix models the offering as a first-class entity:

```
subject  — WHAT is taught      (CS301 Data Structures)
faculty  — WHO teaches it      (Prof. Das)
section  — to WHICH group      (Section B)
year     — WHEN                (2026-2027)
──────────────────────────────────────────────
offering — one teaching instance
```

Authorisation becomes a join: `session → offering → faculty_id`. In ER terms this is a **resolved ternary relationship**: when three or more entities must associate simultaneously, the correct model is an associative entity, not extra foreign keys sprinkled across the participants.

### 3.2 `UNIQUE (session_id, student_id)` — the most important line of DDL

This single constraint is the complete implementation of duplicate prevention.

The naive approach:
```python
if exists(session, student): return existing
insert(...)                            # ← gap between check and insert
```

There is a window between the SELECT and the INSERT. **Not theoretical:** the camera streams at 2 fps, so a student standing in frame for ten seconds generates twenty requests, and network jitter routinely delivers two concurrently.

The correct approach lets the database arbitrate:
```python
try:
    with db.begin_nested():        # SAVEPOINT
        db.add(record)
    db.commit()
except IntegrityError:
    db.rollback()
    return existing_record         # not an error — end state already holds
```

**The SAVEPOINT is essential.** In PostgreSQL an `IntegrityError` aborts the entire transaction; when marking six faces from one frame, one duplicate would poison the other five.

**Verified:** 20 consecutive duplicate marks → 0 spurious creations, exactly 1 record, and the *next* student still marks successfully.

### 3.3 Embeddings as `BYTEA`

| Encoding | Size | Verdict |
|----------|------|---------|
| `JSONB` | ~6 KB | ❌ 3× larger; float→text→float loses precision |
| `REAL[]` | ~2.5 KB | ❌ Per-element overhead; list→array conversion on every read |
| **`BYTEA`** | **2,048 bytes exactly** | ✅ Lossless, zero-copy `np.frombuffer` |

`model_name` and `model_version` are mandatory because **embeddings from different models are mathematically incomparable**. Cosine similarity between an ArcFace vector and another model's vector returns a number, and that number is meaningless. Without these columns, a model upgrade would silently corrupt every recognition while raising no error.

---

## 4. Normalisation Analysis

### 1NF ✅
Every column holds a single value. No repeating groups.

**Apparent exceptions:** `face_embeddings.embedding` (512 floats as BYTEA) and `audit_logs.details` (JSONB). Neither violates 1NF, because 1NF concerns values the *database* must decompose and query independently. An embedding is a single opaque mathematical object — you never query "the 87th dimension". The test is whether the application addresses components separately; here it does not.

### 2NF ✅
Applies only to composite keys. Every table uses a single-column surrogate key, so partial dependency is structurally impossible. The two junction tables use surrogate keys with the natural pair enforced as UNIQUE.

### 3NF ✅
No non-key column determines another. The trap avoided:

```
students(id, roll_number, department_id, semester)
```

Had `department_name` been stored here, it would depend on `department_id`, which depends on `id` — a transitive dependency. Storing only the FK eliminates it. Applied consistently: `subjects` stores `department_id`; `attendance_records` stores `session_id`, not the date.

### BCNF ✅
Every determinant is a candidate key:

| Table | Determinant | Candidate key? |
|-------|-------------|----------------|
| `subject_offerings` | (subject_id, section, academic_year) | ✅ UNIQUE |
| `enrollments` | (student_id, offering_id) | ✅ UNIQUE |
| `attendance_records` | (session_id, student_id) | ✅ UNIQUE |

### Deliberate denormalisation — one instance

`students.face_enrolled` duplicates a fact derivable from `face_embeddings`.

**Justification:** the admin roster lists 1,000 students with an "enrolled?" badge. Deriving it means an EXISTS subquery per row or a LEFT JOIN + GROUP BY on every page load.

**Safety rule:** exactly one component may write it — `EnrollmentService`. An undocumented denormalisation is a bug waiting to happen; a documented one with a single writer is an optimisation.

---

## 5. Constraint Inventory

### Uniqueness (13)
`roles.name` · `users.email` · `lower(users.email)` · `students.user_id` · `students.roll_number` · `faculty.user_id` · `faculty.employee_code` · `departments.code` · `departments.name` · `subjects.code` · `(subject_id, section, academic_year)` · `(student_id, offering_id)` · **`(session_id, student_id)`** · `refresh_tokens.token_hash`

### CHECK constraints (17)

The three doing real work:

```sql
-- Cross-column provenance: a face-marked row must carry confidence;
-- a manually-marked row must record who marked it.
CONSTRAINT ck_record_method_requires_provenance CHECK (
    (method = 'face'   AND confidence IS NOT NULL) OR
    (method = 'manual' AND marked_by  IS NOT NULL)
)

-- State machine invariant: closed sessions have an end time, open ones don't.
CONSTRAINT ck_session_ended_matches_status CHECK (
    (status = 'open'  AND ended_at IS NULL) OR
    (status <> 'open' AND ended_at IS NOT NULL)
)

-- Fails loudly at write time rather than producing wrong similarities later.
CONSTRAINT ck_embedding_byte_length CHECK (octet_length(embedding) = 2048)
```

### Partial indexes (14 of 35)

Because entities are soft-deleted, deactivated rows accumulate forever while queries almost always want only active ones. A partial index covers only those rows — smaller, faster, cheaper to maintain on write.

```sql
CREATE INDEX idx_enroll_offering ON enrollments(offering_id)
    WHERE is_active = true;

-- A UNIQUE partial index encoding a business rule the naive schema cannot:
-- a student may have many embeddings, but at most one marked primary.
CREATE UNIQUE INDEX uq_embedding_one_primary_per_student
    ON face_embeddings(student_id)
    WHERE is_primary = true AND is_active = true;
```

---

## 6. Referential Integrity Policy

| Relationship | Action | Why |
|--------------|--------|-----|
| `students` → `attendance_records` | RESTRICT | Attendance is history and must survive |
| `students` → `face_embeddings` | **CASCADE** | Right to erasure overrides the soft-delete convention |
| `sessions` → `attendance_records` | CASCADE | A record for a session that never happened is orphaned garbage |
| `users` → `audit_logs` | SET NULL | Audit entries outlive the users who created them |
| `roles` → `users` | RESTRICT | Deleting a role in use must fail |

---

## 7. Attendance Percentage — Precise Definition

Ambiguity here produces disputes, so:

```
                    present + late
percentage = ────────────────────────────── × 100
              eligible_closed − excused
```

**Eligible sessions:** `status = 'closed'` (open ones are in progress; cancelled never happened) AND `session_date >= enrolment date` (mid-semester joiners are not penalised).

**Excused absences are removed from the denominator**, not counted as attended.

**When the denominator is zero the percentage is `NULL`**, not 0% and not 100%.

### Worked example (verified in tests)

| | Count |
|---|---|
| Eligible closed sessions | 6 |
| — excused | −1 |
| **Denominator** | **5** |
| Present + late | 4 |
| **Result** | **4/5 = 80.0%** |

---

*End of Database Design v1.0*
