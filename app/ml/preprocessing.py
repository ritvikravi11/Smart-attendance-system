"""
Image preprocessing and quality assessment.

This is the module that makes yours an IMAGE PROCESSING project rather than a
database project with a camera attached. Every operation here is a classical CV
technique, and each solves a specific, measurable failure of the recognition
pipeline.

Read the concept notes before the code -- the code is short; the reasoning is
the substance.
"""

import cv2
import numpy as np

from app.core.config import settings
from app.ml.interfaces import (
    BoundingBox,
    DetectedFace,
    QualityFailure,
    QualityResult,
)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

# Detection cost scales with pixel count, and SCRFD is trained around this
# scale. Larger inputs cost time without improving accuracy.
MAX_DIMENSION = 640

# Variance-of-Laplacian threshold. See blur_score() for the derivation.
# Calibrate this empirically on your own camera -- see the tuning note below.
DEFAULT_BLUR_THRESHOLD = 40.0

# Mean pixel intensity bounds (0-255). Outside these, the face is silhouetted
# or blown out and carries too little texture to embed reliably.
MIN_BRIGHTNESS = 40.0
MAX_BRIGHTNESS = 215.0


# ---------------------------------------------------------------------------
# CONCEPT 1: COLOUR SPACES -- why we convert to LAB before equalising
# ---------------------------------------------------------------------------
#
# An image arriving from OpenCV is BGR: three channels holding Blue, Green,
# and Red intensity per pixel. (Not RGB -- OpenCV's ordering is a historical
# quirk from 1990s camera hardware, and mixing them up produces images with
# blue skin. It is the single most common OpenCV bug.)
#
# The problem with BGR for our purposes: BRIGHTNESS AND COLOUR ARE ENTANGLED.
# Making a pixel brighter means increasing all three channels. There is no
# single "brightness" number to adjust.
#
# LAB colour space separates them:
#     L = Lightness   (0 = black, 100 = white)
#     A = green <-> magenta
#     B = blue  <-> yellow
#
# So in LAB you can change how BRIGHT something is without changing what
# COLOUR it is. That separation is exactly what we need.
#
# WHY THIS MATTERS FOR FAIRNESS, not just aesthetics:
# If you equalise the B, G, and R channels independently (a very common
# beginner approach), each channel is stretched by a different amount. The
# ratios between them change, so the HUE shifts. Faces come out visibly
# discoloured, and because the distortion depends on the original skin tone,
# it is not uniform across people. Equalising only L leaves A and B untouched,
# so skin tone is preserved exactly.
#
# This is a concrete, defensible fairness decision, and worth stating in your
# report's limitations section alongside the NIST FRVT discussion.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CONCEPT 2: HISTOGRAM EQUALISATION and why CLAHE beats the global version
# ---------------------------------------------------------------------------
#
# A HISTOGRAM counts how many pixels have each intensity value. A photo taken
# in a dim room has a histogram bunched at the low end -- all the pixels are
# dark, so detail is compressed into a narrow band of values.
#
# HISTOGRAM EQUALISATION redistributes those values to span the full 0-255
# range, pulling detail out of the compressed region. Contrast improves.
#
# GLOBAL equalisation computes ONE mapping for the whole image, and that is
# its downfall in a classroom. Picture the real scene: a window on the left
# floods half the frame with daylight; the right half sits in shadow. One
# global mapping must serve both. It typically brightens everything -- so the
# shadowed faces improve slightly while the window side blows out to white,
# destroying the faces that were fine.
#
# CLAHE = Contrast Limited Adaptive Histogram Equalisation. Two ideas:
#
#   ADAPTIVE: the image is divided into tiles (we use 8x8). Each tile gets its
#   OWN histogram and its own mapping. The shadowed region is brightened; the
#   window region is not. Tile boundaries are blended by bilinear interpolation
#   so no seams appear.
#
#   CONTRAST LIMITED: within a tile, if too many pixels share one intensity,
#   naive equalisation amplifies them enormously -- and amplifies the sensor
#   NOISE riding on them, producing a grainy mess. The `clipLimit` caps how
#   much any single histogram bin can be amplified; the excess is redistributed
#   across other bins. This is what makes CLAHE usable on real, noisy footage.
#
# This directly addresses the "handle different lighting conditions"
# requirement (FR-3, NFR-4) with a specific, named, defensible technique.
# ---------------------------------------------------------------------------


