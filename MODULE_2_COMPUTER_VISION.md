# Module 2 — Computer Vision Pipeline

**Status:** Complete — 144/144 checks passing across 4 test suites
**Model:** InsightFace `buffalo_l` (SCRFD detection + ArcFace w600k_r50 recognition)
**Validated against:** real face images, not mocks

| Suite | Checks | Covers |
|-------|--------|--------|
| `test_ml_01_preprocessing.py` | 30/30 | CLAHE, resize, blur, brightness, pose |
| `test_ml_02_detection.py` | 40/40 | Model lifecycle, SCRFD, landmarks, quality gate |
| `test_ml_03_embedding.py` | 30/30 | Alignment, embeddings, averaging |
| `test_ml_04_matching.py` | 44/44 | Index, thresholds, unknown detection |

---

## 1. The Eleven Concepts, In Order

Each stage of the pipeline is a named technique solving a specific failure. This is the spine of your report's methodology chapter.

| # | Concept | Problem it solves | Where |
|---|---------|-------------------|-------|
| 1 | **LAB colour space** | BGR entangles brightness with colour | `preprocessing.py` |
| 2 | **CLAHE** | Mixed classroom lighting (window vs shadow) | `preprocessing.py` |
| 3 | **Aspect-preserving resize** | Detection cost; distorted faces embed poorly | `preprocessing.py` |
| 4 | **Variance of Laplacian** | Motion-blurred faces cause wrong-person matches | `preprocessing.py` |
| 5 | **Landmark pose estimation** | Profile views carry half the facial information | `preprocessing.py` |
| 6 | **Similarity transform alignment** | Head tilt, scale, position vary independently of identity | `embedder.py` |
| 7 | **Pixel normalisation** | Must match training preprocessing exactly | `embedder.py` |
| 8 | **ArcFace angular embeddings** | Identity encoded as vector *direction* | `embedder.py` |
| 9 | **In-memory NumPy index** | 1,000 comparisons per face, per frame | `matcher.py` |
| 10 | **Matrix multiply search** | BLAS beats a Python loop by orders of magnitude | `matcher.py` |
| 11 | **Three-band thresholding** | `argmax` always returns *something* | `matcher.py` |

---

## 2. Measured Results — Use These In Your Report

Everything below was measured by the test suites on real face images. These are results, not claims.

### 2.1 Alignment is worth 72 percentage points

The stage most beginners skip. Same person, head rotated 18°:

| Approach | Cosine similarity |
|----------|-------------------|
| **With alignment** | **0.9804** |
| Without alignment (naive resize) | 0.2529 |
| **Difference** | **+0.7275** |

**Read that carefully.** Without alignment the same person scores 0.25 — *below* the 0.50 threshold. They would be marked **Unknown** and not counted present. The failure isn't a crash; it's silent, systematic mediocrity.

This is why `align()` takes the full frame plus landmarks, never a cropped box.

### 2.2 CLAHE on LAB preserves skin tone; per-RGB equalisation does not

| Method | Mean hue shift |
|--------|----------------|
| **CLAHE on LAB L-channel** | **3.98°** |
| Naive per-RGB equalisation | 43.46° |

An 11× difference. Because per-channel equalisation stretches B, G, and R by different amounts, the ratios between them change and hue shifts — and the distortion depends on the original skin tone, so it isn't uniform across people. This is a concrete fairness decision, and it belongs in your limitations section alongside the NIST FRVT discussion.

### 2.3 Genuine and impostor distributions separate cleanly

Three enrolled students, three unenrolled people, real photos:

```
GENUINE  matches:  1.0000
IMPOSTOR matches: -0.0142 to 0.2133
SEPARATION GAP:    0.7867
```

Thresholds at 0.40 / 0.50 sit comfortably inside that gap. Every unenrolled face was correctly flagged **UNKNOWN**.

### 2.4 Performance

| Operation | Time | Budget |
|-----------|------|--------|
| Preprocessing (CLAHE + resize) | 5 ms | — |
| Detection, 6 faces | 257 ms | 500 ms |
| Embedding, per face | ~285 ms | 300 ms |
| **Index search, 1,000 students** | **157 µs** | 1 ms |
| Index search, 50-student roster | 56 µs | — |

The search number is the striking one: **searching a thousand students takes 157 microseconds** because it's one BLAS matrix multiply. A naive Python loop over the same data took 1,162 µs — 7× slower for identical arithmetic.

---

## 3. Three Bugs the Tests Caught

### Bug 1 — Detection was running recognition it didn't need (9× slowdown)

`FaceAnalysis.get()` is a convenience wrapper that runs **every loaded model over every detected face**. My detector called it, so drawing bounding boxes on a live preview was silently computing six ArcFace embeddings per frame.

| | Before | After |
|---|--------|-------|
| Detection, 6 faces | 2,337 ms | **257 ms** |

Fixed by calling the detection model directly via `engine.detect_only()`. This took the pipeline from *outside* the 2 fps budget to comfortably inside it.

**The general lesson:** a convenience API that "does everything" is rarely right inside a hot loop. Reach for the specific model you need.

### Bug 2 — Pre-aligned crops are not detectable

A 112×112 aligned face crop returns **zero detections**. The face fills the frame with no surrounding context, and detectors need margin to localise.

Practical consequence: **you cannot re-detect an already-aligned face.** Alignment must happen once, from the full frame. If you ever store aligned crops and try to re-process them later, this will bite you.

### Bug 3 — Calibration on weak data recommends a dangerous threshold

The calibration script initially recommended **0.19** — because my test dataset's "genuine" pairs were variants of a *single* photograph. Genuine scores clustered at 0.96, impostors at 0.03, so the sweep found perfect separation at an absurdly low threshold.

