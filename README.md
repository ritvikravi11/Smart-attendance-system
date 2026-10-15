# Smart Attendance System

AI-powered attendance management using face recognition.

Built with **FastAPI**, **React (Vite + Tailwind)**, **SQLite/PostgreSQL**, and **ONNX Runtime (SCRFD + ArcFace)**.

## Quick Start

See [README_SETUP.md](README_SETUP.md) for full setup instructions.

## Project Structure

```
app/              Backend application package
  api/            FastAPI routers and dependencies
  core/           Configuration, security, exceptions
  db/             Database session and base classes
  ml/             Face detection, embedding, matching
  models/         SQLAlchemy ORM models
  schemas/        Pydantic request/response schemas
  services/       Business logic layer
src/              React frontend (Vite)
tests/            Pytest test suite
checks/           Standalone verification scripts
alembic/          Database migrations
models/           ONNX model files
```

## Running Tests

```bash
# Activate virtual environment first
pytest                                    # Full pytest suite
python checks/test_ml_01_preprocessing.py # Standalone ML checks (need insightface)
```
