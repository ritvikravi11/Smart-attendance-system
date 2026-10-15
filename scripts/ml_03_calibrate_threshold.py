"""
Threshold calibration -- turn a guessed number into a measured result.

WHY THIS SCRIPT EXISTS
----------------------
Your match threshold is currently 0.50 because a design document proposed it.
That is a weak answer under questioning. This script replaces it with an
experimental result.

THE METHOD (standard biometric evaluation):

  1. Collect embeddings for several people, several images each.
  2. Compute every pairwise similarity, labelled:
       GENUINE  -- both images are the SAME person
       IMPOSTOR -- images are of DIFFERENT people
  3. Plot the two distributions. They should be well separated.
  4. Sweep the threshold and compute, at each value:
       FAR (False Acceptance Rate)  -- impostors wrongly accepted
       FRR (False Rejection Rate)   -- genuines wrongly rejected
  5. Pick the threshold matching your risk preference.

  EER (Equal Error Rate) is where FAR == FRR -- the conventional single-number
  summary of a biometric system's accuracy. Lower is better.

FOR ATTENDANCE, DO NOT SIMPLY PICK THE EER POINT. A false acceptance marks an
absent student present and enables proxy attendance; a false rejection costs
one manual click. The errors are not equally bad, so choose a threshold BIASED
TOWARD LOW FAR -- typically where FAR reaches ~0.1% -- and accept a higher FRR.

Being able to say that in a viva, with a plot to point at, is worth real marks.

USAGE
-----
Organise images as one folder per person:

    calibration_data/
        ravi/     img1.jpg  img2.jpg  img3.jpg ...
        priya/    img1.jpg  img2.jpg ...
        ankit/    ...

Aim for 5+ people and 5+ images each. Then:

    python scripts/ml_03_calibrate_threshold.py calibration_data/

Outputs a report to the terminal plus docs/threshold_calibration.png for your
project document.
"""

import os
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SECRET_KEY", "local-dev-key")

import cv2
import numpy as np

from app.ml.detector import ScrfdDetector
from app.ml.embedder import ArcFaceEmbedder
from app.ml.engine import get_engine
from app.ml.preprocessing import apply_clahe

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def collect_embeddings(root: Path, detector, embedder):
    """Walk the folder tree, embedding one face per image."""
    per_person: dict[str, list[np.ndarray]] = {}

    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        name = person_dir.name
        vectors = []

        for img_path in sorted(person_dir.iterdir()):
            if img_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            image = cv2.imread(str(img_path))
            if image is None:
                print(f"  ! unreadable: {img_path.name}")
                continue

            enhanced = apply_clahe(image)
            face = detector.detect_single(enhanced)
            if face is None:
                n = len(detector.detect(enhanced))
                print(f"  ! {img_path.name}: expected 1 face, found {n}")
                continue

            vectors.append(embedder.embed(enhanced, face))

        if vectors:
            per_person[name] = vectors
            print(f"  {name}: {len(vectors)} embeddings")
        else:
            print(f"  {name}: NO usable images")

    return per_person


def build_score_sets(per_person):
    """Produce genuine and impostor similarity lists."""
    genuine, impostor = [], []

    for name, vectors in per_person.items():
        for a, b in combinations(vectors, 2):
            genuine.append(float(a @ b))

    names = list(per_person)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            for a in per_person[names[i]]:
                for b in per_person[names[j]]:
                    impostor.append(float(a @ b))

    return np.array(genuine), np.array(impostor)


