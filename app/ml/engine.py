"""
Model lifecycle: loading ONNX face models exactly once.

PURE ONNX IMPLEMENTATION
-------------------------
This version loads the SCRFD detector and ArcFace recognition model directly
from .onnx files using onnxruntime, WITHOUT requiring the insightface Python
package. This avoids the C++ build tools requirement on Windows.

The API surface (detect_only, embed_aligned, align_face) is identical to the
original insightface-based engine, so detector.py and embedder.py need no
changes at all.

WHY A SINGLETON -- same reasoning as before
--------------------------------------------
Model load time:  ~2 seconds
Resident memory:  ~300 MB
Inference time:   ~50-100 ms

If the model were loaded per request, every recognition would take 2+ seconds
instead of 0.1, and two concurrent requests would consume 600MB. Loading once
at startup is the only viable approach (NFR-1.4).
"""

import logging
import os
import threading
import time
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SCRFD post-processing helpers
# ---------------------------------------------------------------------------

# The five ArcFace alignment reference points for a 112x112 crop.
# These are the canonical positions where eyes, nose, and mouth corners
# should land after alignment. ArcFace was trained exclusively on faces
# aligned to these coordinates.
ARCFACE_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    """Convert distances from anchor points to bounding box coordinates."""
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    """Convert distances from anchor points to landmark coordinates."""
    num_points = distance.shape[1] // 2
    kps = np.zeros((distance.shape[0], num_points, 2), dtype=distance.dtype)
    for i in range(num_points):
        kps[:, i, 0] = points[:, 0] + distance[:, 2 * i]
        kps[:, i, 1] = points[:, 1] + distance[:, 2 * i + 1]
    return kps


def _nms(dets: np.ndarray, threshold: float = 0.4) -> list[int]:
    """Non-maximum suppression. Returns indices of kept detections."""
    x1 = dets[:, 0]
    y1 = dets[:, 1]
    x2 = dets[:, 2]
    y2 = dets[:, 3]
    scores = dets[:, 4]

    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []

    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(iou <= threshold)[0]
        order = order[inds + 1]

    return keep


