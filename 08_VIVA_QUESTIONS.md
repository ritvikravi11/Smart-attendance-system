# Viva Preparation
## AI-Powered Attendance Management System

Questions an examiner is likely to ask, with answers grounded in what you actually built. Where a number appears, it is measured — you can reproduce it live if challenged.

**The single most useful piece of advice:** examiners reward students who can name a flaw in their own work far more than students who claim perfection. Section 8 exists for that.

---

## 1. Project Overview

### Q: Explain your project in two minutes.

A web application that replaces the paper attendance register. A lecturer opens a session, points a webcam at the room, and enrolled students are recognised and marked present automatically. Faculty keep override control on every record.

The stack is FastAPI and PostgreSQL on the backend, React with Tailwind on the frontend, and InsightFace for computer vision — SCRFD for detection and ArcFace for recognition.

The core technical problem is that recognition is imperfect and the camera streams continuously, so the system must handle uncertain matches and repeated observations of the same student without corrupting the record.

### Q: Why is this an image processing project rather than a database project?

Because the pipeline stages are classical image processing techniques, each solving a measurable failure:

- **CLAHE on the LAB luminance channel** for mixed classroom lighting
- **Variance of the Laplacian** for blur rejection
- **Similarity transform alignment** using five facial landmarks
- **Cosine similarity in a 512-dimension embedding space**

The database stores the result; the image processing produces it.

### Q: Who are the users and what can each do?

Three roles. **Admin** manages master data. **Faculty** conduct sessions and manage attendance *for offerings they teach only*. **Students** have read-only access to *their own* record.

Faculty are deliberately not fully trusted — the ownership check is per-request, not per-role.

---

## 2. Image Processing

### Q: Explain your complete pipeline.

Eleven stages:

1. **Capture** — browser grabs a frame at 2 fps, JPEG at quality 0.8
2. **Decode** — bytes → BGR array
3. **Preprocess** — resize to 640 px max, CLAHE on the L channel
4. **Detect** — SCRFD returns boxes, scores, and five landmarks
5. **Quality gate** — reject faces that are too small, blurry, dark, or turned away
6. **Align** — similarity transform to canonical 112×112
7. **Normalise** — pixels to [−1, 1]
8. **Embed** — ArcFace → 512-d → L2 normalise
9. **Match** — one matrix multiply against the enrolled index
10. **Decide** — three-band threshold
11. **Persist** — insert, catching the unique violation

### Q: What is CLAHE and why did you use it?

Contrast Limited Adaptive Histogram Equalization.

Histogram equalisation redistributes pixel intensities to use the full range, improving contrast. Global equalisation computes one mapping for the whole image, which fails in a classroom: a window floods one half with daylight while the other sits in shadow, and one mapping must serve both.

CLAHE is **adaptive** — the image is divided into 8×8 tiles, each with its own mapping — and **contrast limited**, so the amplification of any histogram bin is capped, which prevents amplifying sensor noise into a grainy mess.

### Q: Why LAB colour space rather than RGB?

In BGR, brightness and colour are entangled — you cannot change how bright something is without changing its colour values. LAB separates them: L is lightness, A and B carry colour.

Equalising only L means colour is untouched.

**This is a fairness decision, and I measured it.** Equalising B, G, and R independently stretches each channel by a different amount, so the ratios between them change and hue shifts. Measured mean hue shift: **43.46° for per-RGB equalisation versus 3.98° for CLAHE on LAB** — an 11× difference. Because the distortion depends on the original skin tone, it is not uniform across people.

### Q: How does blur detection work?

Variance of the Laplacian, and it takes one line.

The Laplacian is a second-derivative operator that responds strongly at edges. A sharp image has crisp edges, so the Laplacian output has a wide spread of values — high variance. A blurred image has soft transitions, so the output is small everywhere — low variance.

Measured on a real photograph: sharp 207.5, mildly blurred 10.0, heavily blurred 2.5. An 83× separation, with no training and no parameters.

### Q: What is face alignment and why does it matter?

The detected face is warped so the eyes, nose, and mouth land on fixed canonical coordinates in a 112×112 output, using a similarity transform — rotation, uniform scale, and translation — estimated from the five landmarks by least squares.

It matters because **ArcFace was trained exclusively on aligned faces.** An unaligned crop is out-of-distribution input.

**I measured the cost of skipping it.** Same person, head rotated 18 degrees:

| | Cosine similarity |
|---|---|
| With alignment | **0.9804** |
| Without alignment | **0.2529** |

