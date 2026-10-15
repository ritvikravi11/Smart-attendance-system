"""
In-memory embedding index and similarity search.

CONCEPT 9: WHY THE INDEX LIVES IN RAM, NOT THE DATABASE
--------------------------------------------------------
Recognising one face means comparing its embedding against every enrolled
student. With 1,000 students that is 1,000 comparisons -- per face, per frame,
at 2 frames per second.

Three ways to do it:

  1. One database query per comparison.
     1,000 round trips per face. Roughly 5,000 ms. Absurd.

  2. One query to fetch all embeddings, then compare in Python.
     ~250 ms. Better, but you re-fetch 2 MB from the database on EVERY frame
     to compare against data that changes only when someone enrols.

  3. Load once into a NumPy matrix; compare with one matrix multiply.
     ~1 ms.

Option 3 is what this module implements. The reasoning generalises: data that
is read constantly and written rarely belongs in memory.

CONCEPT 10: THE MATRIX MULTIPLY TRICK
-------------------------------------
Every stored embedding is L2-normalised, so cosine similarity reduces to a dot
product. Stack all N embeddings into an (N, 512) matrix, and comparing a query
against ALL of them becomes:

        similarities = index_matrix @ query        # (N, 512) @ (512,) -> (N,)

One operation. NumPy dispatches this to BLAS -- the same heavily optimised
linear algebra kernels that scientific computing has refined for decades, using
SIMD instructions and cache-aware blocking. A hand-written Python loop over
1,000 vectors is roughly 1,000x slower for identical arithmetic.

This is the single most important performance decision in the recognition
path, and it is four characters of code: `@`.

CONCEPT 11: UNKNOWN FACE DETECTION
----------------------------------
`argmax` ALWAYS returns something. Given an embedding of a complete stranger,
the index still reports a "best" match -- it is just a poor one. Without a
threshold, every visitor, every parent, every passer-by gets marked present as
whichever student they least-dissimilarly resemble.

The threshold is what converts "nearest neighbour" into "recognition":

    similarity >= 0.50   accept
    0.40 - 0.50          accept but flag for human review
    below 0.40           UNKNOWN -- write no attendance record

THE THREE BANDS EXIST BECAUSE THE ERRORS ARE NOT SYMMETRIC.
A false ACCEPTANCE marks an absent student present -- it enables proxy
attendance and corrupts the record. A false REJECTION means a present student
is not recognised -- annoying, and fixed by one manual click.

The first is much worse, so the system is tuned to prefer the second. The grey
band routes genuinely ambiguous cases to a human instead of silently guessing.

Measured on real data in this project's own tests: impostor pairs scored at
most 0.213 while a genuine same-person pair scored 0.980. The gap is wide, and
0.40/0.50 sits comfortably inside it.
"""

import logging
import threading
import time

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.interfaces import EMBEDDING_DIM, IMatcher, MatchResult
from app.models.face import FaceEmbedding
from app.models.people import Student

logger = logging.getLogger(__name__)


