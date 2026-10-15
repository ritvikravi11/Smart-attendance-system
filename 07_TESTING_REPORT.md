# Testing Report
## AI-Powered Attendance Management System

**Version 1.0 · August 2026**

All figures in this document are measured output, not estimates. Every number can be reproduced by running the stated command.

---

## 1. Summary

| Suite | Checks | Result |
|-------|--------|--------|
| `validate_schema.py` | 30 | ✅ 30/30 |
| `test_auth.py` | 61 | ✅ 61/61 |
| `test_attendance.py` | 59 | ✅ 59/59 |
| `test_analytics.py` | 46 | ✅ 46/46 |
| `test_ml_01_preprocessing.py` | 30 | ✅ 30/30 |
| `test_ml_02_detection.py` | 40 | ✅ 40/40 |
| `test_ml_03_embedding.py` | 30 | ✅ 30/30 |
| `test_ml_04_matching.py` | 44 | ✅ 44/44 |
| **Total (authored suites)** | **340** | **✅ 340/340** |

### A disclosure about additional test files

The `backend/tests/` directory also contains `conftest.py`, `test_edge_cases.py`, and `tests/unit/`, `tests/api/`, `tests/integration/`. These are pytest-based suites totalling **142 tests, all passing**:

```
python -m pytest tests/unit tests/api tests/integration tests/test_edge_cases.py -q
142 passed
```

**These files were not authored as part of the documented module-by-module build**, so they are reported separately rather than folded into the total above. They pass and appear consistent with the codebase, but you should review them before claiming them as your own work. If they are not yours, delete them — an inflated test count that you cannot explain in a viva is worse than an honest smaller one.

---

## 2. Test Strategy

### 2.1 Levels

| Level | What it exercises | Example |
|-------|-------------------|---------|
| **Schema** | Constraints enforced by PostgreSQL itself | Duplicate attendance rejected by the database |
| **Unit** | Pure functions, no I/O | Cosine similarity, blur scoring, threshold bands |
| **Integration** | Service + database | Session close generating absentees |
| **API** | Full stack through HTTP | 403 on cross-faculty access |
| **ML** | Real model, real images | Alignment impact on similarity |

### 2.2 Approach

Tests run the **real application** against an in-memory SQLite database via FastAPI's `TestClient`. Authentication, authorisation, and business logic are **not mocked** — every API test travels HTTP → router → dependency → service → database.

The ML suites use the **real InsightFace model** on **real face images** bundled with the library. Nothing about recognition is simulated.

### 2.3 Why SQLite for tests, PostgreSQL in production

SQLite enforces UNIQUE, CHECK, and FOREIGN KEY constraints, which covers every rule the suites verify, and runs in memory with no setup. Two dialect differences are bridged in the harness (boolean defaults and `now()`), and PostgreSQL-only features — partial indexes, JSONB — are verified separately by inspecting the generated DDL.

This is a documented trade-off. A production-grade suite would run against PostgreSQL in CI.

---

## 3. Results by Area

### 3.1 Database schema — 30/30

| Group | Checks |
|-------|--------|
| Relationship integrity | 5 |
| Uniqueness constraints | 4 |
| CHECK constraints | 5 |
| **Duplicate prevention** | **5** |
| Foreign key enforcement | 2 |
| Embedding serialisation | 6 |
| Cosine similarity properties | 3 |

**The duplicate-prevention test** simulates a camera at 2 fps hammering the endpoint with one student for ten seconds:

```
[PASS] First mark succeeds
[PASS] 20 duplicate frames all blocked — 20/20 raised IntegrityError
[PASS] Still exactly 1 record for this student
[PASS] Different student in same session allowed
[PASS] Same student, second session same day allowed — lecture + lab on one day
```

That last check matters: a naive `UNIQUE (student_id, session_date)` would have blocked it, breaking any subject with a lecture and a lab on the same day.

### 3.2 Authentication — 61/61

| Group | Checks | Notable |
|-------|--------|---------|
| Password hashing | 6 | Salting verified: identical passwords produce different hashes |
| JWT mechanics | 7 | **`alg=none` forgery rejected** |
| Login endpoint | 12 | **Unknown email and wrong password return byte-identical responses** |
| Account lockout | 4 | 6th attempt returns 423; correct password still blocked while locked |
| Protected endpoints | 12 | **`password_hash` never appears in any response** |
| Refresh and rotation | 7 | Old refresh token dies on use |
| Logout | 3 | Idempotent |
| Password change | 7 | All sessions revoked |
| System | 3 | |

**Three security properties worth highlighting:**

```
[PASS] alg=none forgery rejected — algorithms= pinning works
[PASS] Unknown email gives IDENTICAL message to wrong password
[PASS] Deactivation blocks an UNEXPIRED token immediately
```

The third is why `get_current_user` queries the database rather than trusting token claims alone. A student caught proxying attendance is blocked instantly, not in 30 minutes.