Without alignment that person scores below the 0.50 threshold and is marked *Unknown*. The failure is not a crash — it is silent, systematic mediocrity.

### Q: Why a similarity transform and not an affine one?

A similarity transform permits rotation, uniform scale, and translation — four degrees of freedom. An affine transform additionally permits shear and non-uniform scale, which would **distort facial geometry** — precisely the signal we are trying to measure.

Five point-pairs over-determine four parameters, so it is solved in the least-squares sense, which averages out small landmark errors rather than warping the face to fit them.

### Q: Why SCRFD rather than Haar cascades or MediaPipe?

SCRFD returns **five landmarks alongside each box**, which is exactly what alignment needs. Haar cascades and MediaPipe's basic mode return only boxes, forcing a second landmark model into the pipeline — one more dependency and one more failure mode.

SCRFD is also more accurate: Haar cascades have a high false-positive rate and are essentially frontal-only.

---

## 3. Face Recognition

### Q: What is a face embedding?

A 512-dimension vector produced by the recognition network from an aligned face. It is a compact numerical summary of identity: embeddings of the same person land close together, different people land far apart. Recognition becomes a nearest-neighbour search.

512 floats is 2,048 bytes per student. A thousand students is 2 MB — it fits in RAM.

### Q: Why cosine similarity rather than Euclidean distance?

Because of how ArcFace was trained.

ArcFace uses Additive Angular Margin Loss, which requires each embedding to sit within a margin **angle** of its class centre. Optimising an angular margin means identity is encoded in the **direction** a vector points, not its length.

Cosine similarity measures the angle between vectors. Euclidean distance conflates direction with magnitude, so two embeddings of the same person pointing the same way but with different lengths would appear far apart. Wrong metric for this model.

### Q: What is L2 normalisation and why do you do it?

Scaling every vector to unit length.

If both vectors are unit length, the denominator of the cosine formula becomes 1 and cosine similarity collapses to a plain **dot product**. That turns "compare against 1,000 students" into a single matrix-vector multiplication.

**Measured: 157 microseconds for 1,000 students.** A naive Python loop over the same data took 1,162 µs — 7× slower for identical arithmetic, because NumPy dispatches to BLAS.

### Q: How do you detect an unknown face?

`argmax` always returns something. Given a complete stranger, the index still reports a "best" match — it is just a poor one. Without a threshold, every visitor gets marked present as whoever they least-dissimilarly resemble.

Three bands:

| Similarity | Decision |
|-----------|----------|
| ≥ 0.50 | Accept |
| 0.40 – 0.50 | Accept, flag for review |
| < 0.40 | Unknown — write nothing |

### Q: Why three bands rather than one threshold?

**Because the two errors are not equally bad.**

A false acceptance marks an absent student present — it enables proxy attendance and corrupts the record. A false rejection means a present student is not recognised — annoying, and fixed by one click.

So the system is tuned to prefer the second, and the grey band routes genuinely ambiguous cases to a human instead of silently guessing.

### Q: How did you choose 0.50 and 0.40?

**Honestly: they are informed starting values, not calibrated results.**

I built the calibration tool — it computes FAR and FRR across a threshold sweep, finds the Equal Error Rate, and plots the genuine and impostor distributions. On my test data it correctly warned that the dataset was too easy and refused to recommend the 0.19 the raw sweep suggested.

**Real calibration requires photographs from different days and lighting conditions**, which I have not collected. That is a documented limitation, and it is the first thing I would do before deploying.

What I *can* show is separation on real data: genuine matches at 1.0000, impostors from −0.0142 to 0.2133, a gap of **0.7867**. The thresholds sit comfortably inside it.

### Q: Why average multiple images during enrolment?

Any single photograph carries incidental noise — an expression, a shadow, sensor grain. Those push the embedding away from the person's true position.

Averaging cancels it: random per-image deviations point in different directions and partially cancel, while the consistent identity signal reinforces.

**The mean must be re-normalised.** The mean of several unit vectors is *not* itself unit length — measured at 0.9915 in my tests. Skipping re-normalisation scales every future similarity down by about 1%, which is a subtle systematic accuracy loss with no error message.

---

## 4. Database Design

### Q: Explain your schema.

14 tables in four groups: identity (roles, users, refresh_tokens), academic structure (departments, subjects, subject_offerings, enrollments), profiles (students, faculty), attendance (sessions, records, face_embeddings), and observability (recognition_logs, audit_logs).

35 indexes, 21 foreign keys, 17 CHECK constraints, 13 UNIQUE constraints.

