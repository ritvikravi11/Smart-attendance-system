"""
Biometric storage: face embeddings.

WHY THIS TABLE EXISTS
---------------------
It stores the mathematical representation of a registered student's face --
a 512-dimensional vector produced by ArcFace. This is what recognition
compares against, and it is the single most sensitive table in the system.

WHY NOT STORE THE PHOTOGRAPHS
-----------------------------
Three reasons, in increasing order of importance:

  1. SIZE. Fifteen JPEGs per student x 1,000 students is several gigabytes.
     Fifteen embeddings is 30 KB.
  2. SPEED. Recognition compares vectors. Storing images would mean re-running
     the model over every stored photo on every match -- catastrophically slow.
  3. PRIVACY -- the decisive one. A breached photo database is a set of
     usable photographs of a thousand young people. A breached embedding
     database is a set of float arrays. Embeddings are not trivially
     reversible into recognisable faces, and they are useless outside a
     system running the same model. Deleting raw images after extraction
     (NFR-3.2) is data minimisation, and it is the right default.
"""

from typing import TYPE_CHECKING, Optional

import numpy as np
from sqlalchemy import (
    false as sa_false,
    true as sa_true,
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, BigIntPK, TimestampMixin

if TYPE_CHECKING:
    from app.models.people import Student


# The dimensionality produced by ArcFace r100 (the buffalo_l model pack).
# Declared as a constant so the value appears exactly once. If you ever swap
# to a model with different output size, this is the one line to change.
EMBEDDING_DIM = 512

# float32 is what the model emits. Storing float64 would double the size for
# precision the model never produced in the first place.
EMBEDDING_DTYPE = np.float32

# 512 dimensions x 4 bytes = 2048 bytes exactly.
EMBEDDING_BYTES = EMBEDDING_DIM * 4


class FaceEmbedding(Base, TimestampMixin):
    """
    WHY BYTEA AND NOT AN ARRAY OR JSON
    ----------------------------------
    Three candidate encodings were considered:

      JSONB -- stores "[0.0123, -0.4421, ...]" as TEXT. Roughly 6 KB per
               vector (3x the size), and float-to-string-to-float round-trips
               lose precision in the final digits. For a similarity threshold
               comparison, silently drifting values are exactly what you do
               not want. REJECTED.

      REAL[] -- PostgreSQL native array. Readable in psql, which is genuinely
               nice for debugging. But it carries per-element overhead, and
               converting a 512-element Python list to a NumPy array on every
               read costs measurable time when you are loading 1,000 of them
               at startup. REJECTED.

      BYTEA  -- raw bytes from `numpy.ndarray.tobytes()`. Exactly 2048 bytes,
               lossless (it is the literal memory representation), and
               `np.frombuffer()` reconstructs the array with zero copying.
               SELECTED.

    The trade-off accepted: you cannot read the value in psql. That is fine --
    a human reading 512 floats learns nothing anyway.

    WHY model_name AND model_version ARE MANDATORY
    ----------------------------------------------
    Embeddings from different models are mathematically INCOMPARABLE. An
    ArcFace vector and a FaceNet vector are both 512 numbers, and cosine
    similarity between them will happily return a number -- a meaningless one.

    Without these columns, upgrading your model in a year would silently
    corrupt every recognition: old students stored under the old model would
    be compared against new queries under the new model, producing garbage
    similarities that look plausible. The matcher MUST filter on
    (model_name, model_version) when building its index. These two columns are
    what make a model migration survivable.
    """

    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        # CASCADE, unlike almost everywhere else in this schema. Deleting a
        # student MUST genuinely erase their biometric data -- that is the
        # right to erasure (NFR-3.4), and it overrides the soft-delete
        # convention used for historical tables.
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
    )

    embedding: Mapped[bytes] = mapped_column(
        LargeBinary,  # maps to BYTEA in PostgreSQL
        nullable=False,
        comment="512 x float32, L2-normalised, via numpy.tobytes(). 2048 bytes.",
    )

    model_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="e.g. 'buffalo_l'. Embeddings across models are incomparable.",
    )

    model_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="e.g. '0.7.3'. Enables safe model migration.",
    )

    quality_score: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="Detection confidence at capture time (0-1)",
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_false(),
        comment=(
            "TRUE for the averaged reference vector used in matching. "
            "At most one per student (enforced by unique partial index)."
        ),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_true(),
        comment="FALSE excludes from the matcher index without deleting",
    )

    source_image_count: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="For primary vectors: how many images were averaged",
    )

    student: Mapped["Student"] = relationship(
        "Student", back_populates="face_embeddings"
    )

    __table_args__ = (
        # Enforces the exact byte length. If a bug ever writes a 256-dim
        # vector or a truncated buffer, the INSERT fails loudly at write time
        # rather than producing silently wrong similarities at read time.
        # Failing early and loudly is always cheaper than failing late and quietly.
        CheckConstraint(
            f"length(embedding) = {EMBEDDING_BYTES}",
            name="ck_embedding_byte_length",
        ),
        CheckConstraint(
            "quality_score IS NULL OR quality_score BETWEEN 0 AND 1",
            name="ck_embedding_quality_range",
        ),
        # A UNIQUE PARTIAL INDEX -- a genuinely powerful PostgreSQL feature.
        # It enforces "at most one active primary embedding per student"
        # while placing no restriction at all on the non-primary rows.
        # A plain UNIQUE(student_id) would wrongly forbid multiple source
        # embeddings; this permits many, but only one flagged primary.
        Index(
            "uq_embedding_one_primary_per_student",
            "student_id",
            unique=True,
            postgresql_where=text("is_primary = true AND is_active = true"),
        ),
        # The startup query: load every active primary embedding to build the
        # in-memory matcher index.
        Index(
            "idx_embeddings_active_primary",
            "student_id",
            "model_name",
            postgresql_where=text("is_active = true AND is_primary = true"),
        ),
        {"comment": "Biometric templates. Most sensitive table in the system."},
    )

    # ---- Serialisation helpers -----------------------------------------
    # These live on the model because encoding is a property of how this
    # column is stored, not business logic. Putting them here means no
    # service ever has to remember the dtype or the shape.

    def to_vector(self) -> np.ndarray:
        """
        Deserialise the stored bytes back into a NumPy array.

        `np.frombuffer` creates a VIEW over the existing bytes rather than
        copying them, so this is effectively free even when called 1,000
        times while building the matcher index.

        The returned array is read-only (because it views immutable bytes).
        Call `.copy()` if you need to modify it.
        """
        return np.frombuffer(self.embedding, dtype=EMBEDDING_DTYPE)

    @staticmethod
    def to_bytes(vector: np.ndarray) -> bytes:
        """
        Serialise a NumPy vector for storage, with validation.

        Validation happens here rather than in the service layer because this
        is the single choke point through which every embedding must pass.
        Guarding the choke point beats guarding every caller.
        """
        if vector.ndim != 1:
            raise ValueError(
                f"Embedding must be 1-dimensional, got shape {vector.shape}"
            )
        if vector.shape[0] != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding must have {EMBEDDING_DIM} dimensions, "
                f"got {vector.shape[0]}"
            )

        # Enforce dtype rather than trusting the caller. If a float64 array
        # arrives, silently storing it would produce 4096 bytes and trip the
        # CHECK constraint with a confusing error message.
        vector = vector.astype(EMBEDDING_DTYPE, copy=False)

        # Verify L2 normalisation. The matcher relies on all vectors being
        # unit length -- that is what makes cosine similarity reduce to a
        # plain dot product. An unnormalised vector slipping in would produce
        # similarities outside [-1, 1] and quietly break every threshold
        # comparison in the system.
        norm = float(np.linalg.norm(vector))
        if not np.isclose(norm, 1.0, atol=1e-3):
            raise ValueError(
                f"Embedding must be L2-normalised (norm=1.0), got norm={norm:.6f}"
            )

        return vector.tobytes()

    def __repr__(self) -> str:
        return (
            f"<FaceEmbedding id={self.id} student_id={self.student_id} "
            f"primary={self.is_primary} model={self.model_name!r}>"
        )