class FaceEngine:
    """
    Wraps SCRFD + ArcFace ONNX models with explicit lifecycle control.

    Pure onnxruntime implementation — no insightface package required.
    """

    _instance: "FaceEngine | None" = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        self._det_session: ort.InferenceSession | None = None
        self._rec_session: ort.InferenceSession | None = None
        self._model_name: str = settings.face_model_name
        self._model_dir: str = settings.face_model_dir
        self._load_time_seconds: float = 0.0
        self._ready = False
        self._det_size: tuple[int, int] = (640, 640)

        # SCRFD architecture constants (for det_10g model)
        self._feat_stride_fpn = [8, 16, 32]
        self._num_anchors = 2
        self._det_thresh = 0.5

    # -- Singleton access ------------------------------------------------

    @classmethod
    def instance(cls) -> "FaceEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -- Lifecycle -------------------------------------------------------

    def load(self, *, det_size: tuple[int, int] = (640, 640)) -> None:
        """
        Load ONNX model weights into memory. Called once, from the startup hook.

        FIRST RUN: You must download the models first with:
            python -m scripts.download_models
        """
        if self._ready:
            logger.debug("Face engine already loaded; skipping")
            return

        with self._lock:
            if self._ready:
                return

            self._det_size = det_size

            det_path = os.path.join(self._model_dir, "det_10g.onnx")
            rec_path = os.path.join(self._model_dir, "w600k_r50.onnx")

            # Verify files exist
            for path, name in [(det_path, "SCRFD detector"), (rec_path, "ArcFace recognizer")]:
                if not os.path.isfile(path):
                    raise FileNotFoundError(
                        f"{name} not found at {path}. "
                        "Run 'python -m scripts.download_models' to download the models."
                    )

            logger.info("Loading face models from %s ...", self._model_dir)
            start = time.perf_counter()

            # Session options for CPU inference
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 2
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            providers = ["CPUExecutionProvider"]

            self._det_session = ort.InferenceSession(
                det_path, sess_options=opts, providers=providers
            )
            self._rec_session = ort.InferenceSession(
                rec_path, sess_options=opts, providers=providers
            )

            self._load_time_seconds = time.perf_counter() - start
            self._ready = True

            logger.info(
                "Face models loaded in %.2fs (detector: %s, recognizer: %s)",
                self._load_time_seconds,
                os.path.basename(det_path),
                os.path.basename(rec_path),
            )

    def unload(self) -> None:
        """Release the models. Called at shutdown."""
        with self._lock:
            self._det_session = None
            self._rec_session = None
            self._ready = False
            logger.info("Face models released")

    # -- Access ----------------------------------------------------------

    @property
    def app(self) -> Any:
        """Compatibility property. Returns self for engine.app.models style access."""
        if not self._ready:
            raise RuntimeError(
                "Face engine is not loaded. Call load() during application "
                "startup before serving requests."
            )
        return self

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_version(self) -> str:
        """Version string stored alongside every embedding."""
        return f"onnx-{self._model_name}"

    def health(self) -> dict[str, Any]:
        """Diagnostic payload for the /recognition/health endpoint."""
        return {
            "ready": self._ready,
            "model_name": self._model_name,
            "model_version": self.model_version if self._ready else None,
            "load_time_seconds": round(self._load_time_seconds, 2),
            "modules": ["detection", "recognition"] if self._ready else [],
        }

    # -- Detection -------------------------------------------------------

    def _preprocess_det(self, image: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        """
        Resize and normalize image for SCRFD detector input.

        Returns (blob, scale, padding).
        """
        h, w = image.shape[:2]
        target_h, target_w = self._det_size

        # Letterbox resize: fit into det_size maintaining aspect ratio
        scale = min(target_h / h, target_w / w)
        new_h, new_w = int(h * scale), int(w * scale)

        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Pad to det_size with zeros
        padded = np.zeros((target_h, target_w, 3), dtype=np.float32)
        padded[:new_h, :new_w, :] = resized.astype(np.float32)

        # Normalize: (pixel - 127.5) / 128.0 -- SCRFD training preprocessing
        padded = (padded - 127.5) / 128.0

        # HWC -> NCHW
        blob = padded.transpose(2, 0, 1)[np.newaxis, ...]
        return blob.astype(np.float32), scale, (new_h, new_w)

    def _postprocess_det(
        self, outputs: list[np.ndarray], scale: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Post-process SCRFD detector outputs into bboxes and landmarks.

        SCRFD det_10g outputs 9 tensors: for each of 3 FPN strides (8, 16, 32),
        it produces (scores, bbox_deltas, kps_deltas).

        Returns:
            bboxes: (N, 5) array of [x1, y1, x2, y2, score]
            landmarks: (N, 5, 2) array of landmark coordinates
        """
        scores_list = []
        bboxes_list = []
        kps_list = []

        input_h, input_w = self._det_size

        for idx, stride in enumerate(self._feat_stride_fpn):
            # Each stride produces 3 outputs: scores, bbox_preds, kps_preds
            scores = outputs[idx]
            bbox_preds = outputs[idx + len(self._feat_stride_fpn)]
            kps_preds = outputs[idx + 2 * len(self._feat_stride_fpn)]

            # Outputs are already (num_anchors_for_stride, channels) - no batch dim
            # stride 8: 80*80*2=12800, stride 16: 40*40*2=3200, stride 32: 20*20*2=800

            # Generate anchor grid
            height = input_h // stride
            width = input_w // stride

            # Create anchor centers
            ys, xs = np.mgrid[:height, :width]
            anchor_centers = np.stack([xs, ys], axis=-1).reshape(-1, 2)
            anchor_centers = (anchor_centers * stride).astype(np.float32)

            # Repeat for num_anchors
            if self._num_anchors > 1:
                anchor_centers = np.repeat(anchor_centers, self._num_anchors, axis=0)

            # Filter by threshold
            pos_inds = np.where(scores[:, 0] >= self._det_thresh)[0]
            if len(pos_inds) == 0:
                continue

            pos_scores = scores[pos_inds]
            pos_bbox_preds = bbox_preds[pos_inds] * stride
            pos_kps_preds = kps_preds[pos_inds] * stride
            pos_anchors = anchor_centers[pos_inds]

            bboxes = _distance2bbox(pos_anchors, pos_bbox_preds)
            kps = _distance2kps(pos_anchors, pos_kps_preds)

            scores_list.append(pos_scores)
            bboxes_list.append(bboxes)
            kps_list.append(kps)

        if not scores_list:
            return np.zeros((0, 5), dtype=np.float32), np.zeros((0, 5, 2), dtype=np.float32)

        scores_all = np.vstack(scores_list)
        bboxes_all = np.vstack(bboxes_list)
        kps_all = np.vstack(kps_list)

        # Combine bboxes with scores for NMS
        dets = np.hstack([bboxes_all, scores_all])

        # Apply NMS
        keep = _nms(dets, threshold=0.4)
        dets = dets[keep]
        kps_all = kps_all[keep]

        # Scale back to original image coordinates
        dets[:, :4] /= scale
        kps_all /= scale

        return dets, kps_all

    def detect_only(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Run ONLY the detector, skipping the recognition model entirely.

        Returns:
            (bboxes, landmarks) where bboxes is (N, 5) -- x1,y1,x2,y2,score --
            and landmarks is (N, 5, 2).
        """
        if not self._ready or self._det_session is None:
            raise RuntimeError("Face engine is not loaded.")

        blob, scale, _ = self._preprocess_det(image)

        # Run SCRFD detector
        input_name = self._det_session.get_inputs()[0].name
        outputs = self._det_session.run(None, {input_name: blob})

        bboxes, landmarks = self._postprocess_det(outputs, scale)
        return bboxes, landmarks

    # -- Recognition -----------------------------------------------------

    def align_face(self, image: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        """
        Warp a face into the canonical 112x112 aligned form using a similarity
        transform from the detected landmarks to the ArcFace template.

        This replaces insightface.utils.face_align.norm_crop().
        """
        src_pts = landmarks.astype(np.float32)
        dst_pts = ARCFACE_DST.astype(np.float32)

        # Estimate similarity transform (rotation + uniform scale + translation)
        # using Umeyama's method via OpenCV
        tform, _ = cv2.estimateAffinePartial2D(
            src_pts.reshape(-1, 1, 2),
            dst_pts.reshape(-1, 1, 2),
            method=cv2.LMEDS,
        )

        if tform is None:
            # Fallback: simple affine from first 3 points
            tform = cv2.getAffineTransform(src_pts[:3], dst_pts[:3])

        aligned = cv2.warpAffine(
            image,
            tform,
            (112, 112),
            borderValue=(0, 0, 0),
        )
        return aligned

    def embed_aligned(self, image: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        """
        Align a face using its landmarks and produce its embedding.

        The aligned 112x112 face is preprocessed (normalized to [-1, 1]) and
        fed through the ArcFace ONNX model.
        """
        if not self._ready or self._rec_session is None:
            raise RuntimeError("Face engine is not loaded.")

        # Align
        aligned = self.align_face(image, landmarks)

        # Preprocess for ArcFace: (pixel - 127.5) / 127.5 -> range [-1, 1]
        # Then HWC -> NCHW, float32
        blob = (aligned.astype(np.float32) - 127.5) / 127.5
        blob = blob.transpose(2, 0, 1)[np.newaxis, ...]

        # Run ArcFace
        input_name = self._rec_session.get_inputs()[0].name
        embedding = self._rec_session.run(None, {input_name: blob})[0]

        return embedding.flatten()

    # -- Full pipeline (for compatibility) ---------------------------------

    def analyse(self, image: np.ndarray) -> list[Any]:
        """
        Run the FULL pipeline: detection AND recognition, in one call.

        Returns a list of dicts with bbox, det_score, landmarks, and embedding.
        """
        bboxes, landmarks = self.detect_only(image)
        results = []

        for i in range(len(bboxes)):
            x1, y1, x2, y2, score = bboxes[i]
            kps = landmarks[i]
            embedding = self.embed_aligned(image, kps)

            result = type("Face", (), {
                "bbox": np.array([x1, y1, x2, y2]),
                "det_score": float(score),
                "kps": kps,
                "embedding": embedding,
            })()
            results.append(result)

        return results


def get_engine() -> FaceEngine:
    """Convenience accessor, also usable as a FastAPI dependency."""
    return FaceEngine.instance()