### Q: What is `subject_offerings` and why do you need it?

**This is the question I would most want to be asked.**

A subject is a catalogue entry — "CS301 Data Structures". An **offering** is a specific teaching instance: this subject, this faculty member, this section, this academic year.

Link attendance directly to `subjects` and four things break:

1. **Authorisation becomes impossible.** "May Prof. Sharma view this session?" has no answer — the session points to CS301, and nothing records who teaches CS301. Faculty can read each other's data. That is an IDOR vulnerability.
2. **Sections collide** — two sections' attendance lands in one unseparable bucket.
3. **History corrupts** when teaching is reassigned.
4. **Academic years run together.**

In ER terms this is a **resolved ternary relationship**: when three entities must associate simultaneously, the correct model is an associative entity.

### Q: Is your database normalised?

Yes, to BCNF.

- **1NF** — every column atomic
- **2NF** — trivially satisfied; all tables use single-column surrogate keys, so partial dependency is impossible
- **3NF** — no transitive dependencies. `students` stores `department_id`, not `department_name`; storing the name would make it depend on `department_id`, which depends on `id`
- **BCNF** — every determinant is a candidate key; all three multi-column uniqueness rules are enforced as UNIQUE constraints

**One deliberate denormalisation:** `students.face_enrolled` duplicates a fact derivable from `face_embeddings`, because the admin roster shows an "enrolled?" badge for 1,000 students and deriving it would mean a subquery per row. Exactly one component may write it. A documented denormalisation with a single writer is an optimisation; an undocumented one is a bug.

### Q: How do you prevent duplicate attendance?

A database constraint: `UNIQUE (session_id, student_id)`.

The naive approach checks then inserts, which has a race window between the SELECT and the INSERT. **That window is not theoretical here** — the camera streams at 2 fps, so a student standing in frame for ten seconds generates twenty requests, and network jitter delivers two concurrently.

The correct approach attempts the insert and catches the integrity error. PostgreSQL evaluates the unique index atomically, so exactly one succeeds.

**And there is a subtlety most people miss.** In PostgreSQL an `IntegrityError` aborts the *entire transaction* — every subsequent statement fails until you roll back. When marking six faces from one frame, one duplicate would poison the other five. So the insert is wrapped in `db.begin_nested()`, which issues a SAVEPOINT; only the savepoint unwinds.

**I verified it:** 20 duplicate marks → 0 spurious creations, 1 record, and the next student still marks successfully.

### Q: Why store embeddings as BYTEA rather than an array or JSON?

JSONB stores 512 floats as text — roughly 3× larger, and float-to-string-to-float round-trips lose precision in the final digits. `REAL[]` carries per-element overhead and needs list-to-array conversion on every read.

`BYTEA` from `numpy.tobytes()` is exactly 2,048 bytes, lossless because it is the literal memory representation, and reconstructs with `np.frombuffer` at zero copy cost.

There is a CHECK constraint on `octet_length(embedding) = 2048`, so a truncated vector fails loudly at write time rather than producing silently wrong similarities later.

### Q: Why soft deletion?

Hard-deleting a student with 200 attendance records leaves three options: CASCADE destroys history, RESTRICT makes deletion impossible, or soft delete sets `is_active = false` and preserves everything.

**One deliberate exception:** `face_embeddings` uses a real `ON DELETE CASCADE`, because biometric data must be genuinely erasable on request. Privacy law overrides the convention.

---

## 5. Backend and Security

### Q: Why FastAPI rather than Django or Flask?

FastAPI generates interactive OpenAPI documentation automatically, which satisfies the "document every endpoint" requirement for free. Pydantic gives declarative validation. Its dependency injection is the cleanest way to implement JWT plus role checks.

Django's ORM and admin bring conventions this project does not need, and it is heavier for ML-serving workloads.

### Q: How does JWT authentication work here?

Two tokens. A short-lived **access token** (30 minutes) sent on every request, and a long-lived **refresh token** (7 days) stored in the database.

The refresh token is stored because **a JWT cannot otherwise be revoked** — it is verified by signature alone, so logging out cannot invalidate it. Storing refresh tokens means logout can mark them revoked.

They are stored **hashed** with SHA-256, for the same reason passwords are: a database dump must not yield usable session credentials.

### Q: Why bcrypt for passwords but SHA-256 for refresh tokens?

Because the threats differ.

bcrypt is deliberately slow, which defeats brute force against **low-entropy** inputs — humans pick "password123", so guessing is viable and must be made expensive.

