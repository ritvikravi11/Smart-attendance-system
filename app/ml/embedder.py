"""
Face alignment and embedding extraction.

These two stages are the heart of face recognition. Read the concepts before
the code -- particularly the alignment section, which is the stage beginners
skip and the one that silently costs the most accuracy.
"""

import logging

import numpy as np

from app.ml.engine import FaceEngine, get_engine
from app.ml.interfaces import (
    ALIGNED_FACE_SIZE,
    EMBEDDING_DIM,
    DetectedFace,
    IEmbedder,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CONCEPT 6: FACE ALIGNMENT -- the stage people skip, and why that is costly
# ---------------------------------------------------------------------------
#
# THE PROBLEM
# A detected face crop is whatever the camera happened to capture. The head may
# be tilted 15 degrees. The subject may be near or far, so the face occupies a
# different fraction of the crop. The eyes may sit high or low in the box.
#
# Every one of those variations changes the pixel values fed to the recognition
# network -- WITHOUT the person changing at all. If the network has to learn
# invariance to rotation, scale, AND translation on top of learning identity,
# it wastes capacity and performs worse.
#
# THE SOLUTION
# Warp every face so that specific facial features land on FIXED coordinates in
# a fixed-size output. ArcFace uses this canonical template for 112x112:
#
#       left eye     -> (38.29, 51.69)
#       right eye    -> (73.53, 51.50)
#       nose tip     -> (56.02, 71.73)
#       left mouth   -> (41.55, 92.37)
#       right mouth  -> (70.73, 92.20)
#
# After alignment, EVERY face has its eyes at the same pixels. The network sees
# a standardised input and can spend its entire capacity on identity.
#
# THE MATHEMATICS: a SIMILARITY TRANSFORM
# We need a transform mapping our 5 detected landmarks onto those 5 template
# points. A similarity transform allows exactly three operations:
#
#     rotation      (fixes head tilt)
#     uniform scale (fixes distance from camera)
#     translation   (fixes position in frame)
#
# It deliberately does NOT allow shear or non-uniform scale -- those would
# distort facial geometry, which is precisely the signal we are trying to
# measure. It has 4 degrees of freedom, and 5 point-pairs over-determine it, so
# it is solved in the least-squares sense (Umeyama's method). The result is the
# best rigid fit; small landmark errors get averaged out rather than warping
# the face.
#
# WHY THIS MATTERS SO MUCH -- and it is easy to underestimate
# ArcFace was trained EXCLUSIVELY on aligned faces. Feeding it an unaligned
# crop is out-of-distribution input. Published ablations put the cost of
# skipping alignment at roughly 10-20 percentage points of accuracy.
#
# The failure is insidious because it is not a crash. Recognition still
# "works" -- it just quietly matches the wrong people more often. If your
# system is mysteriously mediocre, this is the first thing to check.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CONCEPT 7: NORMALISATION -- matching the training preprocessing exactly
# ---------------------------------------------------------------------------
#
# Pixel values arrive as integers 0-255. Neural networks train far better on
# small values centred near zero, so the aligned crop is rescaled:
#
#       normalised = (pixel - 127.5) / 127.5      ->  range [-1, +1]
#
# The rule that matters: THIS MUST EXACTLY MATCH THE PREPROCESSING USED DURING
# TRAINING. A model trained on [-1, 1] but fed [0, 1] sees inputs half the
# expected magnitude. Every activation shifts, and the embeddings come out
# subtly wrong -- not obviously broken, just quietly worse. That class of bug
# is extremely hard to find, because nothing errors.
#
# There is also the BGR/RGB question. OpenCV loads BGR; most models train on
# RGB. Getting it backwards swaps the red and blue channels, which visibly
# changes skin tone and measurably degrades accuracy.
#
# InsightFace's recognition wrapper handles both internally, so we do not
# hand-roll them here. But you must KNOW they are happening -- when you swap
# models later, or read a paper, this is where the mismatches hide.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CONCEPT 8: EMBEDDINGS and why COSINE similarity is the right metric
# ---------------------------------------------------------------------------
#
# The recognition network maps a 112x112x3 aligned face (37,632 numbers) to a
# 512-dimensional vector. That vector is the FACE EMBEDDING -- a compact
# numerical summary of identity.
#
# The useful property is geometric: embeddings of the SAME person land close
# together; embeddings of DIFFERENT people land far apart. Recognition then
# reduces to a nearest-neighbour search.
#
# HOW ARCFACE ACHIEVES THIS -- Additive Angular Margin Loss
# During training, ArcFace does not merely ask "is this the right class?". It
# additionally requires each embedding to sit within a margin ANGLE of its
# class centre. Optimising an angular margin means the learned structure is
# fundamentally ANGULAR: identity is encoded in the DIRECTION a vector points,
# not its length.
#
# That single fact determines the correct comparison metric.
#
#   COSINE SIMILARITY measures the angle between two vectors:
#       cos(theta) = (A . B) / (|A| * |B|)
#       +1 = identical direction, 0 = perpendicular, -1 = opposite
#
#   EUCLIDEAN DISTANCE measures straight-line separation, which conflates
#   direction with magnitude. Two embeddings of the same person pointing the
#   same way but with different lengths would appear "far apart". Wrong metric
#   for this model.
#
# THE L2 NORMALISATION TRICK
# If we force every vector to unit length (|A| = |B| = 1), the denominator
# becomes 1 and cosine similarity collapses to a plain DOT PRODUCT:
#
#       similarity = A . B
#
# That is why every embedding is normalised at creation. It turns "compare
# against 1,000 students" into ONE matrix-vector multiplication -- the
# optimisation that makes the in-memory index viable (see matcher.py).
#
# THE PRACTICAL PAYOFF
# 512 floats = 2 KB per student. A thousand students = 2 MB, which fits
# comfortably in RAM. You are not storing photographs; you are storing
# geometry.
# ---------------------------------------------------------------------------


class ArcFaceEmbedder(IEmbedder):
    """
    Alignment + embedding via ArcFace (w600k_r50 from the buffalo_l pack).

    Implements IEmbedder, so services depend on the interface and this class
    can be swapped for a different model -- or faked in tests -- without any
    business logic changing.
    """

    def __init__(self, engine: FaceEngine | None = None):
        self._engine = engine or get_engine()

    # -- Alignment -------------------------------------------------------

    def align(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        """
        Warp a detected face into the canonical 112x112 form.

        Note the inputs: the FULL image plus the landmarks -- not a cropped
        box. Alignment samples from the original pixels, so warping from the
        full frame avoids the quality loss of crop-then-warp (two resampling
        steps instead of one).

        Returns a 112x112x3 BGR image with eyes, nose, and mouth on the
        canonical coordinates.
        """
        aligned = self._engine.align_face(image, face.landmarks)

        if aligned.shape[:2] != (ALIGNED_FACE_SIZE, ALIGNED_FACE_SIZE):
            raise RuntimeError(
                f"Alignment produced {aligned.shape[:2]}, expected "
                f"({ALIGNED_FACE_SIZE}, {ALIGNED_FACE_SIZE})"
            )
        return aligned

    # -- Embedding -------------------------------------------------------

    def embed(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        """
        Produce an L2-normalised 512-d embedding for a detected face.

        Pipeline: align -> normalise pixels -> ArcFace forward pass ->
        L2 normalise.

        L2 normalisation is part of the INTERFACE CONTRACT, not an internal
        detail. The matcher, the database CHECK constraint, and every
        threshold comparison downstream assume unit-length vectors. Returning
        an unnormalised vector here would break all three silently.
        """
        raw = self._engine.embed_aligned(image, face.landmarks)
        return self._l2_normalise(np.asarray(raw, dtype=np.float32))

    def embed_batch(
        self, image: np.ndarray, faces: list[DetectedFace]
    ) -> list[np.ndarray]:
        """
        Embed several faces from one image.

        Currently a loop. Kept as a distinct method because it is the natural
        place to add true batching later -- ONNX Runtime can process a stacked
        batch in one call, which amortises overhead across faces and matters
        for the multi-face classroom photo feature. Having the seam already in
        place means that optimisation touches one method.
        """
        return [self.embed(image, face) for face in faces]

    # -- Helpers ---------------------------------------------------------

    @staticmethod
    def _l2_normalise(vector: np.ndarray) -> np.ndarray:
        """
        Scale a vector to unit length.

        The zero-norm guard matters: a degenerate input could produce an
        all-zero embedding, and dividing by zero would fill the vector with
        NaN. NaN then propagates silently through every similarity comparison
        -- NaN compares False against everything, so the student would simply
        never match, with no error anywhere to explain why.
        """
        norm = float(np.linalg.norm(vector))
        if norm < 1e-10:
            raise ValueError(
                "Embedding has near-zero magnitude; cannot normalise. "
                "This usually indicates a corrupt or blank input image."
            )
        normalised = (vector / norm).astype(np.float32)

        if normalised.shape[0] != EMBEDDING_DIM:
            raise RuntimeError(
                f"Expected a {EMBEDDING_DIM}-d embedding, got {normalised.shape[0]}"
            )
        return normalised


def average_embeddings(embeddings: list[np.ndarray]) -> np.ndarray:
    """
    Combine several embeddings of one person into a single reference vector.

    WHY AVERAGING IS BETTER THAN PICKING ONE IMAGE
    ----------------------------------------------
    Any single photograph carries incidental noise: a particular expression, a
    shadow across one cheek, a momentary blink, sensor grain. Those artefacts
    push the embedding away from the person's "true" position in the space.

    Averaging several embeddings cancels that noise. The random per-image
    deviations point in different directions and partially cancel, while the
    consistent identity signal reinforces. The mean sits closer to the centre
    of that person's cluster than any individual sample does.

    This is why enrolment captures 5-15 images rather than one (FR-2.3, FR-2.6).

    THE RE-NORMALISATION STEP IS NOT OPTIONAL
    -----------------------------------------
    The mean of several unit vectors is NOT itself a unit vector -- it is
    shorter, because the vectors do not point in exactly the same direction.
    Averaging five unit vectors 20 degrees apart yields something around 0.94
    long.

    Skip the re-normalisation and every similarity computed against this
    reference is scaled down by ~6%, so a genuine match at 0.62 reads as 0.58
    and may fall below threshold. A subtle, systematic accuracy loss that
    produces no error message.
    """
    if not embeddings:
        raise ValueError("Cannot average an empty list of embeddings")

    stacked = np.stack(embeddings).astype(np.float32)
    mean = stacked.mean(axis=0)

    norm = float(np.linalg.norm(mean))
    if norm < 1e-10:
        raise ValueError(
            "Averaged embedding has near-zero magnitude. The input images are "
            "probably of different people, cancelling each other out."
        )
    return (mean / norm).astype(np.float32)


def intra_set_similarity(embeddings: list[np.ndarray]) -> float:
    """
    Mean pairwise similarity within a set of enrolment embeddings.

    A CONSISTENCY CHECK FOR ENROLMENT, and a genuinely useful one.

    If someone accidentally captures two different students in one enrolment
    batch, the embeddings will not resemble each other. Averaging them produces
    a meaningless vector sitting between two identities -- which then matches
    NEITHER student reliably, and may match a third person entirely.

    Real enrolment sets of one person typically score above ~0.6. Below that,
    reject the enrolment and ask for a recapture. Catching this at enrolment is
    vastly cheaper than debugging why one student never gets recognised.
    """
    if len(embeddings) < 2:
        return 1.0

    stacked = np.stack(embeddings).astype(np.float32)
    # All vectors are unit length, so the full pairwise similarity matrix is
    # simply the Gram matrix.
    similarity_matrix = stacked @ stacked.T

    # Exclude the diagonal (self-similarity, always 1.0) which would inflate
    # the mean toward 1 and mask genuine inconsistency.
    n = len(embeddings)
    off_diagonal_sum = float(similarity_matrix.sum() - np.trace(similarity_matrix))
    return off_diagonal_sum / (n * (n - 1))
