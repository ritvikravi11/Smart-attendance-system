"""
ML layer interfaces and shared data types.

WHY INTERFACES EXIST HERE
-------------------------
This module keeps the detector and embedder contracts explicit
real rather than aspirational.

`RecognitionService` will depend on `IEmbedder`, not on `ArcFaceEmbedder`. Two
consequences follow, and both are worth real money:

1. TESTABILITY. A unit test can inject a fake embedder returning fixed vectors.
   Testing attendance logic then needs no 275MB model, no webcam, and no 3-second
   load. Tests drop from seconds to milliseconds, which is the difference between
   a suite you run constantly and one you avoid.

2. REPLACEABILITY. When you upgrade from ArcFace to whatever supersedes it in
   two years, you write a new class implementing the same interface. Business
   logic does not change. This is also the seam along which the ML layer gets
   extracted into a separate service in the cloud deployment (Topology C).

This is the Dependency Inversion Principle -- the D in SOLID -- and this file
is what you point at when an examiner asks where you applied it.

A NOTE ON DATACLASSES
---------------------
The result types below are frozen dataclasses rather than dicts. A dict has no
schema: `result["confidence"]` versus `result["score"]` is a runtime KeyError
discovered in production. A dataclass gives you attribute access, editor
autocomplete, and an immediate TypeError at construction if a field is missing.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

# ArcFace was trained on 112x112 aligned crops. This is NOT a tunable parameter
# -- feeding any other size produces garbage, because the network's input layer
# has a fixed shape.
ALIGNED_FACE_SIZE = 112

# Output dimensionality of the w600k_r50 recognition model in buffalo_l.
EMBEDDING_DIM = 512


class QualityFailure(StrEnum):
    """
    Why a detected face was rejected before embedding.

    Recording the SPECIFIC reason (rather than a bare False) is what lets the
    UI tell a user "move closer" versus "hold still" versus "face the camera".
    A generic "face rejected" message is useless to the person in front of the
    lens.
    """

    TOO_SMALL = "too_small"
    TOO_BLURRY = "too_blurry"
    LOW_DETECTION_SCORE = "low_detection_score"
    EXTREME_POSE = "extreme_pose"
    TOO_DARK = "too_dark"
    TOO_BRIGHT = "too_bright"


@dataclass(frozen=True)
class BoundingBox:
    """
    Axis-aligned face rectangle in pixel coordinates.

    Convention: (x1, y1) is top-left, (x2, y2) is bottom-right. Image origin is
    top-left, y increases DOWNWARD -- the opposite of school graph paper, and a
    reliable source of upside-down bugs for people meeting image coordinates for
    the first time.
    """

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    def to_list(self) -> list[int]:
        """Serialisation form for JSON responses to the frontend."""
        return [self.x1, self.y1, self.x2, self.y2]


@dataclass(frozen=True)
class DetectedFace:
    """
    One face located in an image, before recognition.

    THE LANDMARKS FIELD IS THE IMPORTANT ONE. It holds five points:
        [0] left eye centre
        [1] right eye centre
        [2] nose tip
        [3] left mouth corner
        [4] right mouth corner

    as a (5, 2) float array. These are what alignment uses. A detector that
    returns only a box (Haar cascades, MediaPipe's simplest mode) forces you to
    run a SECOND landmark model before you can align -- which is the main reason
    SCRFD is used for the production detector while MediaPipe remains an
    optional dependency for experimentation.
    """

    bbox: BoundingBox
    detection_score: float
    landmarks: np.ndarray  # shape (5, 2), dtype float32

    def __post_init__(self) -> None:
        if self.landmarks.shape != (5, 2):
            raise ValueError(
                f"Expected 5 landmark points of (x, y), got {self.landmarks.shape}"
            )


@dataclass(frozen=True)
class QualityResult:
    """Outcome of the quality gate for one detected face."""

    passed: bool
    failures: tuple[QualityFailure, ...]
    blur_score: float
    brightness: float
    face_width: int

    @property
    def reason(self) -> str:
        """Human-readable summary for UI feedback."""
        if self.passed:
            return "OK"
        return ", ".join(f.value for f in self.failures)


@dataclass(frozen=True)
class MatchResult:
    """
    Outcome of comparing one embedding against the enrolled index.

    `student_id` is None when the face is UNKNOWN -- nothing cleared the
    threshold. That nullability is meaningful, not an error state: unknown faces
    are the impostor distribution you need for threshold calibration, and they
    drive the unknown-person alert.
    """

    student_id: int | None
    confidence: float
    is_unknown: bool
    needs_review: bool
    runner_up_confidence: float = 0.0

    @property
    def margin(self) -> float:
        """
        Gap between the best and second-best match.

        A SMALL MARGIN IS A WARNING SIGN even when confidence is high. If the
        top two candidates score 0.62 and 0.61, the model is essentially
        undecided between two people -- typically identical twins or siblings.
        Confidence alone would happily accept that match. Margin is what
        exposes the ambiguity, and it is a genuinely useful diagnostic that
        most implementations never compute.
        """
        return self.confidence - self.runner_up_confidence


# ---------------------------------------------------------------------------
# INTERFACES
# ---------------------------------------------------------------------------


class IFaceDetector(ABC):
    """Locates faces in an image."""

    @abstractmethod
    def detect(self, image: np.ndarray) -> list[DetectedFace]:
        """
        Find all faces in a BGR image.

        Returns an empty list when no faces are present. That is a normal
        outcome, not an error -- most frames from a classroom camera contain
        nothing worth reporting, and raising here would make the 2 fps loop
        an exception storm.
        """
        ...


class IEmbedder(ABC):
    """Converts a detected face into a comparable vector."""

    @abstractmethod
    def embed(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        """
        Produce an L2-normalised 512-d float32 embedding.

        L2 normalisation is part of the contract, not an implementation
        detail: the matcher's single matrix-multiply depends on every vector
        being unit length. An implementation returning unnormalised vectors
        would silently break every threshold comparison downstream.
        """
        ...


class IMatcher(ABC):
    """Searches enrolled embeddings for the closest match."""

    @abstractmethod
    def search(
        self, embedding: np.ndarray, candidate_ids: set[int] | None = None
    ) -> MatchResult:
        """
        Find the best match for a query embedding.

        `candidate_ids` restricts the search to specific students -- normally
        the roster enrolled in the current offering. This is both a ~20x
        speedup and, more importantly, a large false-positive reduction: a
        lookalike from another department simply is not a candidate.
        """
        ...

    @abstractmethod
    def rebuild(self) -> int:
        """Reload the index from the database. Returns the entry count."""
        ...

    @property
    @abstractmethod
    def size(self) -> int:
        """Number of enrolled students currently in the index."""
        ...
