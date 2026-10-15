"""
Step 1 test: preprocessing in isolation.

No models loaded, no detection. Just verifying that CLAHE, resizing, blur
detection, and brightness measurement behave as the theory predicts.

Run:  python tests/test_ml_01_preprocessing.py
"""

import os
import sys

sys.path.insert(0, "/home/claude/backend")
os.environ.setdefault("SECRET_KEY", "test-key")

import cv2
import insightface
import numpy as np

from app.ml.preprocessing import (
    apply_clahe,
    blur_score,
    estimate_yaw_ratio,
    mean_brightness,
    preprocess_frame,
    resize_max_dimension,
)

GREEN, RED, RESET = "\033[92m", "\033[91m", "\033[0m"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


IMG_DIR = os.path.join(os.path.dirname(insightface.__file__), "data", "images")
group_photo = cv2.imread(os.path.join(IMG_DIR, "t1.jpg"))
face_crop = cv2.imread(os.path.join(IMG_DIR, "Tom_Hanks_54745.png"))

print("\n" + "=" * 72)
print("STEP 1 -- PREPROCESSING (no models loaded)")
print("=" * 72)

# ---------------------------------------------------------------------------
print("\n[1.1] Test images loaded")
# ---------------------------------------------------------------------------
check("Group photo loaded", group_photo is not None, f"{group_photo.shape}")
check("Face crop loaded", face_crop is not None, f"{face_crop.shape}")
check("Images are BGR 3-channel", group_photo.shape[2] == 3)
check("Images are uint8", group_photo.dtype == np.uint8, str(group_photo.dtype))

# ---------------------------------------------------------------------------
print("\n[1.2] Aspect-preserving resize")
# ---------------------------------------------------------------------------
resized, scale = resize_max_dimension(group_photo, 640)
h0, w0 = group_photo.shape[:2]
h1, w1 = resized.shape[:2]

check("Longest side is 640", max(h1, w1) == 640, f"{w1}x{h1}")
orig_ratio = w0 / h0
new_ratio = w1 / h1
check(
    "Aspect ratio preserved",
    abs(orig_ratio - new_ratio) < 0.01,
    f"{orig_ratio:.4f} vs {new_ratio:.4f}",
)
check("Scale factor returned", abs(scale - 640 / max(h0, w0)) < 1e-6, f"{scale:.4f}")

small = np.zeros((100, 200, 3), dtype=np.uint8)
_, s = resize_max_dimension(small, 640)
check("Small images not upscaled", s == 1.0, "scale=1.0 means untouched")

# Verify the scale factor actually round-trips coordinates
test_x_small = 320
test_x_orig = int(test_x_small / scale)
check(
    "Scale factor maps coordinates back",
    abs(test_x_orig - (test_x_small * w0 / w1)) < 2,
    f"x={test_x_small} in small -> {test_x_orig} in original",
)

# ---------------------------------------------------------------------------
print("\n[1.3] CLAHE on the L channel")
# ---------------------------------------------------------------------------
# Build a deliberately bad image: dark, low contrast. This is the "classroom
# with the lights off" case.
dark = (group_photo * 0.28).astype(np.uint8)
enhanced = apply_clahe(dark)

check("Output shape unchanged", enhanced.shape == dark.shape, str(enhanced.shape))
check("Output dtype unchanged", enhanced.dtype == np.uint8)

std_before = float(np.std(cv2.cvtColor(dark, cv2.COLOR_BGR2GRAY)))
std_after = float(np.std(cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)))
check(
    "Contrast increased (std dev)",
    std_after > std_before,
    f"{std_before:.1f} -> {std_after:.1f}",
)

bright_before = mean_brightness(dark)
bright_after = mean_brightness(enhanced)
check(
    "Dark image brightened",
    bright_after > bright_before,
    f"{bright_before:.1f} -> {bright_after:.1f}",
)

# THE FAIRNESS CHECK: hue must not shift. This is what distinguishes
# LAB-L-channel CLAHE from naive per-RGB equalisation.
hsv_before = cv2.cvtColor(dark, cv2.COLOR_BGR2HSV)
hsv_after = cv2.cvtColor(enhanced, cv2.COLOR_BGR2HSV)
hue_shift = float(np.mean(np.abs(hsv_after[:, :, 0].astype(float) - hsv_before[:, :, 0].astype(float))))
check(
    "*** Hue preserved (skin tone not distorted) ***",
    hue_shift < 8.0,
    f"mean hue shift {hue_shift:.2f} degrees",
)