class InMemoryMatcher(IMatcher):
    """
    NumPy-backed nearest-neighbour search over enrolled embeddings.

    Holds two parallel structures:
        _matrix     (N, 512) float32 -- the embeddings
        _student_ids (N,)    int     -- row i belongs to student _student_ids[i]

    Parallel arrays rather than a dict because the whole point is the matrix
    multiply, which needs contiguous memory.
    """

    def __init__(self, session_factory=None):
        self._matrix: np.ndarray = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        self._student_ids: np.ndarray = np.zeros((0,), dtype=np.int64)
        self._session_factory = session_factory
        self._lock = threading.RLock()
        self._last_rebuild: float = 0.0

    # -- Index construction ----------------------------------------------

    def load_from_db(self, db: Session) -> int:
        """
        Build the index from active primary embeddings.

        Called at application startup and again whenever a student is enrolled
        or deactivated.

        NOTE THE model_name FILTER. Embeddings produced by different models are
        mathematically incomparable -- cosine similarity between an ArcFace
        vector and some other model's vector returns a number, and that number
        is meaningless. Without this filter, a model upgrade would silently
        corrupt every recognition while raising no error at all.
        """
        stmt = (
            select(FaceEmbedding.student_id, FaceEmbedding.embedding)
            .join(Student, Student.id == FaceEmbedding.student_id)
            .where(
                FaceEmbedding.is_primary.is_(True),
                FaceEmbedding.is_active.is_(True),
                FaceEmbedding.model_name == settings.face_model_name,
                Student.is_active.is_(True),  # deactivated students drop out
            )
        )
        rows = db.execute(stmt).all()
        return self._build(rows)

    def _build(self, rows: list[tuple[int, bytes]]) -> int:
        """Assemble the matrix from (student_id, embedding_bytes) pairs."""
        with self._lock:
            if not rows:
                self._matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
                self._student_ids = np.zeros((0,), dtype=np.int64)
                self._last_rebuild = time.time()
                logger.warning("Embedding index is EMPTY -- no students enrolled")
                return 0

            ids: list[int] = []
            vectors: list[np.ndarray] = []

            for student_id, blob in rows:
                vec = np.frombuffer(blob, dtype=np.float32)
                if vec.shape[0] != EMBEDDING_DIM:
                    # Skip rather than crash: one corrupt row must not take the
                    # whole system offline. Log loudly so it gets fixed.
                    logger.error(
                        "Student %s has a %d-d embedding, expected %d -- skipping",
                        student_id, vec.shape[0], EMBEDDING_DIM,
                    )
                    continue
                ids.append(student_id)
                vectors.append(vec)

            # np.ascontiguousarray guarantees a single contiguous memory block.
            # BLAS is dramatically faster on contiguous data, and np.stack can
            # otherwise produce a non-contiguous view.
            self._matrix = np.ascontiguousarray(
                np.stack(vectors), dtype=np.float32
            )
            self._student_ids = np.asarray(ids, dtype=np.int64)
            self._last_rebuild = time.time()

            logger.info(
                "Embedding index built: %d students, %.1f KB",
                len(ids), self._matrix.nbytes / 1024,
            )
            return len(ids)

    def rebuild(self) -> int:
        """Reload from the database using the configured session factory."""
        if self._session_factory is None:
            raise RuntimeError(
                "No session factory configured. Call load_from_db(db) instead."
            )
        db = self._session_factory()
        try:
            return self.load_from_db(db)
        finally:
            db.close()

    # -- Search ----------------------------------------------------------

    def search(
        self, embedding: np.ndarray, candidate_ids: set[int] | None = None
    ) -> MatchResult:
        """
        Find the best match for a query embedding.

        Args:
            embedding: L2-normalised 512-d query vector.
            candidate_ids: Restrict the search to these students -- normally
                the roster enrolled in the current offering.

        WHY RESTRICTING TO THE ROSTER MATTERS TWICE OVER
        ------------------------------------------------
        SPEED: 40 candidates instead of 1,000 is ~25x less arithmetic.

        ACCURACY, which matters more: false-positive probability grows with
        the number of candidates. Search 1,000 students and you are far more
        likely to find someone who coincidentally resembles the query than if
        you search only the 40 who could legitimately be in this room. A
        lookalike from another department is simply not a candidate.

        This is a case where the faster option is also the more accurate one,
        which is rare and worth recognising when it appears.
        """
        with self._lock:
            if self._matrix.shape[0] == 0:
                return MatchResult(
                    student_id=None, confidence=0.0,
                    is_unknown=True, needs_review=False,
                )

            matrix = self._matrix
            ids = self._student_ids

            # Roster filtering: build a boolean mask and slice.
            if candidate_ids is not None:
                if not candidate_ids:
                    return MatchResult(
                        student_id=None, confidence=0.0,
                        is_unknown=True, needs_review=False,
                    )
                mask = np.isin(ids, np.fromiter(candidate_ids, dtype=np.int64))
                if not mask.any():
                    return MatchResult(
                        student_id=None, confidence=0.0,
                        is_unknown=True, needs_review=False,
                    )
                matrix = matrix[mask]
                ids = ids[mask]

            # ===== THE ENTIRE SEARCH, IN ONE LINE =====
            # (N, 512) @ (512,) -> (N,). Every stored vector and the query are
            # unit length, so each dot product IS the cosine similarity.
            similarities = matrix @ embedding

            best_index = int(np.argmax(similarities))
            best_score = float(similarities[best_index])
            best_id = int(ids[best_index])

            # Runner-up drives the margin diagnostic. A small gap between the
            # top two means the model is effectively undecided -- typically
            # siblings or twins -- which confidence alone would not reveal.
            if len(similarities) > 1:
                runner_up = float(np.partition(similarities, -2)[-2])
            else:
                runner_up = 0.0

        # ---- Three-band decision ----
        if best_score >= settings.face_match_threshold:
            return MatchResult(
                student_id=best_id, confidence=best_score,
                is_unknown=False, needs_review=False,
                runner_up_confidence=runner_up,
            )

        if best_score >= settings.face_review_threshold:
            # Grey band: accept, but flag for a human to confirm.
            return MatchResult(
                student_id=best_id, confidence=best_score,
                is_unknown=False, needs_review=True,
                runner_up_confidence=runner_up,
            )

        # UNKNOWN. student_id is None deliberately -- that nullability carries
        # meaning downstream (unknown-person alerts, impostor distribution for
        # threshold calibration).
        return MatchResult(
            student_id=None, confidence=best_score,
            is_unknown=True, needs_review=False,
            runner_up_confidence=runner_up,
        )

    def search_batch(
        self, embeddings: list[np.ndarray], candidate_ids: set[int] | None = None
    ) -> list[MatchResult]:
        """
        Match several faces from one classroom photo.

        A true batch implementation would stack the queries into an (M, 512)
        matrix and compute `matrix @ queries.T` in one multiply. At our scale
        the loop is already sub-millisecond, so the simpler version wins --
        but the seam is here when a 200-student assembly hall needs it.
        """
        return [self.search(e, candidate_ids) for e in embeddings]

    # -- Introspection ---------------------------------------------------

    @property
    def size(self) -> int:
        with self._lock:
            return int(self._matrix.shape[0])

    @property
    def student_ids(self) -> set[int]:
        with self._lock:
            return {int(i) for i in self._student_ids}

    def contains(self, student_id: int) -> bool:
        with self._lock:
            return bool(np.any(self._student_ids == student_id))

    def health(self) -> dict:
        with self._lock:
            return {
                "size": int(self._matrix.shape[0]),
                "memory_kb": round(self._matrix.nbytes / 1024, 1),
                "last_rebuild_epoch": self._last_rebuild,
                "match_threshold": settings.face_match_threshold,
                "review_threshold": settings.face_review_threshold,
            }


# Module-level shared instance, mirroring the engine singleton. Built at
# startup and refreshed on enrolment.
_matcher: InMemoryMatcher | None = None
_matcher_lock = threading.Lock()


def get_matcher() -> InMemoryMatcher:
    global _matcher
    if _matcher is None:
        with _matcher_lock:
            if _matcher is None:
                _matcher = InMemoryMatcher()
    return _matcher
