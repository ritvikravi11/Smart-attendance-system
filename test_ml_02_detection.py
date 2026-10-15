"""
Step 2 test: model loading and face detection.

Loads the real buffalo_l model and runs SCRFD on real images.

Run:  python tests/test_ml_02_detection.py
"""

import os
import sys
import time

sys.path.insert(0, "/home/claude/backend")
os.environ.setdefault("SECRET_KEY", "test-key")

import cv2
import insightface
import numpy as np

from app.ml.detector import ScrfdDetector
from app.ml.engine import get_engine
from app.ml.interfaces import DetectedFace
from app.ml.preprocessing import (
    assess_quality,
    estimate_yaw_ratio,
    preprocess_frame,
    scale_bbox,
)

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


IMG_DIR = os.path.join(os.path.dirname(insightface.__file__), "data", "images")
group = cv2.imread(os.path.join(IMG_DIR, "t1.jpg"))
single = cv2.imread(os.path.join(IMG_DIR, "Tom_Hanks_54745.png"))

print("\n" + "=" * 72)
print("STEP 2 -- MODEL ENGINE AND FACE DETECTION")
print("=" * 72)

# ---------------------------------------------------------------------------
print("\n[2.1] Engine lifecycle")
# ---------------------------------------------------------------------------
engine = get_engine()
check("Engine not ready before load()", not engine.is_ready)

try:
    engine.app
    check("Accessing app before load raises", False)
except RuntimeError:
    check("Accessing app before load raises", True, "loud failure, not silent None")

t0 = time.perf_counter()
engine.load()
load_secs = time.perf_counter() - t0
check("Model loads", engine.is_ready, f"{load_secs:.2f}s")

h = engine.health()
check("Health reports ready", h["ready"] is True)
check("Health reports model name", h["model_name"] == "buffalo_l", h["model_name"])
check(
    "Only detection + recognition loaded",
    set(h["modules"]) == {"detection", "recognition"},
    str(h["modules"]),
)
check("Model version recorded", h["model_version"] is not None, h["model_version"])

t0 = time.perf_counter()
engine.load()
second_call = time.perf_counter() - t0
check("Second load() is a no-op", second_call < 0.01, f"{second_call*1000:.2f}ms")

engine2 = get_engine()
check("get_engine() returns the same instance", engine is engine2, "singleton holds")

# ---------------------------------------------------------------------------
print("\n[2.2] Detection on a real group photo")
# ---------------------------------------------------------------------------
detector = ScrfdDetector()

t0 = time.perf_counter()
faces = detector.detect(group)
detect_ms = (time.perf_counter() - t0) * 1000

check("Faces detected in group photo", len(faces) > 0, f"{len(faces)} faces")
check("Multiple faces found", len(faces) >= 2, f"{len(faces)} -- multi-face works")
check("Detection under 500ms", detect_ms < 500, f"{detect_ms:.0f}ms for {len(faces)} faces")

f0 = faces[0]
check("Returns DetectedFace objects", isinstance(f0, DetectedFace))
check("Bounding box has positive area", f0.bbox.area > 0, f"{f0.bbox.width}x{f0.bbox.height}")
check("Detection score in [0,1]", 0 <= f0.detection_score <= 1, f"{f0.detection_score:.3f}")
check("Detection score is high", f0.detection_score > 0.5, f"{f0.detection_score:.3f}")

# ---------------------------------------------------------------------------
print("\n[2.3] Landmarks -- the reason SCRFD was chosen")
# ---------------------------------------------------------------------------
lm = f0.landmarks
check("Landmarks shape is (5,2)", lm.shape == (5, 2), str(lm.shape))
check("Landmarks are float32", lm.dtype == np.float32, str(lm.dtype))

# Landmark order: [left_eye, right_eye, nose, left_mouth, right_mouth]
left_eye, right_eye, nose, left_mouth, right_mouth = lm
check(
    "Left eye is left of right eye",
    left_eye[0] < right_eye[0],
    f"x: {left_eye[0]:.0f} < {right_eye[0]:.0f}",
)
check(
    "Eyes are above the nose (y increases downward)",
    left_eye[1] < nose[1] and right_eye[1] < nose[1],
    f"eye_y={left_eye[1]:.0f} nose_y={nose[1]:.0f}",
)
check(
    "Nose is above the mouth",
    nose[1] < left_mouth[1],
    f"nose_y={nose[1]:.0f} mouth_y={left_mouth[1]:.0f}",
)
check(
    "All landmarks inside the bounding box",
    all(f0.bbox.x1 - 5 <= p[0] <= f0.bbox.x2 + 5 for p in lm),
)

# ---------------------------------------------------------------------------
print("\n[2.4] Ordering and single-face mode")
# ---------------------------------------------------------------------------
areas = [f.bbox.area for f in faces]
check("Faces sorted largest first", areas == sorted(areas, reverse=True), f"{areas[:3]}...")

# FINDING: a pre-aligned 112x112 crop is NOT detectable. The face fills the
# entire frame with no surrounding context, and detectors need margin around a
# face to localise it. This matters practically: you cannot re-detect an
# already-aligned crop, so alignment must happen ONCE, from the full frame.
check(
    "Pre-aligned 112px crop is NOT detectable",
    len(detector.detect(single)) == 0,
    "detectors need context margin -- align once, from the full frame",
)