A refresh token is a signed JWT containing high-entropy machine-generated material. There is no dictionary to try, so bcrypt's 250 ms buys nothing and would be paid on every refresh.

**Matching the algorithm to the threat, rather than reflexively reaching for "the most secure option", is the actual skill.**

### Q: What security vulnerabilities did you defend against?

| Attack | Defence |
|--------|---------|
| SQL injection | ORM parameterises every query |
| Password brute force | bcrypt cost 12 + lockout after 5 failures |
| **Account enumeration** | Identical error for unknown email and wrong password |
| **Timing side-channel** | Dummy bcrypt hash runs on unknown emails so response time matches |
| **JWT `alg=none` forgery** | `algorithms=` pinned; never read from the token |
| **Token type confusion** | A `type` claim; a refresh token cannot be used as an access token |
| **IDOR** | Per-resource ownership check, not just a role check |
| Horizontal privilege escalation | A student may access only their own ID |
| Session hijack via unattended laptop | Password change requires the current password |
| Stack trace disclosure | Generic 500 body; the traceback goes to the server log |

### Q: Explain the timing attack defence.

Without it, a login for an unknown email returns in about 1 ms — no bcrypt work happens. A known email with a wrong password takes about 250 ms, because bcrypt runs.

An attacker measures that difference and enumerates which addresses are registered. **The vague error message alone does not prevent this, because the timing leaks what the text hides.**

So when no user matches, the code still runs bcrypt against a dummy hash. Both paths take the same time.

### Q: Where is authorisation enforced, and why does that matter?

**Server-side, in the service layer, on every request.**

The frontend hides menu items a role cannot use, but that is a usability feature, not security — anyone can open devtools or call the API with curl.

There are two independent checks on faculty requests: a **role** check (are you faculty?) and an **ownership** check (do you teach this offering?). The role check alone leaves the IDOR hole.

---

## 6. Architecture

### Q: Why a monolith rather than microservices?

**Microservices solve an organisational problem — many teams deploying independently — not a technical one.** With one developer, adopting them would be architecture theatre: service discovery, inter-service auth, distributed tracing, orchestration, all for no benefit.

I built a **modular monolith**: one deployable, with strict internal layer boundaries. Shopify, GitHub, and Stack Overflow all run large modular monoliths.

The important part is that I can name the seam I would cut along. The ML layer sits behind interfaces in `app/ml/interfaces.py`, so extracting it into a separate GPU-backed service requires **no change to business logic.**

### Q: Where did you apply SOLID principles?

**Single Responsibility** — `RecognitionService` determines *who*; `AttendanceService` determines *what to record*. Merging them would create a class knowing both computer vision and business rules.

**Dependency Inversion** — services depend on `IFaceDetector`, `IEmbedder`, `IMatcher`, not concrete classes. That is what lets a unit test inject a fake embedder and run in milliseconds instead of loading a 275 MB model.

**Open/Closed** — adding a new attendance status means extending the enum and the CHECK constraint, not editing the marking logic.

### Q: You used a singleton for the model. Isn't that an anti-pattern?

Usually, yes. This is a genuine exception, and the numbers explain why: the model takes 5.5 seconds to load and occupies about 500 MB. Loading per request would make every recognition take 5+ seconds and exhaust memory under any concurrency.

Testability is preserved because services depend on the interfaces, not the singleton — nothing is forced to reach for global state.

---

## 7. Analytics

### Q: How do you calculate attendance percentage?

```
              present + late
percentage = ─────────────────────────── × 100
              eligible closed − excused
```

Three subtleties:

- **Cancelled sessions are excluded entirely** — the class never happened
- **Sessions before a student's enrolment are excluded** — mid-semester joiners are not penalised
- **Excused absences are removed from the denominator**, not counted as absences

And when the denominator is zero, the result is **null, not 0%**. Returning zero would flag every student as a defaulter on day one.

### Q: Your overall percentage sums totals rather than averaging subjects. Why?

Averaging per-subject percentages weights a 5-session subject equally with a 40-session one. Summing numerators and denominators gives each **session** equal weight, which is what "overall attendance" means.

**The same reasoning caught a real bug in my monthly chart.** Consider a month with two sessions: 1 of 2 present, and 9 of 10 present.

| Method | Result |
|--------|--------|
| Average of daily rates | (50 + 90) / 2 = **70.0%** |
| Summed totals | (1+9) / (2+10) = **83.3%** |

A **13-point error**, and nothing about the wrong version looks broken. That is Simpson's paradox, and there is a test that constructs it deliberately.

### Q: Why does your absentee chart rank by count rather than percentage?

