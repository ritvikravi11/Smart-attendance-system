# Module 1 — Authentication Foundation

**Status:** Complete — 61/61 integration checks passing
**Endpoints:** 6 · **Files:** 11 · **Layers exercised:** API → Service → Data

---

## 1. What This Module Does

You can now log in, stay logged in, log out, and change your password — with the security properties a real system needs rather than the ones a tutorial demonstrates.

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/v1/auth/login` | POST | Public | Email + password → token pair |
| `/api/v1/auth/refresh` | POST | Refresh token | New token pair, old one revoked |
| `/api/v1/auth/logout` | POST | Access token | Revoke this session |
| `/api/v1/auth/logout-all` | POST | Access token | Revoke every session |
| `/api/v1/auth/me` | GET | Access token | Current user profile |
| `/api/v1/auth/change-password` | POST | Access token | Change password, kill all sessions |
| `/health` | GET | Public | Liveness probe |

---

## 2. Architecture — Where Each File Sits

```
Request
   ↓
main.py                     app assembly, CORS, exception handlers
   ↓
api/v1/router.py            route aggregation
   ↓
api/v1/endpoints/auth.py    HTTP handling only — 6 thin functions
   ↓
api/deps.py                 get_current_user, RequireRole  ← the security gate
   ↓
services/auth_service.py    THE BUSINESS RULES
   ↓
core/security.py            bcrypt + JWT primitives
   ↓
