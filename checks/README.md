# Executable checks

These files are runnable verification programs rather than pytest modules.
They print a pass/fail summary and exit with a non-zero status on failure.

Run them from the repository root, for example:

```powershell
.\venv\Scripts\python.exe checks\test_ml_01_preprocessing.py
```

The ML checks require the downloaded InsightFace model. The enrollment
end-to-end check also requires suitable face images and should not be included
in ordinary pytest collection.