Because they are different questions.

A student who attended 1 of 2 classes sits at 50%. A student who attended 30 of 45 sits at 67% — but has missed **fifteen** classes against the first student's one.

Percentage answers "who risks being barred from the exam" — the defaulter list already does that. Count answers "who is actually not turning up", which is a pastoral question. Ranking this by percentage would make it a duplicate under another name.

---

## 8. Limitations — Prepare These Most Carefully

**Volunteering these earns more credit than being caught out by them.**

### Q: What does your system not do?

Four things, in order of significance:

1. **Face enrolment is not implemented.** The pipeline, schema, and averaging logic all exist and pass tests, but no endpoint or UI captures images. The embedding index is empty, so automatic marking cannot currently be demonstrated end to end. This is the honest state of the project.
2. **The threshold is not calibrated on real data.** The tool exists and works; the multi-session photographs it needs have not been collected.
3. **Demographic accuracy is unmeasured.** Face recognition varies across skin tone, gender, and age (NIST FRVT Part 3). I have not evaluated this system across a demographic range.
4. **No anti-spoofing.** A photograph held to the camera would be accepted. The mitigation is procedural — faculty are in the room.

### Q: Can someone cheat this system?

Yes, in three ways I know of:

- **A photograph** would be accepted, because liveness detection is not implemented
- **A lookalike** — identical twins or close siblings — could produce a false acceptance. The system computes the margin to the second-best candidate, which exposes ambiguity that confidence alone hides, but does not eliminate it
- **A student not enrolled for face data** must be marked manually, so the usual social vulnerabilities apply

The system reduces proxy attendance; it does not eliminate it. That is why manual override exists and why every recognition is logged.

### Q: Is face recognition fair to everyone?

**Not automatically, and I would not claim it is.**

NIST's FRVT Part 3 evaluation documented accuracy differences across demographic groups at scale. I have not measured this system against my own class demographics.

What I did do: CLAHE operates on the LAB luminance channel specifically so contrast enhancement does not distort skin tone — measured at 3.98° hue shift versus 43.46° for the naive per-channel approach. That mitigates one mechanism, not the underlying model behaviour.

And manual override is always available, so no student can be *stuck* unrecognised.

### Q: What would you do differently?

Three things:

1. **Build face enrolment first**, before the analytics. I built breadth before closing the core loop, and the result is a system that cannot demonstrate its headline feature.
2. **Collect calibration data from week one**, so the threshold would be an experimental result rather than an informed guess.
3. **Run the tests against PostgreSQL in CI**, not only SQLite. Three bugs I found were dialect differences that a PostgreSQL-based suite would have caught immediately.

---

## 9. Rapid-Fire

| Question | Answer |
|----------|--------|
| Embedding dimension? | 512 |
| Storage per student? | 2,048 bytes |
| Detection model? | SCRFD |
| Recognition model? | ArcFace r100 (w600k_r50) |
| Model pack? | InsightFace `buffalo_l`, ~275 MB |
| Match threshold? | 0.50 accept, 0.40 review |
| Frame rate? | 2 fps |
| Aligned face size? | 112×112 |
| Landmarks? | 5 — both eyes, nose, both mouth corners |
| Tables? | 14 |
| API endpoints? | 27 |
| Tests? | 340, all passing |
| Search time, 1,000 students? | 157 µs |
| End-to-end recognition? | ~540 ms |
| Password hashing? | bcrypt, cost 12 |
| Token lifetimes? | 30 min access, 7 day refresh |
| Attendance threshold? | 75%, configurable |

---

## 10. Live Demonstration Checklist

Rehearse this at least twice, **on the actual demo machine**.

- [ ] Both servers started, `/docs` loads
- [ ] Sign in as faculty → dashboard shows classes
- [ ] Open a session → register shows all enrolled students
- [ ] Start the camera → boxes appear on faces
- [ ] **Have someone unenrolled step in → violet "Unknown" box.** This is the moment that proves the system rejects strangers rather than guessing
- [ ] Tap a tile → mark someone manually
- [ ] Close the session → summary shows auto-generated absentees
- [ ] Sign in as a student → threshold meter, worst subject first
- [ ] **Try to view another student's record → 403.** Demonstrating a *refusal* is more convincing than demonstrating a success
- [ ] Show Analytics → five charts
- [ ] Run one test suite live

**Backup plan:** record a screen capture of the full flow 48 hours before the demo. If the camera fails on the day, you still have evidence.

---

*Good luck. The measured numbers are your strongest asset — use them.*
