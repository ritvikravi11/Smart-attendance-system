"""
Download the buffalo_l ONNX model files for face recognition.

Downloads:
  - det_10g.onnx    (~16 MB)  SCRFD face detector
  - w600k_r50.onnx  (~167 MB) ArcFace recognition model

Source: InsightFace official model zoo on GitHub/Hugging Face.

Usage:
    python -m scripts.download_models
"""

import os
import sys
import urllib.request
import zipfile
import shutil

MODEL_DIR = os.path.join("models", "buffalo_l")

# InsightFace hosts model packs as zip files on their model zoo.
# buffalo_l contains: det_10g.onnx, w600k_r50.onnx (plus others we skip).
BUFFALO_L_URL = (
    "https://github.com/deepinsight/insightface/releases/download/"
    "v0.7/buffalo_l.zip"
)

REQUIRED_FILES = ["det_10g.onnx", "w600k_r50.onnx"]


def download_with_progress(url: str, dest: str) -> None:
    """Download a file with a simple progress indicator."""
    print(f"Downloading {url}")
    print(f"       -> {dest}")

    def _progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            mb = downloaded / (1024 * 1024)
            total_mb = total_size / (1024 * 1024)
            print(f"\r  {mb:.1f} / {total_mb:.1f} MB ({pct}%)", end="", flush=True)

    urllib.request.urlretrieve(url, dest, reporthook=_progress)
    print()  # newline after progress


def main() -> int:
    os.makedirs(MODEL_DIR, exist_ok=True)

    # Check if models already exist
    existing = [f for f in REQUIRED_FILES if os.path.isfile(os.path.join(MODEL_DIR, f))]
    if len(existing) == len(REQUIRED_FILES):
        print(f"All model files already present in {MODEL_DIR}:")
        for f in REQUIRED_FILES:
            size = os.path.getsize(os.path.join(MODEL_DIR, f))
            print(f"  {f}  ({size / (1024*1024):.1f} MB)")
        return 0

    # Download the zip
    zip_path = os.path.join("models", "buffalo_l.zip")
    try:
        download_with_progress(BUFFALO_L_URL, zip_path)
    except Exception as e:
        print(f"\nDownload failed: {e}")
        print("\nManual download instructions:")
        print(f"  1. Download from: {BUFFALO_L_URL}")
        print(f"  2. Extract to: {MODEL_DIR}/")
        print(f"  3. Ensure these files exist: {', '.join(REQUIRED_FILES)}")
        return 1

    # Extract only the files we need
    print(f"Extracting models to {MODEL_DIR}/ ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            basename = os.path.basename(name)
            if basename in REQUIRED_FILES:
                # Extract to MODEL_DIR with flat structure
                target = os.path.join(MODEL_DIR, basename)
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                size = os.path.getsize(target)
                print(f"  {basename}  ({size / (1024*1024):.1f} MB)")

    # Clean up zip
    os.remove(zip_path)
    print(f"Cleaned up {zip_path}")

    # Verify
    missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(MODEL_DIR, f))]
    if missing:
        print(f"\nERROR: Missing files after extraction: {missing}")
        return 1

    print(f"\nModel files ready in {MODEL_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
