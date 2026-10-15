# Software Requirements Specification
## AI-Powered Attendance Management System

| | |
|---|---|
| **Document** | Software Requirements Specification (SRS) |
| **Version** | 1.0 |
| **Standard** | Adapted from IEEE 830-1998 |
| **Status** | Baselined — implementation complete for Tier 1 |
| **Date** | August 2026 |

---

## 1. Introduction

### 1.1 Purpose

This document specifies the requirements for an attendance management system that uses computer vision to identify enrolled students and record their attendance automatically. It is written for the project evaluator, the developer, and any future maintainer.

Requirements are numbered so that code, tests, and this document can be cross-referenced. Section 9 provides the traceability matrix.

### 1.2 Scope

**Product name:** Register

The system replaces the paper attendance register in a college classroom. A faculty member opens a session, points a webcam at the room, and enrolled students are identified and marked present as they arrive. Faculty retain override control over every record. Students and administrators access the resulting data through role-specific web dashboards.

**In scope:** authentication, student and academic master data, face enrolment, real-time face detection and recognition, attendance sessions with duplicate prevention, subject-wise and faculty-wise analytics, CSV export.

**Out of scope (documented as Phase 2):** geofencing, GPS verification, SMS and email notification, QR-code fallback, native mobile applications, multi-institution tenancy, LMS integration, offline operation.

### 1.3 Definitions

| Term | Meaning |
|------|---------|
| **Offering** | One teaching instance: a subject, taught by a specific faculty member, to a specific section, in a specific academic year |
| **Session** | One occurrence of a class — the unit attendance is recorded against |
| **Embedding** | A 512-dimension vector representing a face; the mathematical form recognition compares |
| **Threshold** | The cosine similarity above which a face is accepted as a match |
| **Defaulter** | A student below the institution's minimum attendance percentage (default 75%) |
| **FAR / FRR** | False Acceptance Rate / False Rejection Rate — the two error types in a biometric system |

### 1.4 References

- IEEE 830-1998, Recommended Practice for Software Requirements Specifications
- Deng et al., *ArcFace: Additive Angular Margin Loss for Deep Face Recognition*, CVPR 2019
- Guo et al., *SCRFD: Sample and Computation Redistribution for Efficient Face Detection*, ICLR 2022
- NIST FRVT Part 3: Demographic Effects, NISTIR 8280
- Digital Personal Data Protection Act, 2023 (India)
- Regulation (EU) 2016/679 (GDPR), Article 9

---

## 2. Overall Description

### 2.1 Product Perspective

A self-contained, three-tier web application. It does not integrate with existing college systems in this version; student and faculty records are entered through its own administrative interface.

```
Browser (React SPA)
      │  HTTPS · JSON · JWT
Application server (FastAPI + InsightFace)
      │  TCP 5432
PostgreSQL 16
```

### 2.2 Product Functions

1. Authenticate users and enforce role-based access
2. Manage students, faculty, subjects, offerings, and enrolments
3. Enrol a student's face as a biometric template
4. Detect and recognise faces in a live camera stream
5. Record attendance with guaranteed non-duplication
6. Compute attendance percentages by student, subject, and faculty
7. Present analytics and export records

### 2.3 User Classes

| Class | Technical skill | Frequency | Privileges |
|-------|----------------|-----------|------------|
| **Administrator** | Moderate | Weekly | Full CRUD on all master data; may reopen closed sessions |
| **Faculty** | Low | Daily | Sessions and attendance for **their own offerings only** |
| **Student** | Low | Weekly | Read-only access to **their own** attendance |

> Faculty are deliberately **not** fully trusted. A faculty member must not access attendance for an offering they do not teach. This is enforced per-request in the service layer, not by hiding UI elements.

### 2.4 Operating Environment

| Component | Requirement |
|-----------|-------------|
| Server OS | Windows 10/11, Ubuntu 22.04+, or macOS 12+ |
| Python | 3.11 (3.12+ not supported by the ONNX runtime pinned here) |
| Database | PostgreSQL 16 |
| Client | Chrome 100+ or Edge 100+ with a webcam |
| RAM | 4 GB minimum, 8 GB recommended (the face model resides at ~500 MB) |
| Disk | 2 GB (275 MB of that is model weights) |

### 2.5 Constraints