models/                     database tables
```

**The rule to preserve:** dependencies point downward only. `auth_service.py` imports SQLAlchemy and `app.core` — never FastAPI. If you ever need `HTTPException` in a service, the layering has broken; raise a domain exception from `core/exceptions.py` instead and let `main.py` translate it.

---

## 3. Ten Security Decisions Worth Being Able to Defend

These are the parts a viva examiner will probe. Each is a real defence against a real attack.

### 3.1 Timing-attack defence on unknown emails
When no user matches, we still run a bcrypt verification against a dummy hash. Without it, an unknown email returns in ~1ms while a known email with a wrong password takes ~250ms. An attacker measures that difference and enumerates your entire user list — the vague error message alone doesn't help, because the *timing* leaks what the text hides.

### 3.2 Check order in `authenticate()`
Lockout is checked **before** the password; `is_active` is checked **after** it. Both are deliberate. Lockout-first means a locked account can't be probed at all. Active-last means an attacker can't distinguish "deactivated account" from "wrong password" without already knowing the password.

### 3.3 Identical error text for both failure modes
"Incorrect email or password" — never "no account with that email". The test suite asserts the two responses are byte-identical.

### 3.4 The `type` claim on every token
Access and refresh tokens are signed with the same secret, so structurally they're interchangeable. Without a type claim, a stolen refresh token could be presented as an access token for 7 days of API access instead of 30 minutes. Every `decode_token` call asserts the expected type.

### 3.5 `algorithms=` pinning blocks the `alg: none` attack
The classic JWT forgery: set the header algorithm to `none`, strip the signature, and a naive library accepts an attacker-authored token. Pinning the accepted algorithm list means *we* decide, not the token. The test suite constructs this exact forgery and confirms rejection.

### 3.6 Refresh token rotation
Each refresh revokes the old token and issues a new one. A stolen refresh token works exactly once — and the moment either party uses it, the other's copy dies. The legitimate user gets logged out unexpectedly, which is a *visible symptom* rather than a silent 7-day compromise.

### 3.7 Refresh tokens stored as SHA-256 hashes
A dumped `refresh_tokens` table would otherwise be immediately-usable sessions for every logged-in user. Note the different algorithm from passwords: bcrypt's slowness defeats brute force against *low-entropy* human-chosen inputs. A refresh token is high-entropy machine-generated material with no dictionary to try, so bcrypt buys nothing and would be paid on every refresh. Matching the algorithm to the threat is the actual skill.

### 3.8 `get_current_user` queries the database
It could read everything from the token and skip the query. It doesn't, because **deactivation must take effect immediately**. If a student is caught proxying attendance, a claims-only check would let them operate for up to 30 more minutes. For a system whose purpose is preventing attendance fraud, that's the wrong trade. Cost is one indexed primary-key lookup.

### 3.9 `UserResponse` omits `password_hash`
The schema is a separate object from the model precisely so the exposed surface is an explicit decision rather than an accident of table design. Three tests assert that `password_hash`, `failed_login_count`, and `locked_until` never appear in a response.

### 3.10 Password change requires the current password
The user is already authenticated — so why ask again? Because an unattended logged-in laptop is a real threat. Someone changes the password and owns the account permanently. Requiring the current password means possessing the session isn't sufficient.

---

## 4. Two Deviations from the Design Document

Both deliberate, both recorded rather than silently applied.

| Design doc said | We used | Why |
|-----------------|---------|-----|
| `python-jose` | **PyJWT** | python-jose's last release was 2021 and it's effectively unmaintained. Using a dead library for the component guarding every endpoint is a bad trade. |
| `passlib[bcrypt]` | **bcrypt directly** | passlib 1.7.4 reads `bcrypt.__about__.__version__`, which bcrypt 4.1+ removed — the combination raises AttributeError. passlib's last release was 2020. Calling bcrypt directly is ~15 lines and removes a dead dependency from the security path. |

A design document that quietly diverges from the code is worse than no document, so update your DESIGN.md decision table (D-22 area) to reflect these.

---

## 5. Three Real Bugs the Test Suite Caught

None of these would have appeared in code review. All three appeared within seconds of actually running the code.

### Bug 1 — Duplicate refresh tokens (would have 500'd in production)
JWT timestamps have **second** resolution. Two logins for the same user within the same second produced byte-identical payloads → identical tokens → identical SHA-256 hashes → `UNIQUE constraint failed: refresh_tokens.token_hash`.

**Fix:** added a random `jti` (JWT ID) claim. Every token is now unique regardless of timing. As a bonus, `jti` is the identifier a Redis denylist would key on if we later add instant access-token revocation.

**This is not a test artifact.** Log in twice quickly in production and the second request returns 500.

### Bug 2 — Naive vs aware datetime comparison
```
TypeError: can't compare offset-naive and offset-aware datetimes
```
We write timezone-aware UTC values, but what comes *back* depends on the driver — psycopg2 preserves tzinfo; other drivers strip it. This broke account lockout and token refresh with a 500 on code that looked perfectly correct.

**Fix:** an `_as_utc()` helper applied at the boundary where database values enter Python logic. Sprinkling try/except around each comparison would not have been the robust fix.

### Bug 3 — SQLite `:memory:` per-connection isolation (test infrastructure)
Each connection to `sqlite:///:memory:` gets its *own* empty database. The test seeded data on one connection while the app queried another. Fixed with `StaticPool`. Worth knowing if you write more tests.

---

## 6. How to Run It

### Prerequisites
PostgreSQL 16 running, `attendance_db` created, `.env` populated (see Module 0 / DATABASE.md).

### Step 1 — Install dependencies
```
cd backend
venv\Scripts\activate
pip install -r requirements.txt
```

### Step 2 — Generate a real secret key
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Paste into `.env` as `SECRET_KEY`. **Do not skip this** — it signs every token in your system.

### Step 3 — Apply migrations
```
alembic upgrade head
```

### Step 4 — Seed roles and admin
```
python -m app.db.init_db --demo
```
Creates the three roles, an admin, and demo data (one department, one faculty, one subject with three enrolled students). It's **idempotent** — safe to run repeatedly.

