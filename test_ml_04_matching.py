"""
Step 4 test: matching, thresholds, and unknown-face detection.

The end-to-end test at the bottom runs the complete pipeline on real faces:
enrol 3 people, then verify each is recognised and a 4th is rejected.

Run:  python tests/test_ml_04_matching.py
"""

import os
import sys
import time

sys.path.insert(0, "/home/claude/backend")
os.environ.setdefault("SECRET_KEY", "test-key")

import cv2
import insightface
import numpy as np

from app.core.config import settings
from app.ml.detector import ScrfdDetector
from app.ml.embedder import ArcFaceEmbedder
from app.ml.engine import get_engine
from app.ml.interfaces import EMBEDDING_DIM
from app.ml.matcher import InMemoryMatcher

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


print("\n" + "=" * 72)
print("STEP 4 -- MATCHING, THRESHOLDS, UNKNOWN DETECTION")
print("=" * 72)

# ---------------------------------------------------------------------------
print("\n[4.1] Empty index behaviour")
# ---------------------------------------------------------------------------
m = InMemoryMatcher()
check("New matcher is empty", m.size == 0)

r = m.search(unit(np.random.randn(EMBEDDING_DIM)))
check("Empty index returns UNKNOWN", r.is_unknown and r.student_id is None)
check("Empty index does not crash", r.confidence == 0.0)

# ---------------------------------------------------------------------------
print("\n[4.2] Index construction with synthetic vectors")
# ---------------------------------------------------------------------------
rng = np.random.default_rng(42)
synthetic = {}
rows = []
for sid in range(1, 101):
    v = unit(rng.standard_normal(EMBEDDING_DIM).astype(np.float32))
    synthetic[sid] = v
    rows.append((sid, v.tobytes()))

count = m._build(rows)
check("Index built", count == 100, f"{count} students")
check("size property correct", m.size == 100)
check("Matrix shape correct", m._matrix.shape == (100, EMBEDDING_DIM), str(m._matrix.shape))
check("Matrix is float32", m._matrix.dtype == np.float32)
check("Matrix is contiguous (BLAS-friendly)", m._matrix.flags["C_CONTIGUOUS"])
check("contains() works", m.contains(50) and not m.contains(999))
check("Memory as expected", abs(m.health()["memory_kb"] - 200) < 5,
      f"{m.health()['memory_kb']} KB for 100 x 512 float32")

# Corrupt row must be skipped, not fatal
bad_rows = rows[:5] + [(999, np.zeros(256, dtype=np.float32).tobytes())]
m_bad = InMemoryMatcher()
n = m_bad._build(bad_rows)
check("Corrupt embedding skipped, not fatal", n == 5, f"{n}/6 loaded, bad row dropped")

# ---------------------------------------------------------------------------
print("\n[4.3] Exact-match retrieval")
# ---------------------------------------------------------------------------
for sid in [1, 50, 100]:
    r = m.search(synthetic[sid])
    ok = r.student_id == sid and not r.is_unknown
    check(f"Student {sid} retrieved exactly", ok, f"confidence {r.confidence:.4f}")

r = m.search(synthetic[7])
check("Self-match confidence is 1.0", abs(r.confidence - 1.0) < 1e-5, f"{r.confidence:.6f}")
check("Runner-up much lower than best", r.runner_up_confidence < 0.5,
      f"best={r.confidence:.3f} runner-up={r.runner_up_confidence:.3f}")
check("Margin is large for a clear match", r.margin > 0.5, f"margin={r.margin:.3f}")

# ---------------------------------------------------------------------------
print("\n[4.4] *** Three-band threshold logic ***")
# ---------------------------------------------------------------------------
print(f"       thresholds: match>={settings.face_match_threshold} "
      f"review>={settings.face_review_threshold}")

base = synthetic[1]


def vector_at_similarity(target_sim):
    """Construct a vector with a known cosine similarity to `base`."""
    ortho = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    ortho -= (ortho @ base) * base          # remove the base component
    ortho = unit(ortho)                      # now perpendicular to base
    return unit(target_sim * base + np.sqrt(max(0.0, 1 - target_sim**2)) * ortho)

for target, expect in [
    (0.95, "accept"), (0.70, "accept"), (0.52, "accept"),
    (0.45, "review"), (0.42, "review"),
    (0.35, "unknown"), (0.10, "unknown"),
]:
    q = vector_at_similarity(target)
    r = m.search(q)
    if expect == "accept":
        ok = (not r.is_unknown) and (not r.needs_review) and r.student_id == 1
    elif expect == "review":
        ok = (not r.is_unknown) and r.needs_review and r.student_id == 1
    else:
        ok = r.is_unknown and r.student_id is None
    check(f"sim={target:.2f} -> {expect.upper()}", ok, f"got conf={r.confidence:.3f}")

# ---------------------------------------------------------------------------
print("\n[4.5] Roster filtering")
# ---------------------------------------------------------------------------
roster = {10, 20, 30, 40}
r = m.search(synthetic[50], candidate_ids=roster)
check(
    "*** Student outside the roster is NOT matched ***",
    r.student_id != 50,
    f"student 50 excluded; best in roster = {r.student_id} @ {r.confidence:.3f}",
)

r = m.search(synthetic[20], candidate_ids=roster)
check("Student inside the roster IS matched", r.student_id == 20, f"{r.confidence:.4f}")

r = m.search(synthetic[1], candidate_ids=set())
check("Empty roster returns UNKNOWN", r.is_unknown)

