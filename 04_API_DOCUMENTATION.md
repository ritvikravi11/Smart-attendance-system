# API Documentation
## AI-Powered Attendance Management System

**Version 1.0 · 27 endpoints · Base URL `/api/v1`**

The machine-readable OpenAPI 3.1 specification is in `openapi.json`. When the server is running, interactive documentation is at **`http://localhost:8000/docs`** — generated from the code, so it can never drift from the implementation.

---

## 1. Conventions

### 1.1 Authentication

All endpoints except `POST /auth/login`, `POST /auth/refresh`, and `GET /health` require:

```
Authorization: Bearer <access_token>
```

Access tokens expire after 30 minutes. Clients should call `/auth/refresh` on a 401 and retry once.

### 1.2 Error envelope

Every failure uses one shape, so a client needs exactly one error handler:

```json
{
  "detail": "Human-readable message",
  "error_code": "DUPLICATE_ATTENDANCE",
  "field": "roll_number"
}
```

`error_code` is stable and safe to branch on; `detail` may be reworded or translated.

### 1.3 Status codes

| Code | Meaning in this system |
|------|------------------------|
| 200 / 201 | Success |
| 400 | Malformed request |
| **401** | **Missing or expired token — log in again** |
| **403** | **Valid token, insufficient role, or not your resource** |
| 404 | Resource does not exist |
| **409** | **Conflict — session already open, session not open** |
| 422 | Validation failure (Pydantic) |
| 423 | Account locked |
| 500 | Unhandled server error |

> The 401/403 distinction is routinely muddled. **401** means "who are you?" — the client should authenticate. **403** means "I know who you are, and the answer is no" — re-authenticating will not help.

### 1.4 Authorisation model

| Role | Access |
|------|--------|
| **Admin** | Everything |
| **Faculty** | Sessions, attendance, and analytics for **offerings they teach only** |
| **Student** | **Their own** attendance only |

Two independent checks run on faculty requests: a **role** check (are you faculty?) and an **ownership** check (do you teach this offering?). The role check alone would leave an IDOR hole.

---

## 2. Authentication

### `POST /auth/login`

Exchange credentials for a token pair.

**Request**
```json
{ "email": "das@college.edu", "password": "Faculty@123" }
```

**200 Response**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Errors:** `401` invalid credentials · `403` account deactivated · `423` locked after 5 failures

> **Security note:** a wrong password and an unknown email return the **identical** message. This prevents account enumeration. The server also runs a dummy bcrypt hash on unknown emails so response timing does not leak account existence either.

### `POST /auth/refresh`

**Request** `{ "refresh_token": "..." }` → **200** same shape as login.

> **Rotation:** the old refresh token is revoked and a new one issued. A stolen token works exactly once, and the moment either party uses it the other's copy dies — a visible symptom rather than a silent 7-day compromise.

### `POST /auth/logout`
Requires both a valid access token and the refresh token to revoke. Idempotent.

> **Honest limitation:** the access token remains valid until it expires (up to 30 minutes). A JWT is verified by signature alone. "Logout" means "no new access after this point", not "all access ceases instantly".

### `POST /auth/logout-all`
Revokes every active session for the current user.

### `GET /auth/me`
Returns the current user's profile. Called on page load to restore session state.

```json
{
  "id": 2,
  "email": "das@college.edu",
  "full_name": "Dr. Subhransu Das",
  "role": { "id": 2, "name": "faculty" },
  "is_active": true,
  "last_login_at": "2026-08-06T09:15:22Z",
  "created_at": "2026-07-01T10:00:00Z"
}
```

> `password_hash`, `failed_login_count`, and `locked_until` are **never** returned. The response schema is a separate object from the database model precisely so the exposed surface is an explicit decision.

### `POST /auth/change-password`

**Request** `{ "current_password": "...", "new_password": "..." }`

Requires the current password even though the caller is authenticated — an unattended logged-in laptop is a real threat. **All sessions are terminated on success.**

---

## 3. Attendance Sessions

### `POST /sessions`

Open a session.

**Request**
```json
{
  "offering_id": 1,
  "session_date": "2026-08-06",
  "room": "A-204",
  "late_threshold_minutes": 10
}
```

