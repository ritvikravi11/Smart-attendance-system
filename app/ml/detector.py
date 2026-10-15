"""
Face detection using SCRFD.

CONCEPT: WHAT A FACE DETECTOR ACTUALLY DOES
-------------------------------------------
The naive mental model is "the model scans the image looking for face-shaped
things". Closer to the truth, for a modern single-shot detector:

1. The image passes through a convolutional backbone, producing a stack of
   FEATURE MAPS at several resolutions. Early layers respond to edges and
   textures; deeper layers respond to composite structures like eye-pairs.

2. Multiple resolutions matter because faces appear at wildly different sizes.
   A face 30px across and one 300px across cannot both be found well at one
   scale. This multi-scale arrangement is called a feature pyramid.

3. At every position in every feature map, the network predicts:
     - a confidence that a face is centred there
     - offsets refining a box around it
     - five landmark positions
   All in ONE forward pass -- hence "single shot", as opposed to older
   two-stage detectors that proposed regions and then classified them.

4. Thousands of overlapping boxes survive. NON-MAXIMUM SUPPRESSION prunes
   them: sort by confidence, keep the best, discard anything overlapping it
   too heavily, repeat. Without NMS you would get twenty boxes per face.

WHY SCRFD SPECIFICALLY
----------------------
SCRFD = Sample and Computation Redistribution for Face Detection. Its
contribution is redistributing computation across the pyramid levels according
to where faces actually occur in real data, rather than spending equally
everywhere. Result: comparable accuracy to heavier detectors at a fraction of
the compute.

For us the decisive feature is that it returns FIVE LANDMARKS alongside each
box. Alignment needs them, and a box-only detector (Haar cascades, MediaPipe's
basic mode) forces a second model into the pipeline. One model instead of two
is fewer dependencies and fewer failure modes -- the reasoning behind decision
the detector abstraction used by the recognition pipeline.
"""

import logging

import numpy as np

from app.ml.engine import FaceEngine, get_engine
from app.ml.interfaces import BoundingBox, DetectedFace, IFaceDetector

logger = logging.getLogger(__name__)


class ScrfdDetector(IFaceDetector):
    """
    SCRFD detection via InsightFace.

    Implements IFaceDetector, so services depend on the interface and this
    class can be swapped or faked freely.
    """

    def __init__(self, engine: FaceEngine | None = None):
        # Default to the shared engine, but allow injection. A test can pass
        # a stub engine and never touch the real model.
        self._engine = engine or get_engine()

    def detect(self, image: np.ndarray) -> list[DetectedFace]:
        """
        Find every face in a BGR image.

        Returns faces sorted LARGEST FIRST. Ordering matters for usability:
        during enrolment we require exactly one face, and if several are
        present the largest is almost always the intended subject standing
        closest to the camera. Sorting makes that heuristic available without
        extra work at the call site.

        Returns [] when nothing is found -- a normal outcome for most frames,
        not an error.
        """
        if image is None or image.size == 0:
            raise ValueError("Cannot detect faces in an empty image")
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(f"Expected a 3-channel BGR image, got {image.shape}")

        # detect_only(), NOT analyse(). The full pipeline would also run
        # ArcFace over every face -- measured at 7.9x slower on a 6-face
        # image, for embeddings this method does not return and the caller
        # did not ask for. Embedding happens later, and only for faces that
        # clear the quality gate.
        bboxes, landmarks = self._engine.detect_only(image)

        if bboxes is None or len(bboxes) == 0:
            return []

        h, w = image.shape[:2]
        detected: list[DetectedFace] = []

        for i in range(len(bboxes)):
            # SCRFD returns [x1, y1, x2, y2, score] per row.
            x1, y1, x2, y2, score = bboxes[i]

            # Clamp to image bounds. A detection near the edge can legitimately
            # return coordinates slightly outside the frame, and slicing with
            # those produces an empty crop.
            bbox = BoundingBox(
                x1=max(0, int(x1)),
                y1=max(0, int(y1)),
                x2=min(w, int(x2)),
                y2=min(h, int(y2)),
            )

            # Degenerate boxes can appear at frame edges; drop them rather
            # than letting a zero-area crop propagate downstream.
            if bbox.width <= 0 or bbox.height <= 0:
                continue

            detected.append(
                DetectedFace(
                    bbox=bbox,
                    detection_score=float(score),
                    # Copy so callers cannot mutate InsightFace's buffers.
                    landmarks=np.asarray(landmarks[i], dtype=np.float32).copy(),
                )
            )

        detected.sort(key=lambda f: f.bbox.area, reverse=True)
        return detected

    def detect_single(self, image: np.ndarray) -> DetectedFace | None:
        """
        Find exactly one face, or return None.

        Used during ENROLMENT, where ambiguity is unacceptable: if two people
        are in the frame, we cannot know whose identity is being registered,
        and guessing would silently enrol the wrong student's face against a
        roll number. Returning None forces the caller to reject the image.

        Note the asymmetry with recognition, which happily handles many faces.
        Enrolment is strict; recognition is permissive. Different jobs,
        different rules.
        """
        faces = self.detect(image)
        if len(faces) != 1:
            logger.debug("Expected exactly 1 face for enrolment, found %d", len(faces))
            return None
        return faces[0]
