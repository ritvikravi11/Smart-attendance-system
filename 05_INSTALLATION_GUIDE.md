# Installation Guide
## AI-Powered Attendance Management System

**Target platform: Windows 10/11.** Linux and macOS notes are marked where they differ.

Estimated time: **60–90 minutes**, most of it downloads.

---

## 1. Prerequisites Checklist

| Requirement | Version | Why this version |
|-------------|---------|------------------|
| Python | **3.11** | ONNX Runtime and InsightFace lag behind newer releases |
| PostgreSQL | 16 | Partial indexes, JSONB, `BYTEA` |
| Node.js | 18+ | Vite 5 requires it |
| Visual C++ Build Tools | 2019+ | **Windows only** — InsightFace compiles C extensions |
| Git | any | |
| Webcam | 720p+ | |
| Disk | 3 GB free | 275 MB model + node_modules |
| RAM | 8 GB recommended | The model resides at ~500 MB |

> **Do not use Python 3.12 or 3.13.** The pinned ONNX Runtime has no wheels for them, and `pip` will attempt a source build that fails.

---

## 2. Python 3.11

Check what you have — open **Command Prompt** (not PowerShell):

```
python --version
```

If it is not 3.11.x, install from <https://www.python.org/downloads/release/python-3119/>.

**Tick "Add python.exe to PATH" on the first installer screen.** It is easy to miss and causes an hour of confusion.

> **Why Command Prompt and not PowerShell?** PowerShell blocks script execution by default, so `venv\Scripts\activate` throws a permissions error. You can fix it with `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, but `cmd` avoids the issue.

---

## 3. Visual C++ Build Tools — the step everyone skips

**Windows only.** Skip this and `pip install insightface` fails with a wall of red text ending in `error: Microsoft Visual C++ 14.0 or greater is required`.

1. Download from <https://visualstudio.microsoft.com/visual-cpp-build-tools/>
2. Run the installer
3. Select the **"Desktop development with C++"** workload
4. Install (~3 GB, 15–30 minutes)
5. **Restart your terminal**

*Linux:* `sudo apt install build-essential python3.11-dev`
*macOS:* `xcode-select --install`

---

## 4. PostgreSQL 16

Download from <https://www.postgresql.org/download/windows/>.

During installation:
- Set a password for the `postgres` superuser — **write it down**
- Keep port `5432`
- Install pgAdmin 4 when offered (a GUI helps while learning)

### Create the database

Open **SQL Shell (psql)** from the Start menu, press Enter through the prompts, and enter your superuser password:

```sql
CREATE DATABASE attendance_db;
CREATE USER attendance_user WITH PASSWORD 'choose_a_strong_password';
GRANT ALL PRIVILEGES ON DATABASE attendance_db TO attendance_user;

\c attendance_db
GRANT ALL ON SCHEMA public TO attendance_user;
ALTER DATABASE attendance_db OWNER TO attendance_user;
```

> **Why a dedicated user rather than `postgres`?** Principle of least privilege. If the application is compromised, the attacker holds an account scoped to one database rather than one that can drop every database on the server. Standard practice, costs nothing.

Verify:
```
psql -U attendance_user -d attendance_db -h localhost
```

---

## 5. Backend Setup

```
cd C:\Users\%USERNAME%\Documents
git clone <your-repo-url> attendance-system
cd attendance-system\backend

python -m venv venv
venv\Scripts\activate
```

Your prompt should now begin with `(venv)`.

*Linux/macOS:* `source venv/bin/activate`

### Install dependencies

```
pip install --upgrade pip
pip install -r requirements.txt
```

**5–15 minutes.** The large downloads are `onnxruntime` (~50 MB), `opencv-python` (~60 MB), and `mediapipe` (~90 MB).

### Configure

Copy `.env.example` to `.env`, then generate a real secret key:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Edit `.env`:

```env
ENVIRONMENT=development
DEBUG=true

POSTGRES_USER=attendance_user
POSTGRES_PASSWORD=choose_a_strong_password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=attendance_db
SQL_ECHO=false

SECRET_KEY=<paste the generated key here>
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

LOAD_ML_ON_STARTUP=true
FACE_MODEL_NAME=buffalo_l
FACE_MATCH_THRESHOLD=0.50
FACE_REVIEW_THRESHOLD=0.40

ATTENDANCE_WARNING_THRESHOLD=75.0
```

> **Never commit `.env`.** Confirm it appears in `.gitignore` before your next commit. `SECRET_KEY` signs every authentication token in your system.

### Database migrations

```
alembic revision --autogenerate -m "initial schema"
```

**Read the generated file in `alembic/versions/` before applying it.** Autogenerate is good but not infallible — confirm it creates 14 tables and drops nothing. Then:

```
alembic upgrade head
```

Verify in psql:
```sql
\c attendance_db
\dt
```
You should see 14 tables plus `alembic_version`. Confirm the critical constraint:
```sql
\d attendance_records
```
Look for `"uq_attendance_session_student" UNIQUE CONSTRAINT, btree (session_id, student_id)`.

### Seed data

```
python -m app.db.init_db --demo
```

Idempotent — safe to run repeatedly. Creates:

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@college.edu` | `Admin@12345` |
| Faculty | `das@college.edu` | `Faculty@123` |
| Student | `ravi@college.edu` | `Student@123` |