### 3.3 Attendance engine — 59/59

| Group | Checks | Notable |
|-------|--------|---------|
| Session lifecycle and authorisation | 7 | Faculty cannot open a session for another's offering |
| Manual marking and roster | 6 | Unmarked students appear with `status: null` |
| **Duplicate prevention** | **6** | **Transaction survives the IntegrityError (SAVEPOINT)** |
| Session close and derived absence | 7 | Set difference verified |
| Enrolment-date protection | 1 | Late joiner not marked absent for earlier sessions |
| **Percentage arithmetic** | **6** | Verified by hand |
| Subject and faculty views | 9 | Student cannot view another student's record |
| Defaulters and shortfall | 4 | Recovery arithmetic verified |
| Cancel, reopen, correction | 8 | Reopen preserves manual marks |
| Graceful degradation | 5 | Undecodable frame returns 400, not 500 |

**The percentage test, verified by hand:**

```
eligible closed sessions: 6      (cancelled session correctly excluded)
present=4  late=0  absent=1  excused=1
attended    = 4                  (present + late)
denominator = 6 − 1 = 5          (excused removed from denominator)
percentage  = 4/5 = 80.0%
```

**The shortfall test:** a student at 0% with 4 sessions needs 12 consecutive classes → (0+12)/(4+12) = **exactly 75.0%**.

### 3.4 Analytics — 46/46

**The most valuable test in the suite** constructs Simpson's paradox deliberately:

```
Month A holds two sessions: 1 of 2 present, and 9 of 10 present

  average of daily rates : (50 + 90) / 2      = 70.0%   ← WRONG
  summed totals          : (1 + 9) / (2 + 10) = 83.3%   ← correct
  our code returns       :                      83.3%
```

**A 13-point error**, and nothing about the wrong version would look broken. Averaging percentages weights a 5-student session equally with a 40-student one.

The suite also cross-checks that the chart service and the core percentage service agree, so a chart can never contradict the number printed beside it.

```
[PASS] Chart figures match the core percentage service — both report 100.0%
[PASS] Empty class average is None, not 0 — 0% would say everyone is failing on day one
[PASS] The two rankings genuinely differ — absences vs percentage
```

### 3.5 Computer vision — 144/144

| Suite | Checks | Covers |
|-------|--------|--------|
| Preprocessing | 30 | CLAHE, resize, blur, brightness, pose |
| Detection | 40 | Model lifecycle, SCRFD, landmarks, quality gate |
| Embedding | 30 | Alignment, embeddings, averaging, consistency |
| Matching | 44 | Index, thresholds, unknown detection |

---

## 4. Measured Performance

All figures from the ML suites on CPU.

| Operation | Measured | Requirement |
|-----------|----------|-------------|
| Preprocessing (CLAHE + resize) | **5 ms** | — |
| Detection, 6-face group photo | **257 ms** | ≤ 500 ms |
| Embedding, per face | **~285 ms** | — |
| **End-to-end, single face** | **~540 ms** | ≤ 2,000 ms (NFR-1.1) ✅ |
| **Index search, 1,000 students** | **157 µs** | ≤ 1 ms ✅ |
| Index search, 50-student roster | 56 µs | — |
| Model load at startup | 5.5 s | Once per process |

**The search figure is the striking one.** Searching a thousand students takes 157 microseconds because it is one BLAS matrix multiply. A naive Python loop over identical data took **1,162 µs — 7× slower**.

---

## 5. Accuracy Results

### 5.1 Alignment impact

The measurement that justifies the most-skipped stage of the pipeline. Same person, head rotated 18°:

| Approach | Cosine similarity |
|----------|-------------------|
| **With alignment** | **0.9804** |
| Without alignment (naive resize) | 0.2529 |
| **Difference** | **+0.7275** |

**Without alignment that person scores below the 0.50 threshold** — they would be marked Unknown. The failure is not a crash; it is silent, systematic mediocrity.

### 5.2 Genuine versus impostor separation

Three enrolled students, three unenrolled people, real photographs:

```
GENUINE  matches:  1.0000
IMPOSTOR matches: -0.0142 to 0.2133
SEPARATION GAP:    0.7867
```

Thresholds at 0.40 / 0.50 sit comfortably inside the gap. **Every unenrolled face was correctly flagged Unknown.**

### 5.3 Colour-space fairness

| Method | Mean hue shift |
|--------|----------------|
| **CLAHE on LAB L-channel** | **3.98°** |
| Naive per-RGB equalisation | 43.46° |

An **11× difference**. Per-channel equalisation stretches B, G, and R by different amounts, so hue shifts — and because the distortion depends on the original skin tone, it is not uniform across people.

### 5.4 Threshold calibration

The calibration tool produces FAR/FRR curves, an EER figure, and a publication-quality plot. On a test dataset it correctly detected that the data was too easy and refused to recommend a dangerously low threshold:

```
GENUINE   mean 0.9587  std 0.0248
IMPOSTOR  mean 0.0255  std 0.0634
EER: 0.00% at threshold 0.190

WARNING: YOUR GENUINE PAIRS LOOK TOO EASY
  Recommendation floored at 0.35 for safety.
```

**This is a limitation, not a result.** See §7.2.

---

## 6. Defects Found and Fixed

Seven genuine defects were caught by testing. All would have shipped otherwise; none would have appeared in code review.

### D-1 — Detection ran recognition it did not need (9× slowdown)
`FaceAnalysis.get()` runs every loaded model over every detected face, so drawing bounding boxes was silently computing six ArcFace embeddings per frame.
**Before 2,337 ms → after 257 ms.** This took the pipeline from outside the 2 fps budget to comfortably inside it.

### D-2 — String booleans in `server_default` (three occurrences)
`server_default="true"` renders as `DEFAULT 'true'` — a **string literal**. PostgreSQL casts it; SQLite does not, so `WHERE is_active IS true` matched nothing. Every enrolment lookup returned empty.
Found three times: `section='A'`, `status='open'`, and 9 boolean columns.
**Root cause:** `server_default` takes a literal value or a SQL expression, never a pre-quoted string.

### D-3 — Duplicate refresh tokens (production 500)
JWT timestamps have **second** resolution, so two logins in the same second produced byte-identical tokens → identical SHA-256 hashes → `UNIQUE constraint failed`. Log in twice quickly and the second request returns 500.
**Fix:** a random `jti` claim.

### D-4 — Naive vs aware datetime comparison
`TypeError: can't compare offset-naive and offset-aware datetimes`. We write timezone-aware UTC, but what comes back depends on the driver. This broke account lockout and token refresh with a 500 on code that read perfectly.

### D-5 — Cyrillic homoglyph in an import
```
import { CircleСheckBig, ... }
                ↑ U+0421 CYRILLIC CAPITAL ES
```
Visually identical to Latin C. Would have resolved to `undefined` at runtime with no error.

### D-6 — 620 kB initial bundle
The login page was downloading a charting library it never renders. Lazy-loading brought it to **257 kB**.

### D-7 — Flaky timing assertion
A 300 ms threshold measuring 301–308 ms failed roughly half the time — reporting a regression that was not one. Raised to 500 ms, against the real 2,000 ms requirement. **Verified stable across three consecutive runs.**

---

## 7. Untested and Unverified

An honest testing report states what was *not* tested.

### 7.1 Face enrolment end to end
**FR-2.3 is unimplemented.** The pipeline, schema, and averaging logic are tested individually, but no endpoint or UI captures images. The embedding index is empty, so automatic marking cannot be demonstrated.

### 7.2 Threshold calibration on real data
The calibration tool works, but has only been run on synthetic variations of a single capture session. Real genuine pairs — different days, lighting, glasses on and off — score **0.55–0.80**, not the 0.95+ seen in that dataset. **Calibration on multi-session data is required before deployment.**

### 7.3 Demographic accuracy
Not evaluated across skin tone, gender, or age. Face recognition is documented to vary along these axes (NIST FRVT Part 3). The LAB-channel CLAHE mitigates one mechanism; it does not address underlying model behaviour.

### 7.4 Load and concurrency
NFR-1.2 is unverified. All performance figures are single-request measurements.

### 7.5 Browser rendering
Frontend code compiles and the data layer is tested, but no browser-based rendering or end-to-end UI tests were run.

### 7.6 Anti-spoofing
Not implemented, therefore not tested. A photograph would be accepted.

---

## 8. Reproducing These Results

```
cd backend
python scripts/validate_schema.py
python tests/test_auth.py
python tests/test_attendance.py
python tests/test_analytics.py
python tests/test_ml_01_preprocessing.py
python tests/test_ml_02_detection.py
python tests/test_ml_03_embedding.py
python tests/test_ml_04_matching.py
```

Each prints a per-check breakdown and a final `RESULT: n/n`.

**Threshold calibration**, once you have multi-session photographs organised one folder per person:

```
pip install matplotlib
python scripts/ml_03_calibrate_threshold.py calibration_data/
```

---

## 9. Recommendations Before Deployment

| Priority | Action |
|----------|--------|
| 🔴 **Critical** | Implement face enrolment (FR-2.3) |
| 🔴 **Critical** | Calibrate the threshold on multi-session data |
| 🔴 High | Measure accuracy across your actual class demographics and publish the result |
| 🟠 Medium | Run the suites against PostgreSQL in CI, not only SQLite |
| 🟠 Medium | Load-test with concurrent sessions |
| 🟠 Medium | Add browser-based end-to-end tests |
| 🟡 Low | Implement passive liveness detection |

---

*End of Testing Report v1.0*