def sweep(genuine, impostor):
    """Compute FAR and FRR across candidate thresholds."""
    thresholds = np.arange(0.0, 1.001, 0.005)
    far = np.array([(impostor >= t).mean() for t in thresholds])
    frr = np.array([(genuine < t).mean() for t in thresholds])
    return thresholds, far, frr


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 1

    print("Loading model ...")
    engine = get_engine()
    engine.load()
    detector = ScrfdDetector()
    embedder = ArcFaceEmbedder()

    print(f"\nCollecting embeddings from {root} ...")
    per_person = collect_embeddings(root, detector, embedder)

    if len(per_person) < 2:
        print("\nNeed at least 2 people to compute impostor scores.")
        return 1

    genuine, impostor = build_score_sets(per_person)
    if len(genuine) == 0 or len(impostor) == 0:
        print("\nNeed at least 2 images per person for genuine pairs.")
        return 1

    thresholds, far, frr = sweep(genuine, impostor)
    eer_index = int(np.argmin(np.abs(far - frr)))
    eer_threshold = float(thresholds[eer_index])
    eer = float((far[eer_index] + frr[eer_index]) / 2)

    # Attendance-appropriate operating point: FAR <= 0.1%
    strict_indices = np.where(far <= 0.001)[0]
    strict_threshold = float(thresholds[strict_indices[0]]) if len(strict_indices) else 1.0
    strict_frr = float(frr[strict_indices[0]]) if len(strict_indices) else 1.0

    # ---- SANITY GUARD ON THE DATASET ITSELF ----
    #
    # A calibration is only as trustworthy as the data behind it, and the most
    # common way to get a confidently WRONG answer is a dataset whose "genuine"
    # pairs are too easy -- several crops of ONE photograph, or frames grabbed
    # seconds apart in identical lighting.
    #
    # Such pairs score ~0.95+, so the genuine and impostor clusters separate
    # perfectly and the sweep happily recommends something like 0.19. Deploy
    # that and strangers start matching enrolled students, because real
    # impostor scores in the wild reach far higher than a tidy test set
    # suggests.
    #
    # Genuine pairs must come from DIFFERENT SESSIONS: different days, lighting,
    # distances, expressions, glasses on and off. That is what a real classroom
    # produces.
    suspicious = genuine.mean() > 0.90 and genuine.std() < 0.05
    floor = 0.35  # never recommend below this regardless of what the data says

    recommended = max(strict_threshold, floor)

    print("\n" + "=" * 66)
    print("THRESHOLD CALIBRATION REPORT")
    print("=" * 66)
    print(f"People:          {len(per_person)}")
    print(f"Genuine pairs:   {len(genuine)}")
    print(f"Impostor pairs:  {len(impostor)}")
    print()
    print(f"GENUINE   mean {genuine.mean():.4f}  std {genuine.std():.4f}  "
          f"min {genuine.min():.4f}")
    print(f"IMPOSTOR  mean {impostor.mean():.4f}  std {impostor.std():.4f}  "
          f"max {impostor.max():.4f}")
    print()
    gap = genuine.min() - impostor.max()
    if gap > 0:
        print(f"CLEAN SEPARATION: gap of {gap:.4f} between the distributions.")
        print(f"  Any threshold in ({impostor.max():.3f}, {genuine.min():.3f}) "
              f"gives zero errors on this data.")
    else:
        print(f"OVERLAP of {-gap:.4f} -- the distributions intersect.")
        print("  Some errors are unavoidable; the threshold trades FAR against FRR.")
    print()
    print(f"EER:                        {eer*100:.2f}% at threshold {eer_threshold:.3f}")
    print(f"FAR<=0.1% operating point:  threshold {strict_threshold:.3f} "
          f"(FRR {strict_frr*100:.2f}%)")

    if suspicious:
        print()
        print("!" * 66)
        print("WARNING: YOUR GENUINE PAIRS LOOK TOO EASY")
        print(f"  Genuine mean {genuine.mean():.3f} with std {genuine.std():.3f} is")
        print("  characteristic of images from a SINGLE capture session -- crops")
        print("  of one photo, or frames seconds apart in identical lighting.")
        print()
        print("  Real-world genuine pairs (different days, lighting, distance,")
        print("  expression) typically score 0.55-0.80, not 0.95+.")
        print()
        print("  The raw sweep therefore UNDERSTATES the threshold you need.")
        print(f"  Recommendation floored at {floor:.2f} for safety.")
        print()
        print("  TO FIX: recapture across several days and lighting conditions,")
        print("  then re-run. That dataset is also what makes your reported")
        print("  accuracy figures credible.")
        print("!" * 66)

    print()
    print("RECOMMENDED for attendance (bias against false acceptance):")
    print(f"  FACE_MATCH_THRESHOLD  = {recommended:.2f}")
    print(f"  FACE_REVIEW_THRESHOLD = {max(recommended - 0.10, 0.0):.2f}")
    if suspicious:
        print("  ^ provisional; re-run on multi-session data before trusting it")
    print("=" * 66)

    strict_threshold = recommended  # used by the plot below

    # ---- Plot ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

        ax1.hist(impostor, bins=50, alpha=0.65, label="Impostor (different people)",
                 color="#d62728", density=True)
        ax1.hist(genuine, bins=50, alpha=0.65, label="Genuine (same person)",
                 color="#2ca02c", density=True)
        ax1.axvline(strict_threshold, color="black", linestyle="--",
                    label=f"recommended {strict_threshold:.2f}")
        ax1.set_xlabel("Cosine similarity")
        ax1.set_ylabel("Density")
        ax1.set_title("Genuine vs impostor score distributions")
        ax1.legend()
        ax1.grid(alpha=0.3)

        ax2.plot(thresholds, far * 100, label="FAR (false acceptance)", color="#d62728")
        ax2.plot(thresholds, frr * 100, label="FRR (false rejection)", color="#2ca02c")
        ax2.axvline(eer_threshold, color="gray", linestyle=":",
                    label=f"EER {eer*100:.2f}%")
        ax2.axvline(strict_threshold, color="black", linestyle="--",
                    label=f"recommended {strict_threshold:.2f}")
        ax2.set_xlabel("Threshold")
        ax2.set_ylabel("Error rate (%)")
        ax2.set_title("Error rates vs threshold")
        ax2.legend()
        ax2.grid(alpha=0.3)

        plt.tight_layout()
        out = Path("docs/threshold_calibration.png")
        plt.savefig(out, dpi=150)
        print(f"\nPlot saved to {out.resolve()}")
        print("Include this figure in your project report.")
    except ImportError:
        print("\n(Install matplotlib to also generate the plot: pip install matplotlib)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
