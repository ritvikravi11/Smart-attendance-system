"""
Live face recognition: enrol yourself, then be recognised.

This is the full pipeline running against your webcam -- detection, alignment,
embedding, matching, and unknown detection. No database required; enrolments
live in memory for the session.

Run:
    python scripts/ml_02_live_recognition.py

Controls:
    e       enrol the largest face (prompts for a name in the terminal)
    r       reset all enrolments
    q       quit
    s       save the current frame

TRY THIS, in order -- it teaches more than reading about it:
  1. Enrol yourself. Watch the confidence sit near 0.95+.
  2. Turn your head slowly. Watch confidence drop as pose degrades.
  3. Move far from the camera. Watch the quality gate reject you at small size.
  4. Have a friend appear WITHOUT enrolling. They should show UNKNOWN in red.
  5. Cover half your face. Watch what happens to the score.

Step 4 is the important one. Confirm with your own eyes that an unenrolled
person is rejected -- that behaviour is what prevents strangers being marked
present, and it is worth demonstrating live during your project defence.
"""

import os
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SECRET_KEY", "local-dev-key")

import cv2
import numpy as np

from app.core.config import settings
from app.ml.detector import ScrfdDetector
from app.ml.embedder import ArcFaceEmbedder, average_embeddings, intra_set_similarity
from app.ml.engine import get_engine
from app.ml.matcher import InMemoryMatcher
from app.ml.preprocessing import apply_clahe, assess_quality

CAMERA_INDEX = 0
WINDOW = "Face Recognition - e:enrol r:reset q:quit"
ENROL_FRAMES = 8  # how many frames to capture per enrolment

GREEN = (0, 200, 0)
AMBER = (0, 180, 255)
RED = (60, 60, 255)
WHITE = (255, 255, 255)
DARK = (40, 40, 40)


def enrol(cap, detector, embedder, name, person_id):
    """
    Capture several frames and build one averaged reference embedding.

    Mirrors the real enrolment flow: multiple images, strict quality gate,
    consistency check, averaging.
    """
    print(f"\nEnrolling '{name}'. Look at the camera and move your head slightly.")
    embeddings = []
    attempts = 0

    while len(embeddings) < ENROL_FRAMES and attempts < ENROL_FRAMES * 6:
        attempts += 1
        ok, frame = cap.read()
        if not ok:
            continue

        enhanced = apply_clahe(frame)
        face = detector.detect_single(enhanced)  # exactly one face required

        preview = frame.copy()
        if face is not None:
            quality = assess_quality(enhanced, face, strict=True)
            b = face.bbox
            colour = GREEN if quality.passed else AMBER
            cv2.rectangle(preview, (b.x1, b.y1), (b.x2, b.y2), colour, 2)
            if quality.passed:
                embeddings.append(embedder.embed(enhanced, face))
                print(f"  captured {len(embeddings)}/{ENROL_FRAMES}")
            else:
                cv2.putText(preview, quality.reason, (b.x1, b.y1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, AMBER, 1)
        else:
            cv2.putText(preview, "Need exactly ONE face", (20, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, RED, 2)

        cv2.putText(preview, f"ENROLLING {name}: {len(embeddings)}/{ENROL_FRAMES}",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, WHITE, 2)
        cv2.imshow(WINDOW, preview)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
        time.sleep(0.08)

    if len(embeddings) < settings.face_min_enrollment_images:
        print(f"  FAILED: only {len(embeddings)} usable frames "
              f"(need {settings.face_min_enrollment_images}). Try better lighting.")
        return None

    # Consistency check: are these all the same person?
    consistency = intra_set_similarity(embeddings)
    print(f"  intra-set consistency: {consistency:.3f}")
    if consistency < 0.6:
        print("  FAILED: frames are inconsistent -- did someone else step in?")
        return None

    reference = average_embeddings(embeddings)
    print(f"  Enrolled '{name}' as id={person_id} from {len(embeddings)} frames.\n")
    return reference


def main() -> int:
    print("Loading face model ...")
    engine = get_engine()
    engine.load()
    detector = ScrfdDetector()
    embedder = ArcFaceEmbedder()
    matcher = InMemoryMatcher()
    print("Ready.\n")

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("ERROR: could not open the webcam. See ml_01_live_detection.py notes.")
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    names: dict[int, str] = {}
    embeddings_store: list[tuple[int, bytes]] = []
    next_id = 1
    frame_times = deque(maxlen=20)

    print("Press 'e' in the window to enrol yourself. 'q' to quit.\n")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        t0 = time.perf_counter()
        enhanced = apply_clahe(frame)
        faces = detector.detect(enhanced)

        for face in faces:
            quality = assess_quality(enhanced, face)
            b = face.bbox

            if not quality.passed:
                cv2.rectangle(frame, (b.x1, b.y1), (b.x2, b.y2), AMBER, 2)
                cv2.putText(frame, quality.reason, (b.x1, b.y1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, AMBER, 1)
                continue

            if matcher.size == 0:
                cv2.rectangle(frame, (b.x1, b.y1), (b.x2, b.y2), WHITE, 2)
                cv2.putText(frame, "press 'e' to enrol", (b.x1, b.y1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1)
                continue

            embedding = embedder.embed(enhanced, face)
            result = matcher.search(embedding)

            if result.is_unknown:
                colour, label = RED, f"UNKNOWN ({result.confidence:.2f})"
            elif result.needs_review:
                colour = AMBER
                label = f"{names.get(result.student_id, '?')}? {result.confidence:.2f} REVIEW"
            else:
                colour = GREEN
                label = f"{names.get(result.student_id, '?')} {result.confidence:.2f}"

            cv2.rectangle(frame, (b.x1, b.y1), (b.x2, b.y2), colour, 2)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(frame, (b.x1, b.y1 - th - 10), (b.x1 + tw + 8, b.y1), colour, -1)
            cv2.putText(frame, label, (b.x1 + 4, b.y1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)

            # Margin exposes ambiguity that confidence alone hides
            if not result.is_unknown and result.margin < 0.15 and matcher.size > 1:
                cv2.putText(frame, f"low margin {result.margin:.2f}",
                            (b.x1, b.y2 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, AMBER, 1)

        frame_times.append(time.perf_counter() - t0)
        fps = 1.0 / (sum(frame_times) / len(frame_times)) if frame_times else 0

        h, w = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 34), DARK, -1)
        cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)
        cv2.putText(frame,
                    f"FPS {fps:4.1f} | enrolled {matcher.size} | "
                    f"threshold {settings.face_match_threshold}",
                    (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 1, cv2.LINE_AA)

        cv2.imshow(WINDOW, frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("e"):
            name = input("Name for this person: ").strip() or f"Person{next_id}"
            reference = enrol(cap, detector, embedder, name, next_id)
            if reference is not None:
                names[next_id] = name
                embeddings_store.append((next_id, reference.tobytes()))
                matcher._build(embeddings_store)
                next_id += 1
        elif key == ord("r"):
            names.clear()
            embeddings_store.clear()
            matcher._build([])
            next_id = 1
            print("All enrolments cleared.\n")

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