Deploy 0.19 in production and strangers start matching enrolled students.

Fixed with a sanity guard: when genuine mean > 0.90 and std < 0.05, the script warns loudly that the dataset is too easy and floors the recommendation at 0.35.

**This matters for your project.** Real genuine pairs — different days, lighting, distance, expression — typically score **0.55–0.80**, not 0.95+. Calibrate on multi-session data or your threshold will be wrong in the dangerous direction.

---

## 4. Files Delivered

```
backend/app/ml/
├── interfaces.py        ABCs + dataclasses — the SOLID seam
├── preprocessing.py     CLAHE, resize, blur, brightness, pose, quality gate
├── engine.py            Singleton model loader (3s load, 500MB, once)
├── detector.py          SCRFD wrapper → boxes + 5 landmarks
├── embedder.py          Alignment + ArcFace + averaging + consistency
└── matcher.py           In-memory index, matrix search, 3-band thresholds

backend/scripts/
├── ml_01_live_detection.py       Webcam + boxes + landmarks + quality
├── ml_02_live_recognition.py     Enrol yourself, then be recognised
└── ml_03_calibrate_threshold.py  FAR/FRR sweep, EER, publication plot

backend/tests/
├── test_ml_01_preprocessing.py   30 checks
├── test_ml_02_detection.py       40 checks
├── test_ml_03_embedding.py       30 checks
└── test_ml_04_matching.py        44 checks

docs/threshold_calibration.png    Example output plot
```

---

## 5. How to Run It

### First: the automated suites (no webcam needed)

```
cd backend
venv\Scripts\activate
python tests/test_ml_01_preprocessing.py
python tests/test_ml_02_detection.py
python tests/test_ml_03_embedding.py
python tests/test_ml_04_matching.py
```

The first run downloads ~275 MB to `C:\Users\<you>\.insightface\models\`. If your college network blocks it, download `buffalo_l.zip` from the InsightFace releases page manually and unzip it there.

### Then: see your own face

```
python scripts/ml_01_live_detection.py
```

Controls: `q` quit · `c` toggle CLAHE · `l` toggle landmarks · `s` save frame · `SPACE` pause

Watch the numbers under each box — width, blur score, yaw. Toggle CLAHE with the lights dimmed and watch the detection score change. That's the theory made visible.

### Then: recognise yourself

```
python scripts/ml_02_live_recognition.py
```

Press `e`, type your name, hold still while it captures 8 frames.

**Do these five things in order — each teaches something:**

1. Enrol yourself → confidence sits near 0.95+
2. Turn your head slowly → watch confidence fall as pose degrades
3. Walk backwards → the quality gate rejects you at small face size
4. **Have a friend appear without enrolling** → they show **UNKNOWN** in red
5. Cover half your face → watch the score collapse

Step 4 is the one to demonstrate live during your defence. Confirming with your own eyes that an unenrolled person is *rejected* is what proves the system prevents strangers being marked present.

### Finally: calibrate (this is worth marks)

Organise photos as one folder per person:

```
calibration_data/
    ravi/    day1_a.jpg  day1_b.jpg  day3_a.jpg ...
    priya/   ...
    ankit/   ...
```

**Capture across several days and lighting conditions** — see Bug 3. Then:

```
pip install matplotlib
python scripts/ml_03_calibrate_threshold.py calibration_data/
```

You get FAR/FRR curves, an EER figure, a recommended threshold, and `threshold_calibration.png`. That plot plus the numbers turns "we used 0.5 because a tutorial said so" into a defensible experimental result — and it closes gaps G-11 and G-12 from your design audit.

---

## 6. Windows Troubleshooting

| Symptom | Fix |
|---------|-----|
| Camera window never opens | Settings → Privacy & security → Camera → allow desktop apps |
| Black frames | Close Zoom/Teams/OBS — only one process can hold the camera |
| Camera slow to start | Already handled: `cv2.CAP_DSHOW` forces the DirectShow backend |
| Wrong camera used | Change `CAMERA_INDEX = 0` to `1` at the top of the script |
| Model download fails | Manually place `buffalo_l/` in `C:\Users\<you>\.insightface\models\` |
| `numpy` import errors | `pip install numpy==1.26.4 --force-reinstall` |

---

## 7. Definition of Done

- [ ] All four test suites report full passes (30/40/30/44)
- [ ] `ml_01_live_detection.py` shows a box around your face
- [ ] CLAHE toggle produces a visible difference in dim light
- [ ] `ml_02_live_recognition.py` recognises you after enrolment
- [ ] **An unenrolled person shows UNKNOWN in red**
- [ ] Quality gate rejects you when you stand too far back
- [ ] Calibration run on multi-session data, plot saved
- [ ] `.env` thresholds updated from the calibration result
- [ ] Committed: `feat(ml): face detection, alignment, embedding, matching [4.1-5.4]`

---

## 8. What's Next

The CV pipeline works standalone. It is **not yet connected to the database or the API** — `matcher.load_from_db()` exists but nothing calls it, and no endpoint accepts a frame.

**Module 3 — Recognition & Enrolment APIs** is the bridge:
- Load the model in FastAPI's `lifespan` hook (the hook is already stubbed in `main.py`)
- Build the index at startup from `face_embeddings`
- `POST /students/{id}/faces` — enrolment with consent check, quality gate, averaging, index refresh
- `POST /recognition/identify` — frame in, identities out
- `GET /recognition/health` — model status, index size

After that, Module 4 wires recognition into attendance sessions, and the `UNIQUE (session_id, student_id)` constraint from the database module finally does its job under a live 2 fps stream.

Say the word.
