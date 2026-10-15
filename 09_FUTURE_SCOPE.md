# Future Scope
## AI-Powered Attendance Management System

Ordered by what would actually be built next, not by what sounds most impressive. Each item states why it matters, what it takes, and — where relevant — what it would cost.

---

## Phase 0 — Complete the Current Release

**These are not enhancements. They are unfinished work, and they belong before anything else.**

### 0.1 Face enrolment 🔴 Critical

**The single item blocking the system from functioning.** The CV pipeline, database schema, and averaging logic all exist and pass 144 tests, but nothing captures images and writes an embedding. The index is empty, so every face returns Unknown.

Required:
- `POST /students/{id}/faces` — consent check, strict quality gate, 5–15 images, intra-set consistency verification, averaging, raw-image deletion, index refresh
- `DELETE /students/{id}/faces` — right to erasure
- `POST /students/{id}/consent` — consent recording
- Capture UI with live quality feedback

**Effort:** 2–3 days. Every component it depends on is already written and tested.

### 0.2 Threshold calibration on real data 🔴 Critical

The tool exists and produces FAR/FRR curves, an EER figure, and a publication-quality plot. It has only been run on synthetic variations of a single capture session, which overstates genuine similarity and would recommend a dangerously low threshold — the tool now detects this and warns.

Required: photographs of 10+ people across 3+ sessions on different days, in different lighting.

**Effort:** a week of collection, an hour of analysis. **This is the highest-value item for your project report** — it converts "we used 0.5" into an experimental result with an ROC curve.

### 0.3 Demographic accuracy evaluation 🔴 High

Face recognition accuracy varies across skin tone, gender, and age (NIST FRVT Part 3). This system has not been evaluated across a demographic range.

Uses the same dataset as 0.2, segmented. Report per-group accuracy honestly. **A limitations section that names a real weakness in your own system is worth more than a results section claiming perfection.**

### 0.4 Admin interface

Student, faculty, subject, and offering CRUD currently exist only through `/docs`. The service layer and schemas are done; this is UI work against existing endpoints.

**Effort:** 2 days.

---

## Phase 1 — Production Hardening

### 1.1 Anti-spoofing / liveness detection 🟠

A photograph held to the camera would currently be accepted.

| Approach | Cost | Effectiveness |
|----------|------|---------------|
| **Passive texture analysis** | Low — one extra model | Catches printed photos and most screen replays |
| Micro-movement across frames | Low — no new model | Catches static photos; defeated by video |
| Challenge-response ("blink now") | Medium; hurts usability | Strong, but slow in a queue |
| Depth camera | Hardware cost | Strongest |

**Recommendation:** passive texture analysis. InsightFace ships a compatible anti-spoofing model, and it adds ~30 ms per face.

### 1.2 Fix the stale index defect 🟠

Documented in the architecture: the embedding index is per-worker, so with multiple Uvicorn workers, enrolling a student refreshes it in one process while others keep a stale copy.

**Fix:** Redis pub/sub invalidation. When enrolment commits, publish a message; every recognition worker rebuilds.

**Effort:** half a day. **This is a correctness bug, not an optimisation** — it appears the instant you run more than one worker.

### 1.3 Rate limiting

No protection against a client hammering `/mark-face` or `/auth/login`. `slowapi` middleware is roughly ten lines.

### 1.4 Automated backups

`pg_dump` on a nightly cron, with restore *tested*. An untested backup is not a backup.

### 1.5 Structured logging and metrics

Console logs suffice at pilot scale. Beyond that, `structlog` plus Prometheus counters for recognition latency, match confidence distribution, and error rates.

### 1.6 CI/CD

GitHub Actions running the full suite on every push — **against PostgreSQL, not SQLite.** Three of the seven bugs found during development were dialect differences a PostgreSQL-based CI would have caught immediately.

---

## Phase 2 — Features from the Original Brief

Deferred deliberately during the three-week build; each is now straightforward because the schema already supports it.

### 2.1 PDF reports
ReportLab, server-side. CSV already works. **Effort:** 1 day.

### 2.2 Email notifications
Weekly digests to students below threshold; monthly summaries to faculty. Needs a background task queue — Celery or FastAPI's `BackgroundTasks` for a simple version.

### 2.3 SMS notifications
Twilio or an Indian provider such as MSG91. Note the per-message cost: 1,000 students × 4 messages a month is a real recurring expense that should be justified before building.

### 2.4 QR code fallback
A rotating QR displayed by the lecturer; students scan to mark themselves. Useful when the camera fails or a student has no face data.

**Security caveat worth stating:** this is weaker than face recognition — a student can photograph the QR and send it to an absent friend. Rotating the code every 30 seconds narrows but does not close the window.

### 2.5 Geofencing and GPS verification
Confirms the device is physically in the classroom. **Honest assessment: weak.** GPS is accurate to 5–10 m indoors at best, which cannot distinguish a classroom from the corridor, and location can be spoofed on a rooted device. Wi-Fi SSID or BLE beacon verification is more reliable if this is genuinely needed.