**201 Response** — session object with `enrolled_count`.

**Errors:** `403` you do not teach this offering · `409` a session is already open

> **Only one open session per offering.** If two were open, a recognised student would be ambiguous. Rather than inventing a tie-break, the situation is forbidden.

> `session_date` is a **local** date, stored separately from the UTC timestamp. A 23:45 IST class is 18:15 UTC the same day, but a 00:30 IST class is 19:00 UTC the *previous* day — deriving the date from UTC files late-evening classes under the wrong date.

### `POST /sessions/{id}/close`

Close the session and generate absentees.

**200 Response**
```json
{
  "session_id": 12,
  "status": "closed",
  "ended_at": "2026-08-06T10:45:00Z",
  "enrolled_count": 40,
  "present_count": 36,
  "late_count": 1,
  "absent_count": 3,
  "excused_count": 0,
  "review_count": 2,
  "auto_absent_created": 3
}
```

> **Absence is derived, never observed.** You cannot detect a student who is not there. Absentees are computed as a set difference — enrolled minus marked — in one bulk insert. Students who enrolled *after* this session's date are excluded.

### `POST /sessions/{id}/cancel?reason=...`
Cancelled sessions are excluded from percentage calculations entirely, so students are not penalised for a class that never happened.

### `POST /sessions/{id}/reopen`
**Admin only**, audit-logged. Deletes auto-generated absent records but **preserves** manual marks.

### `GET /sessions/{id}/roster`

Every enrolled student with current status. **Unmarked students appear with `status: null`** — which is what lets the UI show "12 of 40 marked" and offer one-click marking without a second request.

```json
{
  "session_id": 12,
  "session_status": "open",
  "enrolled_count": 40,
  "marked_count": 12,
  "entries": [
    {
      "student_id": 5,
      "roll_number": "2226CSE001",
      "student_name": "Ravi Kumar Sahu",
      "face_enrolled": true,
      "status": "present",
      "method": "face",
      "confidence": 0.8734,
      "needs_review": false,
      "marked_at": "2026-08-06T09:32:10Z"
    },
    {
      "student_id": 6,
      "roll_number": "2226CSE002",
      "student_name": "Priya Mohanty",
      "face_enrolled": true,
      "status": null
    }
  ]
}
```

---

## 4. Marking

### `POST /sessions/{id}/mark-face`

**The automatic marking endpoint.** Called at ~2 fps by the faculty UI.

**Request:** `multipart/form-data` with a `frame` field (JPEG or PNG, ≤10 MB).

**200 Response**
```json
{
  "faces": [
    {
      "bbox": [412, 180, 522, 323],
      "detection_score": 0.8712,
      "student_id": 5,
      "student_name": "Ravi Kumar Sahu",
      "roll_number": "2226CSE001",
      "confidence": 0.8734,
      "is_unknown": false,
      "needs_review": false,
      "quality_passed": true,
      "quality_reason": "OK",
      "marked": true,
      "already_marked": false,
      "rejected_reason": null
    }
  ],
  "processing_ms": 540,
  "newly_marked": 1,
  "total_marked": 13,
  "enrolled_count": 40
}
```

> **⚠️ IDEMPOTENT.** A student standing in frame for ten seconds generates twenty calls. The first creates a record; the other nineteen are harmless no-ops returning `already_marked: true`. That property is what makes a continuous camera stream safe against this endpoint.

**The nine-step decision chain:**

| Step | Check | Failure |
|------|-------|---------|
| 1 | Session open? | `409` |
| 2 | Faculty owns offering? | `403` |
| 3 | Face passes quality gate? | Skipped silently — at 2 fps an error would strobe |
| 4 | Similarity ≥ 0.40? | `is_unknown: true`, no record written |
| 5 | Similarity ≥ 0.50? | Marked with `needs_review: true` |
| 6 | Enrolled in this offering? | `rejected_reason` set |
| 7 | Already marked? | Returns existing, no duplicate |
| 8 | Past late threshold? | Status becomes `late` |
| 9 | Write record and log | — |

