# Smart Attendance System — Setup & Execution Guide

This is a full-stack AI-powered Attendance Management System built with **FastAPI**, **React (Vite + Tailwind)**, **SQLite**, and **ONNX Runtime (SCRFD + ArcFace)**.

---

## 📋 Prerequisites

Before starting, ensure the new PC has:
1. **Python 3.10+** (with `pip`)
2. **Node.js 18+** & **npm**

---

## 🚀 Quick Setup Instructions

### 1. Extract the Project
Extract `smart-attendance.zip` to your desired directory.

---

### 2. Backend Setup (Python)

Open a terminal (PowerShell / Command Prompt / Terminal) in the project root:

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install backend dependencies
pip install -r requirements.txt
```

#### Database & Models Setup:
```bash
# 1. Initialize SQLite database tables
python create_tables.py

# 2. Seed default admin, faculty, courses, and demo students
python init_db.py --demo

# 3. Download ONNX face recognition models (if not already in models/buffalo_l/)
python download_models.py
```

---

### 3. Frontend Setup (React / Node.js)

Open a second terminal in the project root:

```bash
# Install Node dependencies
npm install
```

---

## 🏃 Running the Application

### Start Backend Server:
In Terminal 1 (with `venv` activated):
```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
- **Backend API:** `http://127.0.0.1:8000`
- **API Documentation (Swagger UI):** `http://127.0.0.1:8000/docs`

### Start Frontend Server:
In Terminal 2:
```bash
npm run dev
```
- **Frontend App:** `http://127.0.0.1:5173`

---

## 🔑 Default Login Credentials

| Role | Email | Password |
|:---|:---|:---|
| **System Admin** | `admin@college.edu` | `Admin@12345` |
| **Faculty** | `das@college.edu` | `Faculty@123` |
| **Student** | `ravi@college.edu` | `Student@123` |
| **Student** | `priya@college.edu` | `Student@123` |
| **Student** | `ankit@college.edu` | `Student@123` |

---

## 📸 Face Recognition Features
- **Manual Attendance:** Open an offering session, take attendance manually or automatically.
- **Biometric Enrollment:** Grant consent (`POST /api/v1/students/{id}/consent`) and upload 5–15 student face photos (`POST /api/v1/students/{id}/faces`) to enable live webcam recognition.