### 2.6 Multi-face classroom photo
One wide shot of the room rather than a queue past the camera. The pipeline already handles multiple faces; the constraint is **resolution** — a face 60 px wide is the quality-gate minimum, which at the back of a lecture hall needs a 4K camera.

### 2.7 Attendance heat maps
Day-of-week × time-of-day grids showing when attendance drops. Genuinely useful for timetabling: it reveals that the 8 a.m. Monday slot loses 30% of the class.

---

## Phase 3 — Scale

Relevant past roughly 10,000 students. The migration path is documented in the architecture; here is what triggers each step.

### 3.1 GPU inference
**The first thing to break under growth.** CPU inference runs ~3.5 faces/second/worker; a 500-student assembly would take over two minutes.

Change `ctx_id=-1` to `ctx_id=0` and swap `onnxruntime` for `onnxruntime-gpu` — a 10–20× speedup for one parameter and one dependency.

### 3.2 pgvector for the embedding index
Currently a linear scan, which is 157 µs at 1,000 students but grows linearly. Past ~50,000, use pgvector with an HNSW index for approximate nearest-neighbour search in ~5 ms.

**No schema migration needed** — embeddings are already stored in a form pgvector can adopt.

### 3.3 Extract the ML service
The seam already exists. Because the ML layer sits behind `app/ml/interfaces.py`, extraction requires no change to business logic. Lets recognition scale on GPU nodes while CRUD endpoints stay on cheap CPU instances.

### 3.4 Read replica
Analytics queries aggregate across thousands of records and compete with attendance writes. Route reporting to a replica.

### 3.5 Multi-tenancy
Selling to multiple colleges requires tenant isolation. Options: schema-per-tenant (strong isolation, operationally heavy), row-level security (one schema, PostgreSQL RLS policies), or separate databases (strongest, most expensive).

**Recommendation:** row-level security. PostgreSQL supports it natively, and it requires adding `tenant_id` rather than restructuring.

---

## Phase 4 — Research Directions

Genuinely open problems, suitable for a final-year extension or an M.Tech project.

### 4.1 Bias measurement and mitigation
Measuring demographic accuracy is Phase 0. **Mitigating** it is research: fine-tuning on a locally representative dataset, per-group threshold calibration, or fairness-constrained training. This is a publishable direction with real social value.

### 4.2 Occlusion robustness
Masks, glasses, beards, headscarves. Post-2020 face recognition research has produced periocular-region models that work with the lower face covered.

### 4.3 Continuous enrolment
Faces change — students grow beards, change hairstyles, age over four years. A system that updates the reference embedding as confident recognitions accumulate would degrade less over time.

**The risk is drift:** a chain of slightly-wrong updates could gradually shift a student's reference toward someone else. Any implementation needs a mechanism to detect and bound this.

### 4.4 Edge deployment
Running recognition on a Raspberry Pi or Jetson Nano in each classroom, so frames never leave the room. Better privacy and no network dependency, at the cost of deploying and updating many devices.

### 4.5 Federated identity
If several colleges deployed this, a student transferring between them should not need re-enrolment — but sharing raw embeddings across institutions is a significant privacy problem. Federated learning or secure multi-party computation are the relevant approaches.

---

## Commercialisation Considerations

If this were taken to market, these matter more than any feature above.

### Legal and compliance
- **DPDP Act 2023 (India)** — biometric data is sensitive personal data requiring explicit, informed, revocable consent
- **Data Protection Impact Assessment** — likely mandatory for biometric processing at scale
- **Retention policy** — how long is data kept after a student graduates? The system must be able to answer.
- **Minors** — schools introduce parental consent requirements this system does not currently model

### Product
- A student who declines biometric consent must not be disadvantaged. Manual marking must be a first-class path, not a grudging fallback.
- Faculty adoption depends on the system being *faster* than a paper register. If it is slower on any day, it will be abandoned.
- Integration with existing ERP or LMS systems is usually the first question an institution asks.

### The honest commercial assessment
The technical architecture supports scale — the schema needs no migration to reach cloud deployment, and the ML layer is extractable. The gaps are operational (backups, monitoring, CI) and legal (consent workflow, DPIA, retention), not architectural.

**The largest non-technical risk is trust.** A system that wrongly marks a student absent, or that is perceived as surveillance, will be rejected regardless of accuracy. Manual override, transparent logging, and a visible consent process are not compliance overhead — they are the product.

---

## Recommended Order

| Priority | Item | Effort | Why |
|----------|------|--------|-----|
| 1 | Face enrolment | 2–3 days | Nothing works without it |
| 2 | Threshold calibration | 1 week collection | Turns a guess into a result |
| 3 | Demographic evaluation | 1 day (same data) | Highest report value |
| 4 | Admin UI | 2 days | Usable without `/docs` |
| 5 | Anti-spoofing | 1 day | Closes the obvious attack |
| 6 | Redis index invalidation | 0.5 day | Fixes a real correctness bug |
| 7 | CI against PostgreSQL | 0.5 day | Catches dialect bugs |
| 8 | PDF reports | 1 day | Commonly requested |

**Items 1–3 are what stand between this project and a complete one.** Everything after is genuine future scope.

---

*End of Future Scope v1.0*