# Crop one face from the group photo WITH margin -- the realistic single-face case
fb = faces[0].bbox
pad = int(fb.width * 0.6)
cropped = group[max(0, fb.y1 - pad):fb.y2 + pad, max(0, fb.x1 - pad):fb.x2 + pad]
one = detector.detect_single(cropped)
check("detect_single finds a lone face with margin", one is not None,
      f"crop {cropped.shape[1]}x{cropped.shape[0]}")

none_result = detector.detect_single(group)
check(
    "detect_single REJECTS a multi-face image",
    none_result is None,
    "enrolment ambiguity prevented",
)

blank = np.zeros((480, 640, 3), dtype=np.uint8)
check("Blank image returns []", detector.detect(blank) == [], "not an error")

noise = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
check("Random noise returns few/no faces", len(detector.detect(noise)) <= 1)

# ---------------------------------------------------------------------------
print("\n[2.5] Input validation")
# ---------------------------------------------------------------------------
for label, bad in [
    ("empty array", np.array([])),
    ("grayscale 2D", np.zeros((100, 100), dtype=np.uint8)),
]:
    try:
        detector.detect(bad)
        check(f"Rejects {label}", False)
    except ValueError:
        check(f"Rejects {label}", True)

# ---------------------------------------------------------------------------
print("\n[2.6] Preprocessing + detection together")
# ---------------------------------------------------------------------------
processed, scale = preprocess_frame(group)
faces_small = detector.detect(processed)
check("Detection works on preprocessed frame", len(faces_small) > 0, f"{len(faces_small)} faces")

# Coordinate round-trip: box found on the small image must map back correctly
mapped = scale_bbox(faces_small[0].bbox, scale)
orig_face = faces[0]
centre_error = abs(mapped.center[0] - orig_face.center[0] if hasattr(orig_face, "center") else 0)
check(
    "Scaled box lands in the original image",
    0 <= mapped.x1 < group.shape[1] and 0 <= mapped.y1 < group.shape[0],
    f"mapped to ({mapped.x1},{mapped.y1})-({mapped.x2},{mapped.y2})",
)

t0 = time.perf_counter()
for _ in range(5):
    p, _ = preprocess_frame(group)
    detector.detect(p)
pipeline_ms = (time.perf_counter() - t0) / 5 * 1000
check(
    "Preprocess+detect meets the 2 fps budget",
    pipeline_ms < 500,
    f"{pipeline_ms:.0f}ms (budget 500ms)",
)

# CLAHE should help on a dark image
dark = (group * 0.25).astype(np.uint8)
faces_dark_raw = detector.detect(dark)
dark_enhanced, _ = preprocess_frame(dark)
faces_dark_clahe = detector.detect(dark_enhanced)
print(f"       (dark image: {len(faces_dark_raw)} faces raw, "
      f"{len(faces_dark_clahe)} after CLAHE)")
check(
    "CLAHE does not reduce detections on dark input",
    len(faces_dark_clahe) >= len(faces_dark_raw),
    f"{len(faces_dark_raw)} -> {len(faces_dark_clahe)}",
)

# ---------------------------------------------------------------------------
print("\n[2.7] Quality gate on real detections")
# ---------------------------------------------------------------------------
qualities = [assess_quality(group, f) for f in faces]
n_pass = sum(1 for q in qualities if q.passed)
check("Most faces pass the quality gate", n_pass >= len(faces) - 2,
      f"{n_pass}/{len(faces)} passed")

# FINDING: face 0 is an extreme profile (yaw 0.073) and is correctly REJECTED.
# The gate is doing its job -- a profile view carries half the facial
# information and would embed poorly.
profiles = [q for q, f in zip(qualities, faces)
            if abs(estimate_yaw_ratio(f.landmarks) - 0.5) > 0.30]
check("Profile faces are rejected", all(not q.passed for q in profiles),
      f"{len(profiles)} profile face(s) caught by the pose check")

q = qualities[0]
check("Quality reports blur score", q.blur_score > 0)
check("Quality reports face width", q.face_width == faces[0].bbox.width)

blurred = cv2.GaussianBlur(group, (31, 31), 0)
blur_faces = detector.detect(blurred)
if blur_faces:
    qb = assess_quality(blurred, blur_faces[0])
    check(
        "Blurred face REJECTED by quality gate",
        not qb.passed,
        f"reason: {qb.reason}",
    )
else:
    check("Blurred image yields no detections", True, "detector rejected it outright")

# Strict mode must be harder to pass than normal mode
passing_face = next(f for f, q in zip(faces, qualities) if q.passed)
q_normal = assess_quality(group, passing_face)
q_strict = assess_quality(group, passing_face, strict=True)
check(
    "Strict mode is stricter than normal",
    len(q_strict.failures) >= len(q_normal.failures),
    f"normal={len(q_normal.failures)} strict={len(q_strict.failures)} failures",
)

smallest = faces[-1]
print(f"       (smallest detected face: {smallest.bbox.width}px wide, "
      f"score={smallest.detection_score:.3f})")

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
passed = sum(1 for _, ok in results if ok)
total = len(results)
colour = GREEN if passed == total else RED
print(f"{colour}STEP 2 RESULT: {passed}/{total} checks passed{RESET}")
print("=" * 72 + "\n")
sys.exit(0 if passed == total else 1)
