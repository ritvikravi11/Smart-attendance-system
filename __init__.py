"""
Model package exports.

WHY THIS FILE MATTERS MORE THAN IT LOOKS
----------------------------------------
Alembic generates migrations by comparing `Base.metadata` (what your models
declare) against the live database (what actually exists). But a model only
lands in `Base.metadata` when its module is IMPORTED -- defining a class in a
file that nobody imports leaves it invisible.

So if `app/models/face.py` is never imported, Alembic will not know
`face_embeddings` exists, will not create it, and -- worse -- if the table
already exists it will helpfully generate a migration to DROP it.

Importing every model here, and pointing Alembic's env.py at this package,
guarantees the full schema is registered before autogenerate runs.

The `__all__` list also gives you clean imports elsewhere:
    from app.models import Student, AttendanceRecord
instead of reaching into individual modules.

IMPORT ORDER
------------
Order matters only for resolving string-based relationship targets at mapper
configuration time. We import in dependency order (base entities first,
dependents after) so that any configuration error surfaces with a clear
message rather than a confusing partial-registry failure.
"""

from app.db.base_class import Base

# Identity and access
from app.models.user import Role, User, RefreshToken

# Organisational and academic structure
from app.models.academic import (
    Department,
    Subject,
    SubjectOffering,
    Enrollment,
)

# Role-specific profiles
from app.models.people import Student, Faculty

# Biometrics
from app.models.face import (
    FaceEmbedding,
    EMBEDDING_DIM,
    EMBEDDING_DTYPE,
    EMBEDDING_BYTES,
)

# Attendance core
from app.models.attendance import AttendanceSession, AttendanceRecord

# Observability
from app.models.logs import RecognitionLog, AuditLog

# Enumerations
from app.models.enums import (
    RoleName,
    AttendanceStatus,
    MarkingMethod,
    SessionStatus,
    AuditAction,
)

__all__ = [
    "Base",
    # Identity
    "Role",
    "User",
    "RefreshToken",
    # Academic
    "Department",
    "Subject",
    "SubjectOffering",
    "Enrollment",
    # People
    "Student",
    "Faculty",
    # Biometrics
    "FaceEmbedding",
    "EMBEDDING_DIM",
    "EMBEDDING_DTYPE",
    "EMBEDDING_BYTES",
    # Attendance
    "AttendanceSession",
    "AttendanceRecord",
    # Logs
    "RecognitionLog",
    "AuditLog",
    # Enums
    "RoleName",
    "AttendanceStatus",
    "MarkingMethod",
    "SessionStatus",
    "AuditAction",
]
