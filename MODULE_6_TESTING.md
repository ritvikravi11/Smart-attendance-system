# Module 6 — Testing

**Status:** Complete — 142 pytest tests + 354 legacy checks, all passing
**Coverage:** 75% overall · 89–96% on business-critical services
**Bugs found and fixed:** 2 real, 1 latent

---

## 1. What Was Built

| Layer | File | Tests | What it proves |
|-------|------|-------|----------------|
| **Unit** | `tests/unit/test_units.py` | 41 | Pure logic: hashing, JWTs, the percentage formula |
| **API** | `tests/api/test_api.py` | 56 | HTTP contract and the full authorization matrix |
| **Integration** | `tests/integration/test_flows.py` | 11 | Complete workflows across every layer |
| **Edge cases** | `tests/test_edge_cases.py` | 34 | Boundaries, hostile input, states nobody designed for |
| **Fixtures** | `tests/conftest.py` | — | Shared setup so no file re-invents "a student" |

Plus the existing standalone suites (auth 61, attendance 59, analytics 46, ML 144) which still pass.

### The fixture design decision

```
session scope : engine + schema    — built once, it's the slow part
function scope: all data           — wiped after every test
```

**Tests must not depend on each other.** If test A creates a student and test B counts students, B passes or fails based on alphabetical ordering — a bug class that's miserable to diagnose because the test that *fails* is rarely the one that's *wrong*.

---

## 2. Bugs Found and Fixed

### Bug 1 — Unguarded `int()` on the JWT subject → 500 instead of 401

```python
user_id = int(payload["sub"])   # ValueError on a non-numeric subject
```

A validly-**signed** token carrying `"sub": "not-a-number"` raised `ValueError`, which surfaced as a **500**.

**Why this matters beyond untidiness:** a 500 tells an attacker the token *parsed and passed signature verification*, and only the content was rejected. A 401 reveals nothing. Different status codes are an information leak.

**Fixed** in `auth_service.refresh_access_token` — now wrapped and raised as `AuthenticationError`.

### Bug 2 — Future-dated sessions accepted without validation

```
POST /sessions {"session_date": "2036-01-01"}  →  201 Created
```

Nothing validated `session_date`. A typo (2036 for 2026) created a session ten years out that would:
- never appear in any 30-day analytics window
- silently distort monthly aggregates
- be unremovable by faculty

**Fixed** with a bounded window: future dates rejected outright, backdating allowed up to 30 days. Backdating *is* legitimate — faculty forget to open sessions and record them later — but a class from last semester is almost always a typo.

### Bug 3 (latent) — Hardcoded dates rotting in the legacy fixtures

The fix for Bug 2 exposed this: `test_attendance.py` used `base_day = datetime(2026, 8, 1)` with a late joiner at +10 days. As the clock passed that date, the "late joiner" enrolment drifted into the **future**, and every enrolment-date assertion silently tested the wrong thing.

It took three rounds to fully anchor. **The lesson: never hardcode dates in fixtures.** Anchor to `date.today()` with offsets, or the test rots and starts lying to you.

---

## 3. Edge Cases Covered

### Malformed and hostile input

| Case | Expected | Result |
|------|----------|--------|
| SQL injection in email | 401/422, treated as data | ✅ ORM parameterises everything |
| SQL injection in path param | 422 | ✅ Path type coercion rejects |
| XSS payload in notes | Stored **verbatim** | ✅ See note below |
| Negative / enormous IDs | 403/404/422 | ✅ |
| Missing body, wrong content type | 422 | ✅ |
| Unknown extra fields | Ignored | ✅ Forward compatible |

**On XSS:** the API deliberately does **not** sanitise HTML. Escaping belongs at the render layer, and React escapes by default. Sanitising server-side would corrupt legitimate data containing angle brackets and give false confidence that the render layer is safe.

### Token handling

Six malformed `Authorization` header shapes, all → 401. Plus expired tokens, wrong-secret tokens, `alg: none` forgeries, type confusion (refresh presented as access), tokens for deleted users, and **immediate effect of deactivation** — the test that justifies `get_current_user` hitting the database rather than trusting the token.

### Session lifecycle

Future dates, backdating, zero-enrolment close, marking in cancelled sessions, double-close, reopen preserving manual marks while clearing auto-absences, marking after reopen.

### Duplicate prevention

- 20 rapid marks → exactly 1 record
- **The savepoint test**: after a duplicate, the *next* student still inserts successfully. Without `begin_nested()`, one duplicate poisons the whole transaction — so marking six faces from one frame would lose five.
- Same student, two sessions, same day → both allowed (lecture + lab)

### The maths that hides bugs

| Case | Wrong answer | Correct |
|------|-------------|---------|
| Monthly rate (1/2 and 9/10) | 70.0% (average of rates) | **83.3%** (summed totals) |
| Overall across subjects (1/1 and 3/10) | 65.0% (average) | **36.4%** (summed) |
| No sessions yet | 0% | **`None`** |
| All excused | 0% or crash | **`None`** |
| Exactly 75.0% | flagged | **not flagged** |
| Late joiner, pre-enrolment sessions | penalised | **excluded** |

