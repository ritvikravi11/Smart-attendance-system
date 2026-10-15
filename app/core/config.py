"""
Application configuration loaded from environment variables.

WHY PYDANTIC SETTINGS RATHER THAN os.getenv()
---------------------------------------------
`os.getenv("DB_PORT")` returns the string "5432", not the integer 5432, and
returns None silently when the variable is missing. Both failure modes surface
far from their cause.

Pydantic Settings reads the environment, coerces types, validates values, and
fails LOUDLY at startup if something required is missing. A misconfigured
application should refuse to start, not run and misbehave.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings, populated from .env or the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application ----------------------------------------------------
    app_name: str = "AI Attendance Management System"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True

    # ---- Database -------------------------------------------------------
    postgres_user: str = "attendance_user"
    postgres_password: str = "change_me_in_env"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "attendance_db"

    sql_echo: bool = Field(
        default=False,
        description="Log every SQL statement. Very useful while learning.",
    )

    # Optional override: set DATABASE_URL in .env to use SQLite or any other
    # database without needing PostgreSQL credentials.
    database_url_override: str | None = Field(
        default=None,
        alias="DATABASE_URL",
        description=(
            "Full SQLAlchemy URL. When set, overrides the PostgreSQL URL "
            "assembled from POSTGRES_* variables. Use for SQLite dev: "
            "sqlite:///./attendance.db"
        ),
    )

    @computed_field
    @property
    def database_url(self) -> str:
        """
        Return the database URL, preferring an explicit override.

        When DATABASE_URL is set (e.g. to a SQLite path), use it directly.
        Otherwise, assemble the PostgreSQL connection URL from parts.
        """
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ---- Security -------------------------------------------------------
    secret_key: str = Field(
        default="INSECURE_DEFAULT_CHANGE_THIS",
        description="JWT signing key. MUST be overridden in .env.",
    )

    # SECURITY FIX: the origin list was hardcoded in main.py, which meant
    # deploying to a real domain required a CODE CHANGE. Config that varies by
    # environment belongs in the environment -- and a hardcoded list is the
    # kind of thing someone eventually "fixes" by setting allow_origins=["*"],
    # which combined with credentials is a genuine vulnerability.
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
        description="Comma-separated allowed origins. Never use '*' with credentials.",
    )

    @computed_field
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Retention windows. recognition_logs grows at roughly 2 rows per second
    # per active camera -- a term of use produces millions of rows carrying no
    # value once the threshold is calibrated.
    recognition_log_retention_days: int = 90
    audit_log_retention_days: int = 730  # 2 years; attendance disputes are slow
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    bcrypt_rounds: int = 12

    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # ---- ML startup ----
    load_ml_on_startup: bool = Field(
        default=True,
        description=(
            "Load the face model at boot. Set false for tests and for "
            "API-only work where the 3s load and 500MB are unwanted."
        ),
    )

    # ---- Face recognition ----------------------------------------------
    # These live in config rather than hard-coded so the threshold can be
    # retuned after calibration WITHOUT a code change (NFR-4.1).
    face_model_dir: str = Field(
        default="./models/buffalo_l",
        description="Directory containing the ONNX model files (det_10g.onnx, w600k_r50.onnx)",
    )
    face_model_name: str = "buffalo_l"
    face_match_threshold: float = Field(
        default=0.50,
        description="Accept a match at or above this cosine similarity",
    )
    face_review_threshold: float = Field(
        default=0.40,
        description="Grey band floor; matches between the two are flagged",
    )
    face_min_detection_score: float = 0.60
    face_min_face_width_px: int = 60
    face_min_enrollment_images: int = 5
    face_max_enrollment_images: int = 15

    # ---- Attendance rules -----------------------------------------------
    attendance_warning_threshold: float = Field(
        default=75.0,
        description="Percentage below which a student is flagged at risk",
    )

    # ---- Cloudinary (Student Profile Photos) ---------------------------
    cloudinary_cloud_name: str | None = Field(
        default=None,
        description="Cloudinary cloud name (required for photo uploads)",
    )

    cloudinary_api_key: str | None = Field(
        default=None,
        description="Cloudinary API key (required for photo uploads)",
    )

    cloudinary_api_secret: str | None = Field(
        default=None,
        description="Cloudinary API secret (required for photo uploads)",
    )

    cloudinary_secure: bool = Field(
        default=True,
        description="Use HTTPS for Cloudinary URLs",
    )

    cloudinary_upload_preset: str | None = Field(
        default=None,
        description="Optional Cloudinary upload preset for stricter validation",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Fail fast on a dangerous misconfiguration rather than running
        # insecurely in production.
        if self.environment == "production":
            if self.secret_key == "INSECURE_DEFAULT_CHANGE_THIS":
                raise ValueError(
                    "SECRET_KEY must be set to a real value in production."
                )
            # SECURITY FIX: only the DEFAULT value was rejected, so
            # SECRET_KEY=abc passed. HMAC-SHA256 keys shorter than 32 bytes
            # weaken the signature (RFC 7518 §3.2) -- PyJWT even warns about
            # it, and the warning was visible in our own test output.
            if len(self.secret_key.encode()) < 32:
                raise ValueError(
                    f"SECRET_KEY is {len(self.secret_key.encode())} bytes; "
                    "at least 32 are required for HS256. Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )
            if "*" in self.cors_origins:
                raise ValueError(
                    "CORS_ORIGINS must not contain '*' in production."
                )
            if self.postgres_password == "change_me_in_env":
                raise ValueError(
                    "POSTGRES_PASSWORD must be set to a real value in production."
                )


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings accessor.

    lru_cache makes this a singleton: the .env file is parsed once per
    process rather than on every access.
    """
    return Settings()


settings = get_settings()
