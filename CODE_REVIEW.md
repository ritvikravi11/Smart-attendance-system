# Code Review — Senior Engineering Pass

**Scope:** full project · backend, frontend, tests, config
**Method:** measured, not read. Every claim below has a number behind it.
**Result:** 8 issues found and fixed · 152 pytest + 210 standalone checks passing

---

## Summary

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | N+1 queries in analytics — **368 queries per dashboard** | 🔴 High | ✅ Fixed |
| 2 | Full page reload on "Take attendance" | 🔴 High | ✅ Fixed |
| 3 | `SECRET_KEY` length unvalidated | 🟠 Medium | ✅ Fixed |
| 4 | CORS origins hardcoded in application code | 🟠 Medium | ✅ Fixed |
| 5 | `close_session` race → 500 under concurrency | 🟠 Medium | ✅ Fixed |
| 6 | Three tables growing without bound | 🟠 Medium | ✅ Fixed |
| 7 | `_as_utc` duplicated across two services | 🟡 Smell | ✅ Fixed |
| 8 | Two test suites, one breaking the other's collection | 🟡 Smell | ✅ Fixed |

---

## 1. N+1 Queries — the headline finding

I claimed this last module as an estimate. Measured, it was worse.

**Setup:** 3 offerings, 40 students, 30 sessions each — one realistic semester.

| Call | Before | After | |
|------|--------|-------|---|
| `student_percentages` | 122 queries | **5** | |
| `offering_dashboard` | 247 queries | **13** | |
| `subject_percentages` | 364 queries | **13** | |
| `faculty_summary` | **368 queries** | **17** | **22× fewer** |

**Why it looked fine.** On in-memory SQLite, 368 queries took 119 ms. On PostgreSQL each query carries a network round trip of roughly a millisecond, so the same page becomes ~400 ms of pure latency — and it grows **linearly** with students × offerings. A department with 8 offerings of 60 students would issue about 1,000 queries per dashboard load.

**The cause.** `calculate_offering_attendance()` is correct and well-tested, but answers a question about *one* student. The analytics layer needed every student and called it in a loop.

**The fix.** `app/services/attendance_calculator.py` — three queries regardless of class size: enrolments, sessions, records. Then group in Python.

**Why not one `GROUP BY`.** The enrolment-date rule ("count only sessions on or after this student joined") makes the eligible session set *different per student*. Expressing that in SQL needs a correlated subquery or lateral join — correct, but much harder to verify against the hand-checked tests. Three queries is already O(1) in student count; the remaining win is marginal and the readability cost is not.

**The risk this creates, and how it's contained.** There are now two implementations of one formula. `tests/test_refactor.py` asserts they produce *identical* output on every student across eight scenarios, plus a test that fails if the query count ever grows with student count. Without that tripwire, someone edits one and the dashboard silently disagrees with the student's own attendance page.

---

## 2. Full Page Reload on Navigation

```jsx
<Button as={Link} onClick={() => { window.location.href = `/attendance?...` }}>
```

`Button` had no `as` prop. React spread `as={Link}` onto the `<button>` as an unknown DOM attribute, and the fallback `window.location.href` triggered a **full page reload** — bundle re-download, React remount, auth context re-fetching the user.

**Fixed** by making `Button` polymorphic. One component now covers buttons and links without duplicating styling, and links keep native right-click / middle-click / open-in-new-tab behaviour.

The subtlety worth noting: `disabled` and `type` are only valid on a real `<button>`. On an `<a>` they produce invalid HTML, so the component now branches — links get `aria-disabled` and `tabIndex={-1}` instead.

---

## 3. `SECRET_KEY` Length Unvalidated

Production config rejected only the literal default string, so `SECRET_KEY=abc` passed. HMAC-SHA256 keys under 32 bytes weaken the signature (RFC 7518 §3.2).

**The tell was in our own test output** — PyJWT emits `InsecureKeyLengthWarning`, and it had been scrolling past unnoticed for several modules. Warnings you've learned to ignore are warnings that stop working.

**Fixed:** production startup now refuses to boot with a short key, and names the command to generate a real one.

---

## 4. CORS Origins Hardcoded

The allowed-origin list lived in `main.py`, so deploying to a real domain required a **code change**. That's the kind of friction someone eventually resolves by setting `allow_origins=["*"]` — which, combined with `allow_credentials=True`, invites any website to make authenticated requests using a logged-in user's browser.

**Fixed:** driven by `CORS_ORIGINS` in `.env`, with production startup rejecting `*` outright.

---

## 5. `close_session` Concurrency Race

Two simultaneous close requests both passed the status check, both computed the absentee set, and both bulk-inserted. The second hit the `UNIQUE` constraint and returned a **500**.

**Fixed** with `SELECT ... FOR UPDATE`. The second request now blocks until the first commits, then sees `status='closed'` and gets a clean 409. (No-op on SQLite, which serialises writes anyway, so tests are unaffected.)

Also replaced `bulk_save_objects` — legacy in SQLAlchemy 2.0, bypasses ORM event hooks — with a Core `insert()` taking a list of dicts. Same single round trip, supported API, correct default handling.

---

## 6. Unbounded Table Growth