**`bbox` is in the coordinate space of the frame you submitted.** Render overlays as percentages of your frame's intrinsic dimensions, or boxes will land wrong on a differently-sized video element.

### `POST /sessions/{id}/mark-manual`

**Request** `{ "student_id": 5, "status": "present", "notes": "..." }`

The fallback that makes the system safe to deploy. If the camera fails, if recognition misses someone, if a face is not enrolled — attendance still happens.

### `PUT /attendance/{record_id}`

**Request** `{ "status": "absent", "notes": "corrected" }`

Sets `overridden_by` and `overridden_at`.

> The combination `(method='face', overridden_by IS NOT NULL)` is precisely your **false-positive count** — the accuracy metric for your project report. Every correction of an AI mark is a data point.

Closed sessions are immutable except to an Admin.

---

## 5. Recognition

### `POST /recognition/identify?offering_id=1`

Pure recognition. **Writes no attendance.**

> Deliberately separate from `mark-face`: an endpoint that mutates state should always be distinguishable from one that does not. This one is safe for testing, debugging, and future features with no risk of touching attendance data.

### `GET /recognition/health`

```json
{
  "model_loaded": true,
  "model_name": "buffalo_l",
  "model_version": "0.7.3",
  "index_size": 240,
  "index_memory_kb": 480.0,
  "match_threshold": 0.5,
  "review_threshold": 0.4
}
```

`index_size: 0` means no student has face data — every face will return Unknown.

### `POST /recognition/refresh-index`

Rebuilds the in-memory index from the database. Normally automatic after enrolment; exposed for the multi-worker case where one process holds a stale index.

---

## 6. Analytics

### `GET /students/{id}/attendance`

Subject-wise breakdown plus an overall figure.

```json
{
  "student_id": 5,
  "roll_number": "2226CSE001",
  "student_name": "Ravi Kumar Sahu",
  "overall_percentage": 80.0,
  "overall_attended": 4,
  "overall_denominator": 5,
  "is_defaulter": false,
  "subjects": [
    {
      "offering_id": 1,
      "subject_code": "CS301",
      "subject_name": "Data Structures and Algorithms",
      "section": "A",
      "faculty_name": "Dr. Subhransu Das",
      "total_sessions": 6,
      "present": 4, "late": 0, "absent": 1, "excused": 1,
      "attended": 4,
      "denominator": 5,
      "percentage": 80.0,
      "is_below_threshold": false
    }
  ]
}
```

> **`percentage` is `null`, never 0, when no eligible sessions have occurred.** Returning 0% would flag every student as a defaulter on day one.

> **Overall sums numerators and denominators** across subjects — it does not average per-subject percentages. Averaging would weight a 5-session subject equally with a 40-session one.

**Authorisation:** a student may request only their own ID. Faculty and Admin may request any.

### `GET /faculty/{id}/attendance`
Class averages, session counts, and defaulter counts per offering.

### `GET /offerings/{id}/defaulters?threshold=75`

```json
[{
  "student_id": 9,
  "roll_number": "2226CSE004",
  "student_name": "Anita Rout",
  "percentage": 62.5,
  "attended": 10,
  "denominator": 16,
  "shortfall_sessions": 8
}]
```

> `shortfall_sessions` solves `(attended + n)/(denominator + n) ≥ target` for n — how many consecutive future classes are needed. That is what students actually ask, and it is more actionable than a bare percentage.

---

## 7. Chart Analytics

### `GET /analytics/offerings/{id}/dashboard`

**All five charts in one snapshot.**

> **Why bundled.** The charts must agree with each other. Fetched separately, a session could close between call two and call four — the daily chart would include a session the student percentages do not, and the page would silently contradict itself.

```json
{
  "offering_id": 1,
  "daily": [ { "date": "2026-08-06", "present": 36, "late": 1, "absent": 3, "excused": 0, "attended": 37, "total": 40, "rate": 92.5 } ],
  "monthly": [ { "month": "2026-08", "label": "Aug 26", "sessions": 12, "attended": 430, "total": 480, "rate": 89.6 } ],
  "students": [ /* every enrolled student's percentage */ ],
  "top_absentees": [ /* ranked by classes missed */ ],
  "summary": { "enrolled": 40, "class_average": 84.2, "below_threshold": 3, "threshold": 75.0 }
}
```