| ID | Constraint | Consequence |
|----|-----------|-------------|
| C-1 | `getUserMedia` requires a secure context | The camera works on `localhost` or HTTPS only. It **fails silently on `http://192.168.x.x`** |
| C-2 | CPU-only inference | ~285 ms per face; a 200-student hall is impractical without a GPU |
| C-3 | Biometric data is legally sensitive | Explicit consent is mandatory before enrolment |
| C-4 | Recognition accuracy varies by demographic | Documented in §7.3 as a known limitation |

### 2.6 Assumptions and Dependencies

- Single institution, single deployment
- Up to 1,000 registered students and 50 concurrent users
- Students consent to biometric enrolment; those who decline are marked manually
- The InsightFace `buffalo_l` model pack is downloadable at first run

---

## 3. Functional Requirements

Written in the standard form: *"The system shall…"*. Priority follows MoSCoW.

### FR-1 Authentication and Authorisation

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-1.1 | Allow login with email and password | Must | ✅ |
| FR-1.2 | Store passwords only as bcrypt hashes (cost 12) | Must | ✅ |
| FR-1.3 | Issue a 30-minute access token and a 7-day refresh token | Must | ✅ |
| FR-1.4 | Assign exactly one role per user | Must | ✅ |
| FR-1.5 | Reject any request whose role lacks permission for that endpoint | Must | ✅ |
| FR-1.6 | Revoke refresh tokens on logout | Must | ✅ |
| FR-1.7 | Lock an account for 15 minutes after 5 consecutive failures | Should | ✅ |
| FR-1.8 | Rotate the refresh token on every use | Should | ✅ |
| FR-1.9 | Terminate all sessions on password change | Should | ✅ |

### FR-2 Student Registration and Face Enrolment

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-2.1 | Register a student with name, roll number, department, semester, email, phone | Must | ⚠️ API-only |
| FR-2.2 | Enforce uniqueness of roll number and email | Must | ✅ |
| FR-2.3 | Capture 5–15 face images per student | Must | ❌ Not implemented |
| FR-2.4 | Reject an image with zero or multiple faces, or below quality threshold | Must | ✅ (pipeline) |
| FR-2.5 | Compute a 512-dimension embedding per accepted image | Must | ✅ |
| FR-2.6 | Store the L2-normalised mean of all embeddings as the reference | Must | ✅ |
| FR-2.7 | Record explicit biometric consent with a timestamp | Must | ✅ (schema) |
| FR-2.8 | Delete raw images after embedding extraction | Must | ✅ (design) |
| FR-2.9 | Support complete deletion of a student's biometric data | Must | ✅ (CASCADE) |

> **FR-2.3 is the outstanding gap.** The pipeline, schema, and service logic exist and are tested; the HTTP endpoint and capture UI do not. See §8.

### FR-3 Face Detection and Recognition

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-3.1 | Detect all faces in a submitted frame | Must | ✅ |
| FR-3.2 | Return a bounding box, confidence, and five landmarks per face | Must | ✅ |
| FR-3.3 | Align each face using landmarks before embedding | Must | ✅ |
| FR-3.4 | Compute cosine similarity against all candidate embeddings | Must | ✅ |
| FR-3.5 | Accept a match only above the configured threshold | Must | ✅ |
| FR-3.6 | Classify a face as Unknown when no match clears the threshold | Must | ✅ |
| FR-3.7 | Return a confidence score with every result | Must | ✅ |
| FR-3.8 | Log every recognition attempt, including failures | Must | ✅ |
| FR-3.9 | Recognise multiple faces in a single frame | Should | ✅ |
| FR-3.10 | Restrict the search space to students enrolled in the offering | Should | ✅ |

### FR-4 Attendance Management

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-4.1 | Allow faculty to open a session for an offering they teach | Must | ✅ |
| FR-4.2 | Record subject, faculty, date, start time, and room per session | Must | ✅ |
| FR-4.3 | Prevent more than one record per student per session | Must | ✅ |
| FR-4.4 | Record marking method and confidence per record | Must | ✅ |
| FR-4.5 | Allow faculty to add, remove, or change any record while open | Must | ✅ |
| FR-4.6 | Mark unrecognised enrolled students absent on close | Must | ✅ |
| FR-4.7 | Prevent modification of closed sessions except by Admin | Should | ✅ |
| FR-4.8 | Support Present, Absent, Late, Excused | Should | ✅ |
| FR-4.9 | Exclude cancelled sessions from percentage calculations | Should | ✅ |
| FR-4.10 | Exclude sessions before a student's enrolment date | Should | ✅ |