def apply_clahe(
    image: np.ndarray, clip_limit: float = 2.0, tile_grid: int = 8
) -> np.ndarray:
    """
    Normalise lighting via CLAHE on the L channel of LAB.

    Args:
        image: BGR image, uint8.
        clip_limit: Contrast cap. 2.0 is a good default. Higher gives more
            aggressive contrast and more amplified noise; above ~4.0 real
            footage starts looking grainy.
        tile_grid: Grid is tile_grid x tile_grid. 8 means 64 tiles. Larger
            adapts to smaller local variations but costs more and can look
            blotchy on smooth regions like a plain wall.

    Returns:
        BGR image with normalised lighting, same shape and dtype.
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Expected a 3-channel BGR image, got shape {image.shape}")

    # BGR -> LAB. Now channel 0 is pure lightness.
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_grid, tile_grid))
    equalised_l = clahe.apply(lightness)

    # Reassemble with the ORIGINAL a and b channels -- colour untouched.
    merged = cv2.merge((equalised_l, a_channel, b_channel))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


# ---------------------------------------------------------------------------
# CONCEPT 3: ASPECT-PRESERVING RESIZE
# ---------------------------------------------------------------------------
#
# A 1920x1080 webcam frame contains ~2 million pixels. SCRFD processes it at
# roughly quadratic cost in dimension, so shrinking to 640px on the long edge
# is close to a 3x speedup with no accuracy loss -- the detector was trained
# at this scale.
#
# The ASPECT RATIO MUST BE PRESERVED. Squashing 1920x1080 into a 640x640 square
# stretches every face horizontally. Since the recognition model learned facial
# geometry from correctly-proportioned faces, a distorted face is out-of-
# distribution input and embeds poorly.
#
# INTERPOLATION -- which algorithm invents the new pixel values:
#   INTER_AREA  : averages over the source region. Correct for SHRINKING;
#                 it is a proper low-pass filter, so it avoids aliasing
#                 (the jagged, shimmering artefacts you get from naive
#                 subsampling).
#   INTER_LINEAR: blends 4 neighbours. Correct for ENLARGING.
#
# Using INTER_LINEAR to shrink is a real and common mistake -- it skips pixels
# rather than averaging them, so fine detail aliases into noise.
# ---------------------------------------------------------------------------


def resize_max_dimension(
    image: np.ndarray, max_dim: int = MAX_DIMENSION
) -> tuple[np.ndarray, float]:
    """
    Shrink so the longest side is at most `max_dim`, preserving aspect ratio.

    Returns:
        (resized_image, scale_factor). KEEP THE SCALE FACTOR. Detection runs on
        the small image and returns small-image coordinates; to draw boxes on
        the original frame you must divide by this factor. Losing track of it
        produces boxes that are correctly shaped but in the wrong place -- a
        classic and very confusing bug.
    """
    height, width = image.shape[:2]
    longest = max(height, width)

    if longest <= max_dim:
        return image, 1.0

    scale = max_dim / longest
    new_size = (int(width * scale), int(height * scale))
    resized = cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)
    return resized, scale


# ---------------------------------------------------------------------------
# CONCEPT 4: BLUR DETECTION via the variance of the Laplacian
# ---------------------------------------------------------------------------
#
# This is the most elegant idea in the module, and it takes one line of code.
#
# The LAPLACIAN is a second-derivative operator. In one dimension, the first
# derivative measures rate of change; the second measures rate of change OF
# the rate of change. Applied to an image, the Laplacian responds strongly
# wherever intensity changes abruptly -- that is, at EDGES.
#
# Now the key observation:
#
#   A SHARP image has crisp edges. The Laplacian output has large positive
#   values on one side of each edge and large negative values on the other.
#   The spread of those values -- their VARIANCE -- is HIGH.
#
#   A BLURRED image has soft, gradual transitions. The Laplacian output is
#   small everywhere. The variance is LOW.
#
# So: variance of the Laplacian is a single scalar measuring image sharpness.
# No training, no model, no parameters. One convolution and a variance.
#
# WHY WE CARE HERE:
# A motion-blurred face carries far less texture than a sharp one. Embedding
# it produces a low-information vector that may match the WRONG student --
# which is worse than not matching at all, because a false acceptance enables
# proxy attendance while a false rejection costs one manual click.
#
# THRESHOLD TUNING -- important, and an easy source of marks:
# The absolute value is camera-dependent. A cheap webcam is softer than a DSLR,
# so its "sharp" score is lower. DO NOT trust the 40.0 default. Run
# scripts/03_tune_blur_threshold.py on YOUR camera, capture ten sharp and ten
# deliberately shaken frames, and pick the value that separates the two
# clusters. That measurement belongs in your report.
# ---------------------------------------------------------------------------


def blur_score(image: np.ndarray) -> float:
    """
    Sharpness estimate. Higher is sharper.

    Typical values on a laptop webcam: 20-40 blurry, 60-200 sharp. Values
    vary by camera -- calibrate rather than assume.
    """
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    # CV_64F because the Laplacian produces NEGATIVE values. Computing it in
    # uint8 would clip everything below zero to 0, discarding half the signal
    # and roughly halving the measured variance.
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def mean_brightness(image: np.ndarray) -> float:
    """
    Average intensity, 0-255.

    Catches the cases CLAHE cannot rescue: a face fully silhouetted against a
    window carries no usable texture, and no amount of contrast stretching
    invents detail that the sensor never captured.
    """
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(np.mean(grey))


# ---------------------------------------------------------------------------
# CONCEPT 5: POSE ESTIMATION from five landmarks
# ---------------------------------------------------------------------------
#
# A face turned sharply away from the camera embeds poorly -- half of it is
# not visible. We can estimate turn (YAW) cheaply from the landmarks we
# already have, with no extra model.
#
# The trick: for a face looking straight ahead, the nose tip sits roughly
# MIDWAY between the two eyes horizontally. As the head turns left, the nose
# shifts toward the left eye; turning right shifts it toward the right eye.
#
# So the nose's horizontal position, expressed as a fraction of the
# inter-ocular distance, is a usable yaw proxy:
#
#     0.5  = facing forward
#     0.0  = extreme turn (nose aligned with one eye)
#     1.0  = extreme turn the other way
#
# It is approximate -- a proper solution would solve the perspective-n-point
# problem against a 3D face model -- but it costs two subtractions and catches
# the profile views that matter. Good engineering is often the cheap 90%
# solution rather than the expensive 99% one.
# ---------------------------------------------------------------------------


def estimate_yaw_ratio(landmarks: np.ndarray) -> float:
    """
    Approximate horizontal head turn from 5-point landmarks.

    Returns a value in [0, 1] where 0.5 means frontal. Values below ~0.25 or
    above ~0.75 indicate a strong profile view.
    """
    left_eye, right_eye, nose = landmarks[0], landmarks[1], landmarks[2]

    eye_distance = float(np.linalg.norm(right_eye - left_eye))
    if eye_distance < 1e-6:  # degenerate; avoid divide-by-zero
        return 0.5

    nose_offset = float(nose[0] - left_eye[0])
    ratio = nose_offset / (right_eye[0] - left_eye[0] + 1e-6)
    return float(np.clip(ratio, 0.0, 1.0))


# ---------------------------------------------------------------------------
# THE QUALITY GATE
# ---------------------------------------------------------------------------


def assess_quality(
    image: np.ndarray,
    face: DetectedFace,
    *,
    strict: bool = False,
) -> QualityResult:
    """
    Decide whether a detected face is good enough to embed.

    WHY REJECT EARLY RATHER THAN EMBED AND HOPE
    -------------------------------------------
    A 40x40-pixel face from the back row carries too little information for a
    reliable 512-d vector. Embed it anyway and you get a vector that is not
    merely useless -- it may sit closer to some OTHER student than to the
    correct one. A false acceptance is far worse than a rejection: the first
    enables proxy attendance, the second costs one manual click.

    Rejecting bad input is also cheaper. The quality gate is a few
    microseconds; embedding is ~100ms.

    Args:
        strict: Enrolment mode. Reference data poisons every future match, so
            enrolment demands roughly double the recognition thresholds.
    """
    multiplier = 2.0 if strict else 1.0
    failures: list[QualityFailure] = []

    # ---- Detector confidence ----
    min_score = settings.face_min_detection_score * (1.15 if strict else 1.0)
    if face.detection_score < min_score:
        failures.append(QualityFailure.LOW_DETECTION_SCORE)

    # ---- Face size ----
    # Pixel width is a direct proxy for information content. Below ~60px there
    # simply are not enough distinct facial features to separate individuals.
    min_width = int(settings.face_min_face_width_px * multiplier)
    face_width = face.bbox.width
    if face_width < min_width:
        failures.append(QualityFailure.TOO_SMALL)

    # ---- Crop to the face region for the remaining checks ----
    # Measuring blur or brightness over the WHOLE frame would be wrong: a sharp
    # background behind a blurred face would pass, and a bright wall would mask
    # a face in shadow. Both must be measured on the face itself.
    h, w = image.shape[:2]
    x1 = max(0, face.bbox.x1)
    y1 = max(0, face.bbox.y1)
    x2 = min(w, face.bbox.x2)
    y2 = min(h, face.bbox.y2)

    if x2 <= x1 or y2 <= y1:  # degenerate box, e.g. fully off-frame
        return QualityResult(
            passed=False,
            failures=(QualityFailure.TOO_SMALL,),
            blur_score=0.0,
            brightness=0.0,
            face_width=face_width,
        )

    crop = image[y1:y2, x1:x2]

    # ---- Sharpness ----
    sharpness = blur_score(crop)
    if sharpness < DEFAULT_BLUR_THRESHOLD * multiplier:
        failures.append(QualityFailure.TOO_BLURRY)

    # ---- Exposure ----
    brightness = mean_brightness(crop)
    if brightness < MIN_BRIGHTNESS:
        failures.append(QualityFailure.TOO_DARK)
    elif brightness > MAX_BRIGHTNESS:
        failures.append(QualityFailure.TOO_BRIGHT)

    # ---- Pose ----
    yaw = estimate_yaw_ratio(face.landmarks)
    yaw_tolerance = 0.20 if strict else 0.30
    if abs(yaw - 0.5) > yaw_tolerance:
        failures.append(QualityFailure.EXTREME_POSE)

    return QualityResult(
        passed=len(failures) == 0,
        failures=tuple(failures),
        blur_score=sharpness,
        brightness=brightness,
        face_width=face_width,
    )


def preprocess_frame(
    image: np.ndarray, *, enhance: bool = True
) -> tuple[np.ndarray, float]:
    """
    Full preprocessing pipeline applied before detection.

    Order matters: resize FIRST, then CLAHE. Reversed, you would run CLAHE over
    ~3x more pixels for an identical result -- pure wasted compute on every
    frame of a 2 fps stream.

    Returns:
        (processed_image, scale_factor) -- divide detected coordinates by the
        scale factor to map them back onto the original frame.
    """
    resized, scale = resize_max_dimension(image)
    if enhance:
        resized = apply_clahe(resized)
    return resized, scale


def scale_bbox(bbox: BoundingBox, scale: float) -> BoundingBox:
    """Map a box from processed-image coordinates back to the original frame."""
    if scale == 1.0:
        return bbox
    inv = 1.0 / scale
    return BoundingBox(
        x1=int(bbox.x1 * inv),
        y1=int(bbox.y1 * inv),
        x2=int(bbox.x2 * inv),
        y2=int(bbox.y2 * inv),
    )