# Compare against the WRONG approach to show the difference is real
b, g, r = cv2.split(dark)
naive = cv2.merge([cv2.equalizeHist(b), cv2.equalizeHist(g), cv2.equalizeHist(r)])
hsv_naive = cv2.cvtColor(naive, cv2.COLOR_BGR2HSV)
naive_shift = float(np.mean(np.abs(hsv_naive[:, :, 0].astype(float) - hsv_before[:, :, 0].astype(float))))
check(
    "Naive per-RGB equalisation shifts hue MORE",
    naive_shift > hue_shift,
    f"naive {naive_shift:.2f} vs CLAHE-LAB {hue_shift:.2f}",
)

# ---------------------------------------------------------------------------
print("\n[1.4] Blur detection (variance of Laplacian)")
# ---------------------------------------------------------------------------
sharp_score = blur_score(group_photo)

# Gaussian blur simulates camera shake / out-of-focus
mild = blur_score(cv2.GaussianBlur(group_photo, (9, 9), 0))
heavy = blur_score(cv2.GaussianBlur(group_photo, (25, 25), 0))

check("Sharp image scores highest", sharp_score > mild > heavy,
      f"sharp={sharp_score:.1f} mild={mild:.1f} heavy={heavy:.1f}")
check("Score is monotonic with blur amount", mild > heavy)
check(
    "Heavy blur clearly separable",
    sharp_score / max(heavy, 0.01) > 5,
    f"{sharp_score / max(heavy, 0.01):.1f}x difference",
)

# Uniform image = no edges at all = near-zero variance
flat = np.full((200, 200, 3), 128, dtype=np.uint8)
check("Flat image scores ~0", blur_score(flat) < 1.0, f"{blur_score(flat):.4f}")

# ---------------------------------------------------------------------------
print("\n[1.5] Brightness measurement")
# ---------------------------------------------------------------------------
black = np.zeros((100, 100, 3), dtype=np.uint8)
white = np.full((100, 100, 3), 255, dtype=np.uint8)
grey = np.full((100, 100, 3), 128, dtype=np.uint8)

check("Black = 0", mean_brightness(black) == 0.0)
check("White = 255", mean_brightness(white) == 255.0)
check("Mid grey = 128", abs(mean_brightness(grey) - 128) < 1)

# ---------------------------------------------------------------------------
print("\n[1.6] Yaw estimation from landmarks")
# ---------------------------------------------------------------------------
# Synthetic landmarks: [left_eye, right_eye, nose, left_mouth, right_mouth]
frontal = np.array([[100, 100], [200, 100], [150, 150], [120, 200], [180, 200]], dtype=np.float32)
turned_left = np.array([[100, 100], [200, 100], [115, 150], [110, 200], [170, 200]], dtype=np.float32)
turned_right = np.array([[100, 100], [200, 100], [185, 150], [130, 200], [190, 200]], dtype=np.float32)

yaw_f = estimate_yaw_ratio(frontal)
yaw_l = estimate_yaw_ratio(turned_left)
yaw_r = estimate_yaw_ratio(turned_right)

check("Frontal face ~0.5", abs(yaw_f - 0.5) < 0.05, f"{yaw_f:.3f}")
check("Turned left < 0.5", yaw_l < 0.35, f"{yaw_l:.3f}")
check("Turned right > 0.5", yaw_r > 0.65, f"{yaw_r:.3f}")

degenerate = np.array([[100, 100], [100, 100], [100, 150], [100, 200], [100, 200]], dtype=np.float32)
check("Degenerate landmarks do not crash", estimate_yaw_ratio(degenerate) == 0.5, "returns 0.5")

# ---------------------------------------------------------------------------
print("\n[1.7] Full preprocessing pipeline")
# ---------------------------------------------------------------------------
processed, scale = preprocess_frame(group_photo)
check("Pipeline returns image and scale", processed is not None and scale < 1.0)
check("Output resized", max(processed.shape[:2]) == 640, f"{processed.shape}")

no_enhance, _ = preprocess_frame(group_photo, enhance=False)
check(
    "enhance=False skips CLAHE",
    not np.array_equal(processed, no_enhance),
    "outputs differ, so CLAHE is actually applied",
)

import time

t0 = time.perf_counter()
for _ in range(20):
    preprocess_frame(group_photo)
elapsed_ms = (time.perf_counter() - t0) / 20 * 1000
check(
    "Preprocessing fast enough for 2 fps",
    elapsed_ms < 100,
    f"{elapsed_ms:.1f} ms/frame (budget 500ms)",
)

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
passed = sum(1 for _, ok in results if ok)
total = len(results)
colour = GREEN if passed == total else RED
print(f"{colour}STEP 1 RESULT: {passed}/{total} checks passed{RESET}")
print("=" * 72 + "\n")
sys.exit(0 if passed == total else 1)