### FR-5 Dashboards and Reporting

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-5.1 | Display each student's overall attendance percentage | Must | ✅ |
| FR-5.2 | Display subject-wise breakdown per student | Must | ✅ |
| FR-5.3 | Display chronological attendance history | Must | ✅ |
| FR-5.4 | Allow filtering by subject and date range | Must | ✅ |
| FR-5.5 | Export filtered attendance as CSV | Must | ✅ |
| FR-5.6 | Flag students below a configurable threshold | Should | ✅ |
| FR-5.7 | Show the number of consecutive classes needed to recover | Should | ✅ |
| FR-5.8 | Render daily, monthly, distribution, subject, and absentee charts | Could | ✅ |
| FR-5.9 | Generate PDF reports | Could | ❌ Phase 2 |

### FR-6 Administration

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-6.1 | CRUD on students | Must | ⚠️ API-only |
| FR-6.2 | CRUD on faculty | Must | ⚠️ API-only |
| FR-6.3 | Manage subjects and assign faculty | Must | ⚠️ API-only |
| FR-6.4 | Enrol students into offerings | Must | ⚠️ API-only |
| FR-6.5 | Use soft deletion for anything referenced by attendance | Must | ✅ |
| FR-6.6 | Record an audit entry for every state change | Should | ✅ |

---

## 4. Non-Functional Requirements

### NFR-1 Performance

| ID | Requirement | Target | Measured |
|----|-------------|--------|----------|
| NFR-1.1 | Recognition of one face, end to end | ≤ 2,000 ms | **~540 ms** (257 detect + 285 embed) |
| NFR-1.2 | Non-ML API response, 95th percentile | ≤ 300 ms | Not load-tested |
| NFR-1.3 | Embedding index held in memory | — | ✅ |
| NFR-1.4 | Model loaded once at startup | — | ✅ 5.5 s at boot |
| NFR-1.5 | Similarity search over 1,000 students | ≤ 1 ms | **157 µs** |
| NFR-1.6 | Frame preprocessing | ≤ 100 ms | **5 ms** |

### NFR-2 Security

| ID | Requirement | Status |
|----|-------------|--------|
| NFR-2.1 | bcrypt with per-password salt | ✅ |
| NFR-2.2 | All endpoints except login and health require a valid JWT | ✅ |
| NFR-2.3 | Authorisation enforced server-side; UI hiding is not access control | ✅ |
| NFR-2.4 | Face embeddings never returned by any API response | ✅ |
| NFR-2.5 | All input validated through Pydantic schemas | ✅ |
| NFR-2.6 | Database access exclusively through the ORM | ✅ |
| NFR-2.7 | Secrets from environment variables only | ✅ |
| NFR-2.8 | A student may access only their own data | ✅ |
| NFR-2.9 | Refresh tokens stored hashed | ✅ |
| NFR-2.10 | Login errors must not reveal account existence | ✅ |

### NFR-3 Privacy and Compliance

| ID | Requirement | Status |
|----|-------------|--------|
| NFR-3.1 | Explicit consent recorded before biometric enrolment | ✅ schema + service check |
| NFR-3.2 | Raw images deleted after embedding extraction | ✅ design |
| NFR-3.3 | Recognition frames not persisted | ✅ |
| NFR-3.4 | Complete erasure of biometric data on request | ✅ ON DELETE CASCADE |

### NFR-4 Reliability

| ID | Requirement | Status |
|----|-------------|--------|
| NFR-4.1 | Recognition threshold configurable without code change | ✅ `.env` |
| NFR-4.2 | Degrade to manual marking if camera or model fails | ✅ |
| NFR-4.3 | Duplicate prevention enforced by database constraint | ✅ |
| NFR-4.4 | Timestamps stored in UTC | ✅ TIMESTAMPTZ |

### NFR-5 Maintainability and Scalability

| ID | Requirement | Status |
|----|-------------|--------|
| NFR-5.1 | Business logic isolated in a service layer | ✅ |
| NFR-5.2 | ML components behind interfaces | ✅ `app/ml/interfaces.py` |
| NFR-5.3 | Schema changes through versioned migrations | ✅ Alembic |
| NFR-5.4 | API versioned from the first release | ✅ `/api/v1` |
| NFR-5.5 | Stateless API layer permitting horizontal scale | ✅ JWT |

### NFR-6 Usability