| Table | Growth rate | Projected |
|-------|-------------|-----------|
| `recognition_logs` | ~2 rows/sec per camera | **~23 million rows** per 90-day term with 6 cameras |
| `refresh_tokens` | 1 per login, forever | Slows the lookup that runs on every refresh |
| `audit_logs` | 1 per state change | Worth keeping longer, not forever |

The design document flagged `recognition_logs` retention as a known issue, and then nothing implemented it. **That's the most common way a known issue becomes an outage** — writing it down feels like handling it.

**Fixed:** `scripts/purge_old_data.py`, with `--dry-run`. Deletes are batched at 10,000 rows with a commit between batches, because a single `DELETE` over 20 million rows holds a long lock that blocks attendance marking.

A cron job rather than an in-process scheduler: four Uvicorn workers would otherwise run four concurrent deletes.

---

## 7. Duplicated `_as_utc`

Defined identically in `auth_service.py` and `attendance_service.py`. Two copies of one function is a bug waiting for someone to fix one and not the other — and the failure mode is subtle: a naive/aware comparison raises `TypeError` at runtime on a path that reads correctly.

**Fixed:** extracted to `app/utils/datetime_utils.py`. Also removed a magic string (`"Auto-marked absent at session close"`) that appeared in two places — if those ever drifted, reopening a session would silently destroy real faculty data.

The duplication wasn't laziness; each service hit the same problem independently. That's exactly when extraction is warranted — on the *second* occurrence, not the first.

---

## 8. Two Test Suites, One Breaking the Other

The standalone scripts call `sys.exit()` at module level, which **broke pytest collection entirely** once they sat under `tests/`. The duplication I flagged last module had gone from theoretical debt to actively preventing the suite from running.

**Fixed:** moved to `checks/`, with a README stating which are still authoritative (the ML ones — they load the real model, which pytest doesn't) and which are superseded.

---

## Issues Found and *Not* Fixed

Being explicit, because silent omissions are the dangerous kind.

**Tokens in `localStorage` are readable by any XSS.** The alternative — `httpOnly` cookies — is genuinely more secure but requires CSRF protection, complicates the dev proxy, and needs same-site cookie handling. For a college-network deployment where the frontend is the only consumer, the trade is defensible. **It would not be at a bank.** Document it as an accepted risk rather than pretending it isn't one.

**No rate limiting.** Account lockout blocks per-account brute force but not distributed credential stuffing across many accounts. `slowapi` is roughly ten lines.

**`get_current_user` queries on every request.** Deliberate — deactivation must take effect immediately in a system whose purpose is preventing attendance fraud. Cost is one indexed primary-key lookup. Worth revisiting only if that lookup ever shows up in a profile.

**Timezone edge on enrolment dates.** `close_session` compares a UTC date against a local `session_date`. A student enrolling at 02:00 IST has a UTC date of the previous day. Real, low impact, needs an institution-timezone setting to fix properly.

**Refresh token reuse doesn't revoke the family.** Rotation works, but replaying an already-rotated token should be treated as a breach signal and kill the whole chain.

---

## Scalability Assessment

| Component | Ceiling | First bottleneck | Fix when reached |
|-----------|---------|------------------|------------------|
| Analytics queries | ~50k students | Python grouping | Push to SQL with `date_trunc` |
| Embedding search | ~50k students | Linear scan >50 ms | pgvector + HNSW |
| Face inference | ~6 faces/sec/worker | CPU-bound | GPU node or more ML replicas |
| API throughput | ~50 concurrent | Uvicorn workers | Horizontal scale (stateless by design) |
| `recognition_logs` | ~23M rows/term | Disk, then query planning | ✅ Retention now implemented |
| Embedding index | Per-worker copy | Stale after enrolment | Redis pub/sub invalidation |

**The honest headline:** at the stated target of 1,000 students, everything has substantial headroom *after* the N+1 fix. Before it, the analytics dashboard would have been the first thing to fall over — at maybe 200 students, not 50,000.

**The first thing that actually breaks under growth** is still CPU inference throughput. A 500-student assembly hall would take 80+ seconds at ~6 faces/second.

---

## Verification

```
pytest                                  # 152 passed
python checks/test_auth.py              # 61/61
python checks/test_attendance.py        # 59/59
python checks/test_analytics.py         # 46/46
python checks/test_ml_04_matching.py    # 44/44
cd frontend && npm run build            # clean, no warnings
```

Every refactor is covered by a test that would fail if it regressed — the equivalence tests for the batch calculator, the query-count assertion for the N+1, and the config validation test for the secret key.

---

## The One Thing I'd Say in a Real Review

The architecture holds up. Layering is strict, business rules are isolated and tested, and the database enforces its own invariants rather than trusting application code. That's better than most production systems I've reviewed at this size.

The issues found were **operational maturity**, not design: things that work at demo scale and fail at deployment scale. That's the expected shape of debt for a three-week build, and the fact that the N+1 was invisible until measured is the lesson worth carrying forward — **profile before you optimise, but also profile before you claim it's fine.**

Still outstanding, and unchanged since Module 4: **face enrolment has no endpoint.** Everything is tested and reviewed, but nothing connects a student's face to their record.