> **Change the admin password immediately after first login.** The seed script warns loudly about this for a reason — "admin/admin left in production" is a classic failure.

### First run

```
uvicorn app.main:app --reload
```

The **first start downloads ~275 MB** of model weights to `C:\Users\<you>\.insightface\models\`. Expect 2–5 minutes. Subsequent starts take ~6 seconds.

Success looks like:
```
INFO  Starting AI Attendance Management System [development]
INFO  Face model loaded in 5.51s (modules: detection, recognition)
INFO  Embedding index ready: 0 students
INFO  API documentation available at /docs
INFO  Uvicorn running on http://127.0.0.1:8000
```

> `Embedding index ready: 0 students` is expected — no face data has been enrolled yet.

Open **<http://localhost:8000/docs>**.

---

## 6. Frontend Setup

Open a **second** terminal (leave the backend running):

```
cd attendance-system\frontend
npm install
npm run dev
```

Open **<http://localhost:5173>** and sign in.

> The Vite dev server proxies `/api` to `http://localhost:8000`, so the backend must be running or every request returns a network error.

---

## 7. Verify the Installation

```
cd backend
python scripts\validate_schema.py          # 30/30
python tests\test_auth.py                  # 61/61
python tests\test_attendance.py            # 59/59
python tests\test_analytics.py             # 46/46
python tests\test_ml_01_preprocessing.py   # 30/30
python tests\test_ml_02_detection.py       # 40/40
python tests\test_ml_03_embedding.py       # 30/30
python tests\test_ml_04_matching.py        # 44/44
```

Then see the camera work:

```
python scripts\ml_01_live_detection.py
```

A window opens showing your face with a box around it. Press `c` to toggle CLAHE, `l` for landmarks, `q` to quit.

---

## 8. ⚠️ The Demo-Day Trap

**`getUserMedia` only works on `localhost` or HTTPS.** It **fails silently on `http://192.168.x.x`**.

So the moment you demo from a phone or a second laptop on the college wifi, the camera dies with a permissions error that names the wrong cause.

**Decide your demo topology now, not on the day.**

| Option | Setup |
|--------|-------|
| **Demo on the host laptop** (recommended) | Nothing to do — `localhost` is a secure context |
| Tunnel with ngrok | `ngrok http 5173` gives an HTTPS URL that works anywhere |
| Self-signed certificate | More work; needs the cert installed on every client |

---

## 9. Troubleshooting

### Installation

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Microsoft Visual C++ 14.0 or greater is required` | Build Tools missing | Section 3, then restart the terminal |
| `No module named 'onnxruntime'` on Python 3.12+ | No wheels for that version | Recreate the venv with Python 3.11 |
| `numpy.dtype size changed` | numpy 2.x conflict | `pip install numpy==1.26.4 --force-reinstall` |
| `venv\Scripts\activate` permission denied | PowerShell policy | Use `cmd`, or `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |

### Database

| Symptom | Fix |
|---------|-----|
| `could not connect to server` | Services → confirm `postgresql-x64-16` is running |
| `password authentication failed` | `POSTGRES_PASSWORD` in `.env` must match the user you created |
| `relation "users" does not exist` | Run `alembic upgrade head` |
| Alembic generates an empty migration | `alembic/env.py` must import `from app.models import Base` |

> **That last one is worth understanding.** Alembic compares `Base.metadata` against the live database, and a model only registers in that metadata when its module is imported. Miss the import and Alembic will not create the table — or worse, will generate a migration to **drop** it.

### Model and camera

| Symptom | Fix |
|---------|-----|
| Model download fails | Download `buffalo_l.zip` from the InsightFace releases page; unzip to `C:\Users\<you>\.insightface\models\` |
| Camera window never opens | Settings → Privacy & security → Camera → allow desktop apps |
| Black frames | Close Zoom/Teams/OBS — only one process can hold the camera |
| Wrong camera | Change `CAMERA_INDEX = 0` to `1` in the script |
| Camera works in Python, not the browser | Section 8 — the secure-context requirement |

### Frontend

| Symptom | Fix |
|---------|-----|
| Every request returns a network error | The backend is not running |
| Blank page, console shows a module error | Delete `node_modules` and `package-lock.json`, run `npm install` again |
| Charts render blank | Their parent needs an explicit height — `maintainAspectRatio: false` makes the container authoritative |

---

## 10. Quick Reference

**Daily startup** — two terminals:

```
# Terminal 1
cd backend && venv\Scripts\activate && uvicorn app.main:app --reload

# Terminal 2
cd frontend && npm run dev
```

| URL | What |
|-----|------|
| `http://localhost:5173` | The application |
| `http://localhost:8000/docs` | Interactive API documentation |
| `http://localhost:8000/health` | Liveness check |

**Reset the database completely:**
```sql
DROP DATABASE attendance_db;
CREATE DATABASE attendance_db;
```
```
alembic upgrade head
python -m app.db.init_db --demo
```

---

*End of Installation Guide v1.0*