| ID | Requirement | Status |
|----|-------------|--------|
| NFR-6.1 | Responsive across desktop, tablet, mobile | ✅ |
| NFR-6.2 | Light and dark themes with persisted preference | ✅ |
| NFR-6.3 | Live visual feedback during recognition | ✅ |
| NFR-6.4 | Colour never the sole carrier of meaning | ✅ |
| NFR-6.5 | Visible keyboard focus; reduced motion respected | ✅ |

---

## 5. External Interface Requirements

### 5.1 User Interfaces

Five screens: Login, Student Dashboard, Faculty Dashboard, Attendance, Analytics/Reports. Detailed in the User Manual.

### 5.2 Hardware Interfaces

A webcam accessed through the W3C Media Capture API. Minimum useful resolution 720p; the quality gate rejects faces narrower than 60 pixels.

### 5.3 Software Interfaces

| Interface | Protocol | Purpose |
|-----------|----------|---------|
| PostgreSQL 16 | TCP 5432 (psycopg2) | Persistence |
| InsightFace / ONNX Runtime | In-process | Detection and recognition |
| Browser Media Capture | JS API | Frame acquisition |

### 5.4 Communication Interfaces

REST over HTTP/1.1, JSON request and response bodies except frame upload, which is `multipart/form-data`. Authentication by `Authorization: Bearer <token>`.

---

## 6. System Attributes

**Portability.** The backend runs on any platform supporting Python 3.11 and PostgreSQL. Two dialect-specific constructs were deliberately avoided during development: PostgreSQL's `~` regex operator (replaced with SQL-standard `LIKE`) and `date_trunc` (replaced with Python aggregation), so the test suite runs on SQLite.

**Auditability.** Every state change writes to `audit_logs`. Every recognition attempt writes to `recognition_logs`, providing the empirical basis for the accuracy figures in the Testing Report.

---

## 7. Known Limitations

Stated explicitly, because a specification that claims perfection is not credible.

### 7.1 Face enrolment is incomplete

FR-2.3 is unimplemented. The CV pipeline, database schema, and averaging logic all exist and pass tests, but no HTTP endpoint or UI exists to capture images. Consequently the embedding index is empty and automatic marking cannot currently be demonstrated end to end.

### 7.2 Threshold not calibrated on production data

The thresholds (0.50 accept / 0.40 review) are informed defaults. The calibration tool exists and produces FAR/FRR curves and an EER figure, but has been run only on synthetic variations of a single capture session — which overstates genuine similarity and would recommend a dangerously low threshold. Calibration on multi-session data is required before deployment.

### 7.3 Demographic accuracy is unmeasured

Face recognition accuracy is documented to vary across skin tone, gender, and age (NIST FRVT Part 3). This system has not been evaluated across a demographic range. The CLAHE preprocessing operates on the LAB luminance channel specifically to avoid the hue distortion that per-channel equalisation causes — measured at 3.98° versus 43.46° mean hue shift — but that mitigates one mechanism, not the underlying model behaviour.

### 7.4 Anti-spoofing not implemented

A photograph held to the camera would be accepted. The mitigation in this version is procedural: faculty are physically present. Passive liveness detection is a Phase 2 item.

### 7.5 Not load-tested

NFR-1.2 is unverified. Performance figures come from single-request measurements, not concurrent load.

---

## 8. Traceability

| Requirement group | Implementation | Verification |
|-------------------|----------------|--------------|
| FR-1 | `services/auth_service.py`, `core/security.py`, `api/deps.py` | `test_auth.py` (61) |
| FR-2 | `ml/embedder.py`, `models/face.py` | `test_ml_03_embedding.py` (30) |
| FR-3 | `ml/detector.py`, `ml/matcher.py`, `ml/preprocessing.py` | `test_ml_01/02/04` (114) |
| FR-4 | `services/attendance_service.py` | `test_attendance.py` (59) |
| FR-5 | `services/analytics_service.py`, `chart_analytics_service.py` | `test_analytics.py` (46) |
| FR-6 | `models/`, Alembic migrations | `validate_schema.py` (30) |
| NFR-2 | `core/security.py`, `api/deps.py` | `test_auth.py`, `test_edge_cases.py` |
| NFR-5 | `ml/interfaces.py`, layered package structure | Architecture review |

---

## 9. Approval

| Role | Name | Date |
|------|------|------|
| Author | | |
| Reviewer / Guide | | |
| Approved | | |

*End of SRS v1.0*
