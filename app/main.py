"""
FastAPI application entry point.

This file ASSEMBLES the application. It creates the app, registers middleware,
mounts routers, and wires exception handlers. It contains no business logic
and no route definitions of its own beyond a health check.

Keeping assembly separate from behaviour means you can read this file to
understand the shape of the system in about thirty seconds.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppException

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown hooks.

    Code before `yield` runs once at startup; code after runs at shutdown.

    THIS IS WHERE THE FACE MODEL WILL BE LOADED in a later module. The
    InsightFace model takes ~3 seconds to load and ~500MB of RAM. Loading it
    here means that cost is paid ONCE at boot. Loading it per request would
    make every recognition take 3+ seconds and exhaust memory under any
    concurrency (NFR-1.4).

    The in-memory embedding index will also be built here, from the database.
    """
    logger.info("Starting %s [%s]", settings.app_name, settings.environment)

    from app.db.session import ensure_sqlite_schema

    ensure_sqlite_schema()

    # ---- Load the face model ONCE (NFR-1.4) ----
    # ~3 seconds and ~500MB. Paying this per request would make every
    # recognition take 3+ seconds and exhaust memory under any concurrency.
    if settings.load_ml_on_startup:
        try:
            from app.db.session import SessionLocal
            from app.ml.engine import get_engine
            from app.ml.matcher import get_matcher

            engine = get_engine()
            engine.load()

            # ---- Build the embedding index from the database ----
            # Without this the matcher is empty and every face is UNKNOWN.
            db = SessionLocal()
            try:
                count = get_matcher().load_from_db(db)
                logger.info("Embedding index ready: %d students", count)
            finally:
                db.close()

        except Exception:
            # A failed model load must NOT prevent the API from serving.
            # Manual attendance marking still works, which is the graceful
            # degradation promised in NFR-4.2.
            logger.exception(
                "Face model failed to load. Recognition endpoints will return "
                "503; manual marking is unaffected."
            )
    else:
        logger.info("ML startup disabled (LOAD_ML_ON_STARTUP=false)")

    logger.info("API documentation available at /docs")
    yield

    logger.info("Shutting down")
    try:
        from app.ml.engine import get_engine
        get_engine().unload()
    except Exception:
        pass


app = FastAPI(
    title=settings.app_name,
    description=(
        "AI-powered attendance management using face recognition.\n\n"
        "**Authentication:** call `POST /api/v1/auth/login`, then click the "
        "**Authorize** button above and paste the `access_token`."
    ),
    version="1.0.0",
    lifespan=lifespan,
    # Interactive docs are disabled in production. They are enormously useful
    # in development and an unnecessary disclosure of your API surface in
    # production.
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Browsers block cross-origin requests by default. The React dev server runs on
# :5173 while this API runs on :8000 -- different ports mean different origins,
# so without this the frontend cannot call the backend at all.
#
# NOTE the explicit origin list. `allow_origins=["*"]` combined with
# `allow_credentials=True` is rejected by browsers, and is bad practice
# regardless: it invites any website to make authenticated requests against
# your API using a logged-in user's browser.
app.add_middleware(
    CORSMiddleware,
    # Driven by CORS_ORIGINS in .env -- deploying to a real domain is a
    # config change, not a code change.
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# EXCEPTION HANDLERS
# ---------------------------------------------------------------------------
# This is the translation layer promised in core/exceptions.py. Services raise
# domain exceptions; these handlers turn them into HTTP responses with one
# consistent shape. Status codes are decided HERE and nowhere else.


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """
    Handle every application exception uniformly.

    Because every domain exception subclasses AppException and carries its own
    status_code, this single handler covers 401, 403, 404, 409, 423 and more.
    Adding a new exception type requires no change here.
    """
    logger.warning(
        "%s on %s %s: %s",
        exc.error_code,
        request.method,
        request.url.path,
        exc.message,
    )
    body = {"detail": exc.message, "error_code": exc.error_code}
    if exc.field:
        body["field"] = exc.field
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Reshape Pydantic validation errors into our standard envelope.

    FastAPI's default 422 body is a nested structure that differs from every
    other error the API returns. Normalising it means the frontend has exactly
    one error shape to handle.
    """
    first = exc.errors()[0] if exc.errors() else {}
    # loc is like ("body", "email"); the field name is the last element.
    location = first.get("loc", ())
    field = str(location[-1]) if location else None
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": first.get("msg", "Invalid request payload."),
            "error_code": "VALIDATION_ERROR",
            "field": field,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """
    Catch-all for genuine bugs.

    CRITICAL SECURITY POINT: the response body deliberately says nothing
    useful. The full traceback goes to the SERVER LOG, where you can read it;
    the client gets a generic message.

    Leaking stack traces to clients discloses file paths, library versions,
    SQL fragments, and sometimes credentials. It is a real and common
    vulnerability, and it is why `debug=True` must never reach production.
    """
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal error occurred.",
            "error_code": "INTERNAL_ERROR",
        },
    )


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["System"], summary="Liveness probe")
def health_check() -> dict[str, str]:
    """
    Unauthenticated health check.

    Deployment tooling, load balancers, and uptime monitors call this to
    decide whether the process is alive. It must never require authentication
    -- a monitor holding credentials is a credential you have to rotate.

    Deliberately returns no version or environment detail: this endpoint is
    reachable by anyone, so it should disclose nothing.
    """
    return {"status": "ok"}


@app.get("/", tags=["System"], include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "docs": "/docs" if settings.environment != "production" else "disabled",
    }
