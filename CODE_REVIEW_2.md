# Code Review — Second Pass

**Note:** the first review pass is in `CODE_REVIEW.md` (8 issues, all fixed). Rather than repeat it, this pass covers the areas it didn't reach: **deployment readiness, the ML layer's threading, and the frontend in depth.**

**Result:** 6 further issues found and fixed · 152 pytest + 210 standalone checks passing · clean build

---

## Summary

| # | Issue | Severity | Found by |
|---|-------|----------|----------|
| 1 | **No Alembic environment** — docs told users to run a command that didn't exist | 🔴 Critical | Checking the filesystem |
| 2 | **Generated migration crashes with `NameError`** | 🔴 Critical | Actually running it |
| 3 | No React error boundary — one null blanks the whole app | 🔴 High | Grep |
| 4 | `FaceEngine` non-reentrant lock — latent deadlock | 🟠 Medium | Reproduced in a harness |
| 5 | Modal unusable by keyboard | 🟠 Medium | Accessibility audit |
| 6 | Capture loop leaks a timer past unmount | 🟡 Low | Reading the cleanup path |

Plus one caught by my own verification mid-fix: `Card` didn't forward refs, so the focus trap I'd just written would have silently done nothing.

---

## 1 & 2. The Deployment Blockers

**Every module doc since the database told you to run:**

```
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

**There was no `alembic/` directory and no `alembic.ini`.** The instruction had been repeated across five documents without anyone executing it. This is the most embarrassing class of bug: documentation that describes a system that doesn't exist.

I created the environment — and then, instead of assuming it worked, ran it. It failed:

```
sa.Column('details', postgresql.JSONB(astext_type=Text()), ...)
                                                  ^^^^
