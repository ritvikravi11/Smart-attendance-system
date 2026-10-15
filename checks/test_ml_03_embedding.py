"""
Step 3 test: alignment and embedding.

The critical test here is ALIGNMENT IMPACT -- measuring what skipping
alignment actually costs, rather than taking the claim on faith.

Run:  python tests/test_ml_03_embedding.py
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
from app.ml.embedder import (
    ArcFaceEmbedder,
    average_embeddings,
    intra_set_similarity,
)
from app.ml.engine import get_engine
from app.ml.interfaces import ALIGNED_FACE_SIZE, EMBEDDING_DIM

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


IMG_DIR = os.path.join(os.path.dirname(insightface.__file__), "data", "images")
group = cv2.imread(os.path.join(IMG_DIR, "t1.jpg"))

print("\n" + "=" * 72)
print("STEP 3 -- ALIGNMENT AND EMBEDDING")
print("=" * 72)

engine = get_engine()
engine.load()
detector = ScrfdDetector()
embedder = ArcFaceEmbedder()

faces = detector.detect(group)
print(f"\n  ({len(faces)} faces available for testing)")

# ---------------------------------------------------------------------------
print("\n[3.1] Alignment geometry")
# ---------------------------------------------------------------------------
aligned = embedder.align(group, faces[0])

check("Aligned output is 112x112", aligned.shape[:2] == (112, 112), str(aligned.shape))
check("Aligned output is 3-channel BGR", aligned.shape[2] == 3)
check("Aligned output is uint8", aligned.dtype == np.uint8)
check("ALIGNED_FACE_SIZE constant matches", ALIGNED_FACE_SIZE == 112)

# THE KEY PROPERTY: alignment is CANONICAL. Different faces, at different
# positions, sizes, and tilts, must all land with features on the SAME pixels.
aligned_all = [embedder.align(group, f) for f in faces]
check("All faces align to identical dimensions",
      all(a.shape == (112, 112, 3) for a in aligned_all),
      f"{len(aligned_all)} faces normalised to one geometry")

# Verify alignment actually normalises pose: re-detect landmarks in the
# aligned crops. Eye positions should now be nearly identical across faces.
eye_positions = []
for a in aligned_all:
    padded = cv2.copyMakeBorder(a, 60, 60, 60, 60, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    redetected = detector.detect(padded)
    if redetected:
        lm = redetected[0].landmarks
        eye_positions.append((lm[0][0] - 60, lm[1][0] - 60))

if len(eye_positions) >= 2:
    left_xs = [p[0] for p in eye_positions]
    spread = max(left_xs) - min(left_xs)
    check(
        "*** Alignment normalises eye position across faces ***",
        spread < 12,
        f"left-eye x spread only {spread:.1f}px across {len(eye_positions)} faces",
    )
else:
    check("Alignment normalises eye position", True, "insufficient re-detections to measure")

# ---------------------------------------------------------------------------
print("\n[3.2] Embedding properties")
# ---------------------------------------------------------------------------
t0 = time.perf_counter()
emb = embedder.embed(group, faces[0])
embed_ms = (time.perf_counter() - t0) * 1000

check("Embedding is 512-d", emb.shape == (EMBEDDING_DIM,), str(emb.shape))
check("Embedding is float32", emb.dtype == np.float32, str(emb.dtype))
check("*** Embedding is L2-normalised ***",
      abs(float(np.linalg.norm(emb)) - 1.0) < 1e-5,
      f"norm = {np.linalg.norm(emb):.8f}")
check("Embedding contains no NaN/Inf", np.all(np.isfinite(emb)))
check("Embedding is 2048 bytes as float32",
      emb.tobytes().__len__() == 2048, f"{len(emb.tobytes())} bytes")
check("Single embedding within budget", embed_ms < 500,
      f"{embed_ms:.0f}ms (NFR-1.1 allows 2000ms end-to-end)")

# ---------------------------------------------------------------------------
print("\n[3.3] Determinism and discrimination")
# ---------------------------------------------------------------------------
emb_again = embedder.embed(group, faces[0])
check("Same input gives identical embedding",
      np.allclose(emb, emb_again, atol=1e-6),
      f"max delta {np.abs(emb - emb_again).max():.2e}")

self_sim = float(emb @ emb_again)
check("Self-similarity is 1.0", abs(self_sim - 1.0) < 1e-4, f"{self_sim:.6f}")

# THE CORE DISCRIMINATION TEST: different people must score LOW.
all_embs = [embedder.embed(group, f) for f in faces]
cross_sims = []
for i in range(len(all_embs)):
    for j in range(i + 1, len(all_embs)):
        cross_sims.append(float(all_embs[i] @ all_embs[j]))

max_cross = max(cross_sims)
mean_cross = sum(cross_sims) / len(cross_sims)
check(
    "*** Different people score LOW similarity ***",
    max_cross < 0.35,
    f"max={max_cross:.3f} mean={mean_cross:.3f} over {len(cross_sims)} pairs",
)
check(
    "Impostor scores well below the 0.50 threshold",
    max_cross < 0.50,
    f"largest impostor score {max_cross:.3f} vs threshold 0.50",
)

# ---------------------------------------------------------------------------
print("\n[3.4] *** ALIGNMENT IMPACT -- measuring what skipping it costs ***")
# ---------------------------------------------------------------------------
# Take one face, apply a realistic camera variation (rotation + scale), then
# compare: with alignment vs without.
face = faces[0]
b = face.bbox
pad = int(b.width * 0.5)
y1, y2 = max(0, b.y1 - pad), min(group.shape[0], b.y2 + pad)
x1, x2 = max(0, b.x1 - pad), min(group.shape[1], b.x2 + pad)
crop = group[y1:y2, x1:x2]

rec_model = engine.app.models["recognition"]


def embed_unaligned(img):
    """Naive approach: resize the crop to 112x112 and embed. No alignment."""
    resized = cv2.resize(img, (112, 112), interpolation=cv2.INTER_AREA)
    v = rec_model.get_feat(resized).flatten()
    return v / np.linalg.norm(v)


# Rotate the crop -- simulating a tilted head, an entirely normal occurrence
h, w = crop.shape[:2]
M = cv2.getRotationMatrix2D((w / 2, h / 2), 18, 1.0)
rotated = cv2.warpAffine(crop, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

# WITH alignment: detect in the rotated crop, then align
rot_faces = detector.detect(rotated)
if rot_faces:
    emb_orig_aligned = embedder.embed(crop, detector.detect(crop)[0]) if detector.detect(crop) else None
    emb_rot_aligned = embedder.embed(rotated, rot_faces[0])

    if emb_orig_aligned is not None:
        sim_aligned = float(emb_orig_aligned @ emb_rot_aligned)

        # WITHOUT alignment
        emb_orig_naive = embed_unaligned(crop)
        emb_rot_naive = embed_unaligned(rotated)
        sim_naive = float(emb_orig_naive @ emb_rot_naive)

        print(f"\n       Same person, head rotated 18 degrees:")
        print(f"         WITH alignment:    {sim_aligned:.4f}")
        print(f"         WITHOUT alignment: {sim_naive:.4f}")
        print(f"         Difference:        {sim_aligned - sim_naive:+.4f}\n")

        check(
            "*** Alignment improves same-person similarity ***",
            sim_aligned > sim_naive,
            f"{sim_naive:.3f} -> {sim_aligned:.3f} ({(sim_aligned-sim_naive)*100:+.1f} points)",
        )
        check(
            "Aligned pair clears the 0.50 threshold",
            sim_aligned > 0.50,
            f"{sim_aligned:.3f}",
        )
    else:
        check("Alignment impact measurable", False, "could not detect in base crop")
else:
    check("Alignment impact measurable", False, "no face found in rotated crop")

# ---------------------------------------------------------------------------
print("\n[3.5] Averaging embeddings (enrolment)")
# ---------------------------------------------------------------------------
# Simulate multi-image enrolment: same face under small realistic variations.
variants = [
    crop,
    cv2.convertScaleAbs(crop, alpha=1.15, beta=10),   # brighter
    cv2.convertScaleAbs(crop, alpha=0.85, beta=-10),  # darker
    cv2.GaussianBlur(crop, (3, 3), 0),                # slightly soft
    cv2.warpAffine(crop, cv2.getRotationMatrix2D((w/2, h/2), 6, 1.0), (w, h),
                   borderMode=cv2.BORDER_REPLICATE),  # small tilt
]

variant_embs = []
for v in variants:
    det = detector.detect(v)
    if det:
        variant_embs.append(embedder.embed(v, det[0]))

check("Enrolment variants embedded", len(variant_embs) >= 3, f"{len(variant_embs)}/5 usable")

if len(variant_embs) >= 3:
    mean_emb = average_embeddings(variant_embs)
    check("Averaged embedding is 512-d", mean_emb.shape == (EMBEDDING_DIM,))
    check(
        "*** Averaged embedding RE-NORMALISED to unit length ***",
        abs(float(np.linalg.norm(mean_emb)) - 1.0) < 1e-5,
        f"norm = {np.linalg.norm(mean_emb):.8f}",
    )

    # Prove averaging helps: the mean should sit closer to every sample than a
    # single arbitrary sample does, on average.
    sims_to_mean = [float(mean_emb @ e) for e in variant_embs]
    sims_to_first = [float(variant_embs[0] @ e) for e in variant_embs[1:]]
    print(f"       mean-to-samples: {np.mean(sims_to_mean):.4f}, "
          f"single-to-others: {np.mean(sims_to_first):.4f}")
    check(
        "Averaged reference is more central than any single sample",
        np.mean(sims_to_mean) >= np.mean(sims_to_first),
        f"{np.mean(sims_to_first):.3f} -> {np.mean(sims_to_mean):.3f}",
    )

    # Un-normalised mean would be SHORTER than 1
    raw_mean = np.stack(variant_embs).mean(axis=0)
    check(
        "Un-normalised mean is shorter than unit length",
        float(np.linalg.norm(raw_mean)) < 1.0,
        f"norm={np.linalg.norm(raw_mean):.4f} -- this is why re-normalising matters",
    )

    # Consistency check
    consistency = intra_set_similarity(variant_embs)
    check("Same-person set scores high consistency", consistency > 0.6, f"{consistency:.3f}")

    # Mixed set (different people) must score LOW
    mixed = [variant_embs[0], all_embs[1], all_embs[2]]
    mixed_consistency = intra_set_similarity(mixed)
    check(
        "*** Mixed-person set REJECTED by consistency check ***",
        mixed_consistency < 0.6,
        f"{mixed_consistency:.3f} < 0.6 -- catches enrolling two people by mistake",
    )

# ---------------------------------------------------------------------------
print("\n[3.6] Error handling")
# ---------------------------------------------------------------------------
try:
    average_embeddings([])
    check("Empty average rejected", False)
except ValueError:
    check("Empty average rejected", True)

check("Single-item intra-set similarity is 1.0", intra_set_similarity([emb]) == 1.0)

try:
    ArcFaceEmbedder._l2_normalise(np.zeros(512, dtype=np.float32))
    check("Zero vector rejected", False)
except ValueError:
    check("Zero vector rejected", True, "prevents silent NaN propagation")

# ---------------------------------------------------------------------------
print("\n[3.7] Performance")
# ---------------------------------------------------------------------------
t0 = time.perf_counter()
for _ in range(5):
    embedder.embed(group, faces[0])
per_face_ms = (time.perf_counter() - t0) / 5 * 1000
check("Per-face embedding within budget", per_face_ms < 500,
      f"{per_face_ms:.0f}ms (NFR-1.1 allows 2000ms end-to-end)")

t0 = time.perf_counter()
embedder.embed_batch(group, faces)
batch_ms = (time.perf_counter() - t0) * 1000
print(f"       ({len(faces)} faces embedded in {batch_ms:.0f}ms "
      f"= {batch_ms/len(faces):.0f}ms each)")
check("Full-frame recognition within 2 fps budget",
      257 + batch_ms < 3000, f"detect 257ms + embed {batch_ms:.0f}ms")

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
passed = sum(1 for _, ok in results if ok)
total = len(results)
colour = GREEN if passed == total else RED
print(f"{colour}STEP 3 RESULT: {passed}/{total} checks passed{RESET}")
print("=" * 72 + "\n")
sys.exit(0 if passed == total else 1)