Credentials created:

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@college.edu` | `Admin@12345` |
| Faculty | `das@college.edu` | `Faculty@123` |
| Student | `ravi@college.edu` | `Student@123` |

### Step 5 — Start the server
```
uvicorn app.main:app --reload
```

`--reload` restarts on file changes. Development only — never in production.

You should see:
```
INFO  Starting AI Attendance Management System [development]
INFO  API documentation available at /docs
INFO  Uvicorn running on http://127.0.0.1:8000
```

---

## 7. How to Test It

### Option A — Interactive docs (start here)

Open **<http://localhost:8000/docs>**

This page is generated automatically from your code — it satisfies the "document each endpoint" requirement in your PRD for free.

1. Expand `POST /api/v1/auth/login` → **Try it out**
2. Body:
   ```json
   { "email": "admin@college.edu", "password": "Admin@12345" }
   ```
3. **Execute** → copy the `access_token` from the response
4. Click the green **Authorize** button (top right), paste the token, **Authorize**
5. Now try `GET /api/v1/auth/me` → returns your profile

**Confirm the security property yourself:** the response contains `email`, `full_name`, `role`, `is_active` — and no `password_hash`. That omission is the schema layer doing its job.

### Option B — Automated suite
```
python tests/test_auth.py
```
Expected: `RESULT: 61/61 checks passed`

Runs the real FastAPI app against in-memory SQLite. No mocking of auth logic — every test goes HTTP → router → dependency → service → database.

### Option C — Break it deliberately

These are the tests worth doing by hand, because seeing them work is more convincing than reading that they do.

**Wrong password:**
```
curl -X POST http://localhost:8000/api/v1/auth/login -H "Content-Type: application/json" -d "{\"email\":\"admin@college.edu\",\"password\":\"wrong\"}"
```
→ `401` with "Incorrect email or password."

**Unknown email** — same command with `nobody@college.edu` → **byte-identical response**. That's the enumeration defence.

**Trigger lockout:** run the wrong-password command 6 times → the 6th returns `423 Locked`. Then try the *correct* password → still `423`. Lockout precedes the password check.

**No token:**
```
curl http://localhost:8000/api/v1/auth/me
```
→ `401`

**Tampered token:** take a valid token, change the last 6 characters, retry → `401`.

**Refresh token as access token:** put your `refresh_token` in the Authorization header → `401`, "Expected a 'access' token but received 'refresh'". That's the type-confusion defence.

**Rotation:** call `/auth/refresh`, then call it *again with the same old token* → `401`. The old token died the moment it was used.

---

## 8. Files Delivered

```
backend/
├── app/
│   ├── main.py                          App assembly, CORS, exception handlers
│   ├── core/
│   │   ├── config.py                    Typed settings (updated)
│   │   ├── security.py                  bcrypt + JWT primitives
│   │   └── exceptions.py                Domain exception hierarchy
│   ├── api/
│   │   ├── deps.py                      get_current_user, RequireRole
│   │   └── v1/
│   │       ├── router.py                Route aggregation
│   │       └── endpoints/auth.py        6 endpoints
│   ├── schemas/auth.py                  Request/response contracts
│   ├── services/auth_service.py         Business logic
│   └── db/init_db.py                    Idempotent seed script
├── tests/test_auth.py                   61-check integration suite
├── requirements.txt
└── .env.example
```

---

## 9. Definition of Done

- [ ] `pip install -r requirements.txt` completes
- [ ] Real `SECRET_KEY` generated and in `.env`
- [ ] `alembic upgrade head` applied
- [ ] `python -m app.db.init_db --demo` ran without error
- [ ] Server starts on `http://localhost:8000`
- [ ] `/docs` loads and shows 6 auth endpoints
- [ ] Login via Swagger returns a token pair
- [ ] `/auth/me` returns your profile with **no** `password_hash`
- [ ] `python tests/test_auth.py` reports 61/61
- [ ] Lockout verified manually (6 bad attempts → 423)
- [ ] Committed: `feat(auth): JWT authentication with RBAC [6.1-6.4]`

---

## 10. What's Next

Module 1 gives you authentication. It does **not** yet give you anything to authorise — `RequireRole` is written and tested but no endpoint uses it, because there are no protected resources yet.

**Module 2 — Student & Master Data APIs** is the natural next step: student CRUD, departments, subjects, offerings, enrolments. That's where `require_admin` and the faculty ownership check finally get exercised, and it's what face enrolment (Module 3) needs to attach embeddings to.

Alternatively, per the reordered roadmap, we jump to **face detection** and get a webcam window drawing bounding boxes — the risky, unfamiliar part, tackled while there's still time to adapt if InsightFace fights you on Windows.

Say the word.