r = m.search(synthetic[1], candidate_ids={9998, 9999})
check("Roster of unknown IDs returns UNKNOWN", r.is_unknown)

# ---------------------------------------------------------------------------
print("\n[4.6] Search performance -- the matrix multiply")
# ---------------------------------------------------------------------------
big_rows = [(i, unit(rng.standard_normal(EMBEDDING_DIM).astype(np.float32)).tobytes())
            for i in range(1, 1001)]
m_big = InMemoryMatcher()
m_big._build(big_rows)
check("1000-student index built", m_big.size == 1000)

q = unit(rng.standard_normal(EMBEDDING_DIM).astype(np.float32))
t0 = time.perf_counter()
for _ in range(1000):
    m_big.search(q)
per_search_us = (time.perf_counter() - t0) / 1000 * 1e6
check(
    "*** Search over 1000 students is sub-millisecond ***",
    per_search_us < 1000,
    f"{per_search_us:.0f} microseconds per search",
)

# Compare against a naive Python loop to show why the matrix multiply matters
mat = m_big._matrix
t0 = time.perf_counter()
for _ in range(10):
    _ = [float(mat[i] @ q) for i in range(mat.shape[0])]
loop_us = (time.perf_counter() - t0) / 10 * 1e6
speedup = loop_us / per_search_us
print(f"       naive Python loop: {loop_us:.0f}us vs matrix multiply "
      f"{per_search_us:.0f}us = {speedup:.0f}x faster")
check("Matrix multiply beats a Python loop", speedup > 5, f"{speedup:.0f}x")

t0 = time.perf_counter()
for _ in range(100):
    m_big.search(q, candidate_ids=set(range(1, 51)))
filtered_us = (time.perf_counter() - t0) / 100 * 1e6
print(f"       roster-filtered (50 of 1000): {filtered_us:.0f}us")

# ---------------------------------------------------------------------------
print("\n[4.7] *** END-TO-END with real faces ***")
# ---------------------------------------------------------------------------
engine = get_engine()
engine.load()
detector = ScrfdDetector()
embedder = ArcFaceEmbedder()

IMG_DIR = os.path.join(os.path.dirname(insightface.__file__), "data", "images")
group = cv2.imread(os.path.join(IMG_DIR, "t1.jpg"))
faces = detector.detect(group)

# Enrol faces 0, 1, 2 as students 101, 102, 103. Hold back faces 3+ as
# "unknown people" who were never enrolled.
enrolled_embs = {}
real_rows = []
for idx, sid in [(0, 101), (1, 102), (2, 103)]:
    e = embedder.embed(group, faces[idx])
    enrolled_embs[sid] = e
    real_rows.append((sid, e.tobytes()))

m_real = InMemoryMatcher()
m_real._build(real_rows)
check("3 real students enrolled", m_real.size == 3)

# Each enrolled person must be recognised as THEMSELVES
for idx, sid in [(0, 101), (1, 102), (2, 103)]:
    q = embedder.embed(group, faces[idx])
    r = m_real.search(q)
    check(
        f"Enrolled student {sid} recognised correctly",
        r.student_id == sid and not r.is_unknown,
        f"conf={r.confidence:.4f} margin={r.margin:.3f}",
    )

# Unenrolled people must be UNKNOWN
print()
unknown_scores = []
for idx in range(3, len(faces)):
    q = embedder.embed(group, faces[idx])
    r = m_real.search(q)
    unknown_scores.append(r.confidence)
    check(
        f"Unenrolled face {idx} correctly flagged UNKNOWN",
        r.is_unknown and r.student_id is None,
        f"best score only {r.confidence:.4f}",
    )

# The separation between genuine and impostor is the headline number
genuine = [float(m_real.search(embedder.embed(group, faces[i])).confidence)
           for i in [0, 1, 2]]
print(f"\n       GENUINE  matches: min={min(genuine):.4f} max={max(genuine):.4f}")
print(f"       IMPOSTOR matches: min={min(unknown_scores):.4f} max={max(unknown_scores):.4f}")
print(f"       SEPARATION GAP:   {min(genuine) - max(unknown_scores):.4f}")
print(f"       thresholds sit at {settings.face_review_threshold} / "
      f"{settings.face_match_threshold} -- inside the gap\n")

check(
    "*** Genuine and impostor distributions are cleanly separated ***",
    min(genuine) > max(unknown_scores),
    f"gap of {min(genuine) - max(unknown_scores):.4f}",
)
check(
    "Thresholds fall inside the separation gap",
    max(unknown_scores) < settings.face_review_threshold
    and min(genuine) > settings.face_match_threshold,
    "0.40/0.50 correctly placed for this data",
)

# ---------------------------------------------------------------------------
print("\n[4.8] Rebuild and deactivation")
# ---------------------------------------------------------------------------
m_real._build(real_rows[:2])  # student 103 removed, as if deactivated
check("Index shrinks on rebuild", m_real.size == 2)

q103 = embedder.embed(group, faces[2])
r = m_real.search(q103)
check(
    "Deactivated student no longer matched",
    r.student_id != 103,
    f"now returns {'UNKNOWN' if r.is_unknown else r.student_id}",
)

m_real._build([])
check("Rebuilding to empty is safe", m_real.size == 0)
check("Empty index still returns UNKNOWN cleanly", m_real.search(q103).is_unknown)

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
passed = sum(1 for _, ok in results if ok)
total = len(results)
colour = GREEN if passed == total else RED
print(f"{colour}STEP 4 RESULT: {passed}/{total} checks passed{RESET}")
print("=" * 72 + "\n")
sys.exit(0 if passed == total else 1)