NameError: name 'Text' is not defined
```

Alembic's autogenerate emits `Text()` unqualified, but the generated file only imports `sqlalchemy as sa`. **The migration dies at import time, before any DDL runs** — so the failure lands on your very first `alembic upgrade head`, on a fresh deployment, usually under time pressure.

Fixed with a `render_item` hook in `env.py` that renders dialect types with the correct prefix.

**Then I validated it properly.** SQLite can't render `JSONB` at all, so I generated offline SQL against the PostgreSQL dialect (no server needed):

```
CREATE TABLE statements: 15
CREATE INDEX statements: 33
CONSTRAINT ck_embedding_byte_length CHECK (octet_length(embedding) = 2048)
CONSTRAINT uq_attendance_session_student UNIQUE (session_id, student_id)
```

The schema, the byte-length check, and the constraint the whole system depends on all survive the round trip.

`env.py` also enables `compare_type` and `compare_server_default` — off by default, which means Alembic silently misses column type changes and DEFAULT-clause changes. Given this project shipped the `server_default="true"` bug **three times**, that second flag earns its place.

---

## 3. No React Error Boundary

React's most unforgiving default: an uncaught render error unmounts the **entire tree**. Not the broken card — everything. White page, no message, no way back.

This isn't hypothetical here. `summary.class_average` is null before any class is held. One `.toFixed()` on a null anywhere in the tree is a **total outage from a one-character bug**.

Fixed with boundaries at **two levels**, which is the part worth understanding:

- **Outer**, wrapping the providers — last resort, catches failures in `AuthProvider` itself.
- **Inner**, inside `AppShell` around `<Outlet/>` — if a *page* crashes, the sidebar and topbar survive, so the user can navigate somewhere that works instead of being stranded.

The inner boundary is keyed on `location.pathname`, so navigating away clears the error state. Without that, one crash leaves the error screen showing forever.

The fallback shows a stack trace **in development only** — same reasoning as the backend's generic 500 handler. A trace discloses file paths and library versions to anyone who can trigger the error.

This is also the one place a class component is still mandatory in modern React (`componentDidCatch` has no hook equivalent), which is precisely why teams forget it.

---

## 4. Latent Deadlock in `FaceEngine`

`instance()` and `load()` share one class-level `threading.Lock()`. A plain `Lock` is **not reentrant**. I reproduced the shape in a scratch harness — it hangs forever, with no error and no timeout.

It doesn't fire today because `instance()` doesn't call `load()`. It fires the moment someone makes `instance()` auto-load, which is an obvious convenience refactor.

Fixed: `threading.RLock()`. Costs nothing, removes the trap. The matcher already used `RLock` for exactly this reason — the inconsistency between the two was the tell.

---

## 5. The Modal Was a Keyboard Dead End

`MarkSheet` was a plain div with a click-to-dismiss backdrop:

- No `role="dialog"` → screen readers announced it as ordinary page content
- No Escape handler → keyboard users couldn't close it
- No focus trap → Tab walked through the page *underneath* the overlay
- No focus restoration → closing dumped focus at the top of a 40-tile grid

A modal that works for mouse users but not keyboard users isn't "mostly accessible" — it's a dead end for anyone who can't click.

Fixed with `role="dialog"`, `aria-modal`, `aria-labelledby`, an Escape handler, a Tab cycle within the panel, and focus restoration on close.

**And then my own check caught the follow-on bug:** `Card` was a plain function component, so it *silently dropped* the ref the focus trap needed. React warns in development and says nothing in production — the trap would have appeared to work while doing nothing. `Card` now uses `forwardRef`.

The general rule: any primitive that might be measured, focused, or scrolled needs to forward its ref.

---

## 6. Capture Loop Leaked Past Unmount

```js
setTimeout(captureLoop, wait);   // id never stored
```

The `runningRef` guard stopped the *next* iteration, but the already-scheduled callback still fired — calling `setState` on a dead component and issuing **one more frame upload for a session the user had left**.

Fixed by storing the timer id and clearing it in both `stopCamera` and the unmount cleanup.

---

## Bonus: A Code Smell With Teeth

`RecognitionService` passed plain dicts carrying a private `"_match"` key holding a `MatchResult`, popped before serialisation. Two problems:

1. **Fragile.** Forget the pop in one path → FastAPI tries to serialise a `MatchResult` → 500 on an endpoint whose logic succeeded.
2. **Invisible contract.** Nothing declared which keys exist, so `entry["studnet_id"]` is a silent `None`, not an error.

Replaced with a `FaceOutcome` dataclass where `to_dict()` is the **only** way out. The internal field is now structurally excluded rather than manually stripped — it cannot be forgotten.

---

## What I'd Still Flag, Unfixed

- **`localStorage` tokens** — accepted risk, documented. Would not fly at a bank.
- **No rate limiting** — lockout stops per-account brute force, not distributed credential stuffing.
- **No structured logging or request IDs** — correlating a user report to a log line is currently manual.
- **No CI** — every bug in both review passes would have been caught automatically by `pytest` on push.
- **Frontend has zero tests** — `useRecentlyMarked` and the CSV escaping are the two places with real logic worth testing.

---

## Verification

```
pytest                                  # 152 passed
python checks/test_auth.py              # 61/61
python checks/test_attendance.py        # 59/59
python checks/test_analytics.py         # 46/46
python checks/test_ml_04_matching.py    # 44/44
alembic upgrade head --sql              # 15 tables, 33 indexes, valid PostgreSQL
cd frontend && npm run build            # clean, no warnings
```

---

## The Reviewer's Note

The first pass found performance and configuration problems. This one found **things that were never executed** — a migration environment that existed only in documentation, an error path with no handler, a lock that would deadlock on the next refactor.

The pattern across both passes: **the bugs were in what nobody ran.** The N+1 was invisible until measured. The migration was broken because nobody had migrated. The error boundary was missing because nothing had crashed yet.

That is the argument for CI, and it's the single highest-value thing left on the list. Everything found in both reviews would have been caught by a machine running the suite on every push — including the migration, if the pipeline runs `alembic upgrade head` against a throwaway PostgreSQL container.

And still unchanged since Module 4: **face enrolment has no endpoint.** Two review passes, 362 passing checks, a validated migration, and the loop still doesn't close.
