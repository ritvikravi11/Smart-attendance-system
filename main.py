"""
Shim for backward-compatible uvicorn invocation.

Allows both:
    uvicorn main:app          # original form
    uvicorn app.main:app      # canonical form
"""
from app.main import app  # noqa: F401
