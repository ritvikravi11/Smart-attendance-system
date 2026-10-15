"""
Live webcam face detection with bounding boxes and landmarks.

THIS IS THE SCRIPT THAT MAKES THE PROJECT FEEL REAL. Run it and you get a
window showing your own face with a box drawn around it in real time.

Run:
    python scripts/ml_01_live_detection.py

Controls:
    q       quit
    c       toggle CLAHE preprocessing on/off
    l       toggle landmark display
    s       save the current frame to captures/
    SPACE   pause / resume

WINDOWS NOTES
-------------
cv2.CAP_DSHOW forces the DirectShow backend. Without it, Windows webcam
initialisation is often slow (5+ seconds) or silently returns black frames.
This one flag saves a lot of confusion.

If the window does not appear:
  - Settings > Privacy & security > Camera > allow desktop apps
  - Close Zoom / Teams / OBS; only one process can hold the camera
  - Try CAMERA_INDEX = 1 if you have multiple cameras
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

from app.ml.detector import ScrfdDetector
from app.ml.engine import get_engine
from app.ml.preprocessing import apply_clahe, assess_quality, estimate_yaw_ratio

CAMERA_INDEX = 0
WINDOW = "Face Detection - q:quit c:CLAHE l:landmarks s:save SPACE:pause"

# BGR colours
GREEN = (0, 200, 0)
AMBER = (0, 180, 255)
RED = (60, 60, 255)
WHITE = (255, 255, 255)
DARK = (40, 40, 40)


def draw_face(frame, face, quality, show_landmarks):
    """Draw one detection with its quality verdict."""
    b = face.bbox
    colour = GREEN if quality.passed else AMBER

    cv2.rectangle(frame, (b.x1, b.y1), (b.x2, b.y2), colour, 2)

    label = f"{face.detection_score:.2f}"
    if not quality.passed:
        label += f" | {quality.reason}"

    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(frame, (b.x1, b.y1 - th - 8), (b.x1 + tw + 6, b.y1), colour, -1)
    cv2.putText(frame, label, (b.x1 + 3, b.y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    # Diagnostics under the box -- these are the numbers that teach you what
    # the quality gate is reacting to.
    yaw = estimate_yaw_ratio(face.landmarks)
    info = f"w:{b.width}px blur:{quality.blur_score:.0f} yaw:{yaw:.2f}"
    cv2.putText(frame, info, (b.x1, b.y2 + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1, cv2.LINE_AA)

    if show_landmarks:
        # Landmark order: left eye, right eye, nose, left mouth, right mouth
        colours = [(255, 100, 0), (255, 100, 0), (0, 255, 255), (255, 0, 255), (255, 0, 255)]
        for (x, y), c in zip(face.landmarks.astype(int), colours):
            cv2.circle(frame, (x, y), 3, c, -1)
        # Line between the eyes makes head tilt visually obvious
        le, re = face.landmarks[0].astype(int), face.landmarks[1].astype(int)
        cv2.line(frame, tuple(le), tuple(re), (255, 100, 0), 1)


def draw_hud(frame, fps, n_faces, n_pass, clahe_on, paused):
    """Status bar across the top."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 34), DARK, -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

    status = (
        f"FPS {fps:4.1f} | faces {n_faces} ({n_pass} usable) | "
        f"CLAHE {'ON ' if clahe_on else 'OFF'}"
    )
    if paused:
        status += " | PAUSED"
    cv2.putText(frame, status, (10, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 1, cv2.LINE_AA)


def main() -> int:
    print("Loading face model (first run downloads ~275MB) ...")
    engine = get_engine()
    engine.load()
    detector = ScrfdDetector()
    print(f"Model ready: {engine.health()}")

    # CAP_DSHOW is the Windows fix. On Linux/macOS it is ignored harmlessly.
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("\nERROR: could not open the webcam.")
        print("  - Check Windows camera privacy settings")
        print("  - Close any other app using the camera")
        print(f"  - Try a different CAMERA_INDEX (currently {CAMERA_INDEX})")
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    captures_dir = Path(__file__).resolve().parents[1] / "captures"
    captures_dir.mkdir(exist_ok=True)

    clahe_on = True
    show_landmarks = True
    paused = False
    frame_times = deque(maxlen=30)
    last_frame = None

    print("\nRunning. Press 'q' in the window to quit.\n")

    while True:
        if not paused:
            ok, frame = cap.read()
            if not ok:
                print("Frame grab failed; camera may have disconnected.")
                break
            last_frame = frame.copy()
        else:
            frame = last_frame.copy()

        t0 = time.perf_counter()

        # Detection input is optionally CLAHE-enhanced, but boxes are drawn on
        # the ORIGINAL frame -- so you see the true image with correct
        # coordinates, not the processed one.
        detect_input = apply_clahe(frame) if clahe_on else frame
        faces = detector.detect(detect_input)

        n_pass = 0
        for face in faces:
            quality = assess_quality(detect_input, face)
            n_pass += int(quality.passed)
            draw_face(frame, face, quality, show_landmarks)

        frame_times.append(time.perf_counter() - t0)
        fps = 1.0 / (sum(frame_times) / len(frame_times)) if frame_times else 0.0

        draw_hud(frame, fps, len(faces), n_pass, clahe_on, paused)
        cv2.imshow(WINDOW, frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("c"):
            clahe_on = not clahe_on
            print(f"CLAHE {'enabled' if clahe_on else 'disabled'}")
        elif key == ord("l"):
            show_landmarks = not show_landmarks
        elif key == ord(" "):
            paused = not paused
        elif key == ord("s"):
            path = captures_dir / f"capture_{int(time.time())}.jpg"
            cv2.imwrite(str(path), frame)
            print(f"Saved {path}")

    cap.release()
    cv2.destroyAllWindows()
    print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