The 75% boundary test matters more than it looks: an off-by-one there would wrongly bar every student sitting exactly on the line.

---

## 4. Improvements Recommended

Ordered by value per hour spent.

### High — worth doing before your demo

**1. Consolidate the two test suites.** You now have pytest (142 tests) *and* four standalone scripts (354 checks) covering overlapping ground. Duplicated suites drift until they disagree, and then you don't know which to believe. Migrate the ML scripts into pytest with `@pytest.mark.slow` and retire the rest.

**2. Fix the N+1 query problem in analytics.** `student_percentages()` calls `calculate_offering_attendance()` per student, each issuing 2 queries. For 40 students that's **80 queries**, and `subject_percentages()` multiplies it by the offering count. On the dashboard this is easily 400 queries per page load. It's fast enough on SQLite with test data and will not be on PostgreSQL with a real roster. Fix: one query fetching all records for the offering, then group in Python.

**3. Add a `conftest` fixture for time.** Several tests would be more robust with a frozen clock (`freezegun`). Bug 3 wouldn't have been possible.

### Medium

**4. Concurrent `close_session` is unprotected.** Two simultaneous closes both compute absentees and both bulk-insert → `IntegrityError` → 500. The status check narrows the window but doesn't close it. Fix: `SELECT ... FOR UPDATE` on the session row.

**5. Refresh token reuse detection.** Rotation is implemented, but using an already-rotated token should revoke the *entire family* as a breach signal, not just fail. Currently a stolen token that's used before the legitimate user's next refresh creates two valid chains.

**6. Timezone edge on enrolment dates.** `close_session` compares a UTC date against a local `session_date`. A student enrolling at 02:00 IST has a UTC date of the previous day, so they'd be counted eligible for a session they enrolled after. Low impact, real bug.

**7. Property-based testing for the percentage formula.** `hypothesis` would generate thousands of session/status combinations and assert invariants (`0 ≤ percentage ≤ 100`, `attended ≤ denominator`) rather than the specific cases I hand-picked.

### Lower

**8. Frontend tests.** Zero currently. Vitest + Testing Library for the `useRecentlyMarked` transition logic and the CSV escaping, which are the two places with real logic.

**9. Load testing.** The 2 fps claim in NFR-1.1 is an estimate, unverified. One Locust run produces a real number for your report.

**10. CI.** GitHub Actions running pytest on every push. Cheap, and it catches the date-rot class of bug automatically.

---

## 5. How to Run

```
cd backend
pip install pytest pytest-cov

pytest                          # everything
pytest tests/unit -q            # fast, no HTTP  (~24s)
pytest tests/api -q             # endpoint contract (~63s)
pytest tests/integration -q     # full flows (~20s)
pytest tests/test_edge_cases.py # the bug hunt (~39s)

pytest --cov=app --cov-report=term-missing
pytest -k "duplicate or threshold"    # run by name
pytest -x                             # stop at first failure
```

The four ML suites need the face model and run separately:

```
python tests/test_ml_01_preprocessing.py    # 30 checks
python tests/test_ml_02_detection.py        # 40
python tests/test_ml_03_embedding.py        # 30
python tests/test_ml_04_matching.py         # 44
```

---

## 6. Coverage

```
app/schemas/attendance.py               100%
app/models/enums.py                     100%
app/services/analytics_service.py        96%   ← the percentage formula
app/models/attendance.py                 93%
app/services/attendance_service.py       92%   ← the marking engine
app/services/auth_service.py             90%
app/services/chart_analytics_service.py  89%
app/ml/*                                 20-58%  (covered by the ML suites)
app/db/init_db.py                         0%    (a seed script, run manually)
─────────────────────────────────────────────
TOTAL                                    75%
```

**Coverage is a floor, not a goal.** 100% would prove every line executed, not that behaviour is correct. The number worth caring about is that the services holding business rules sit at 89–96%, because those are the ones that would silently corrupt attendance if wrong.

---

## 7. What's Still Untested

Being explicit, because unstated gaps are the dangerous kind:

- **Browser rendering.** Charts compile and data shapes are verified, but no canvas has actually been drawn in a browser here.
- **Camera capture.** `useCamera` and the frame loop are untested — they need a real browser and a real webcam.
- **Concurrency.** Every test is single-threaded. The savepoint behaviour is verified logically, not under genuine parallel load.
- **PostgreSQL-specific behaviour.** Tests run on SQLite. Partial indexes, `JSONB` operators, and true `SAVEPOINT` semantics are verified by inspecting generated DDL, not by execution.
- **Face enrolment.** Still no endpoint to test.

That last one remains the gap that matters. The CV pipeline works, the attendance engine works, the frontend works, and the test suite proves all three — but nothing connects a student's face to their record, so automatic marking cannot be demonstrated end to end.