### Individual series endpoints

| Endpoint | Returns |
|----------|---------|
| `GET /analytics/offerings/{id}/daily?days=30` | One point per **closed session**, not per calendar day |
| `GET /analytics/offerings/{id}/monthly?months=6` | Monthly aggregate |
| `GET /analytics/offerings/{id}/students` | **All** students, not just defaulters |
| `GET /analytics/faculty/{id}/subjects` | Class average per subject taught |
| `GET /analytics/offerings/{id}/absentees?limit=10` | Ranked by **absence count** |

> **Daily emits no point for days without a class.** Weekends and holidays are not zero-attendance days — they are no-data days, and plotting zeros draws a sawtooth that reads as collapsing attendance.

> **Monthly rate is computed from summed totals, never averaged from daily rates.** Two sessions of 1/2 and 9/10 present give 83.3% by summing and 70.0% by averaging — a 13-point error, and nothing about the wrong version looks broken. This is Simpson's paradox.

> **Absentees rank by count, not percentage** — deliberately a different question from the defaulter list. A student at 50% who missed 1 of 2 classes is a threshold problem; a student at 67% who missed 15 of 45 is not turning up.

---

## 8. System

### `GET /health`
Unauthenticated liveness probe. Returns `{ "status": "ok" }` and deliberately discloses nothing else — it is reachable by anyone.

---

## 9. Complete Endpoint Index

| Method | Path | Roles |
|--------|------|-------|
| POST | `/auth/login` | Public |
| POST | `/auth/refresh` | Public |
| POST | `/auth/logout` | All |
| POST | `/auth/logout-all` | All |
| GET | `/auth/me` | All |
| POST | `/auth/change-password` | All |
| POST | `/sessions` | Faculty, Admin |
| POST | `/sessions/{id}/close` | Faculty, Admin |
| POST | `/sessions/{id}/cancel` | Faculty, Admin |
| POST | `/sessions/{id}/reopen` | **Admin** |
| GET | `/sessions/{id}/roster` | Faculty, Admin |
| POST | `/sessions/{id}/mark-face` | Faculty, Admin |
| POST | `/sessions/{id}/mark-manual` | Faculty, Admin |
| PUT | `/attendance/{id}` | Faculty, Admin |
| POST | `/recognition/identify` | Faculty, Admin |
| GET | `/recognition/health` | All |
| POST | `/recognition/refresh-index` | Faculty, Admin |
| GET | `/students/{id}/attendance` | Admin, Faculty, **Self** |
| GET | `/faculty/{id}/attendance` | Admin, **Self** |
| GET | `/offerings/{id}/defaulters` | Faculty, Admin |
| GET | `/analytics/offerings/{id}/dashboard` | Faculty, Admin |
| GET | `/analytics/offerings/{id}/daily` | Faculty, Admin |
| GET | `/analytics/offerings/{id}/monthly` | Faculty, Admin |
| GET | `/analytics/offerings/{id}/students` | Faculty, Admin |
| GET | `/analytics/offerings/{id}/absentees` | Faculty, Admin |
| GET | `/analytics/faculty/{id}/subjects` | Faculty, Admin |
| GET | `/health` | Public |

---

## 10. Not Yet Implemented

Honest gaps, since an API document that lists endpoints which do not exist is worse than useless:

| Endpoint | Status |
|----------|--------|
| `POST /students/{id}/faces` | ❌ **The critical gap** — no face enrolment |
| `DELETE /students/{id}/faces` | ❌ Right to erasure (schema supports it) |
| `POST /students/{id}/consent` | ❌ Consent recording (column exists) |
| `POST/GET/PUT/DELETE /students` | ❌ Student CRUD |
| `POST/GET/PUT/DELETE /faculty` | ❌ Faculty CRUD |
| `POST/GET /subjects`, `/offerings` | ❌ Academic master data |
| `GET /reports/*/export/pdf` | ❌ Phase 2 |

Until face enrolment exists, the embedding index is empty and `mark-face` reports every face as Unknown.

---

*End of API Documentation v1.0*
