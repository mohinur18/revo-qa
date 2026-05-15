"""
Visual regression helper. Compare a freshly captured screenshot against a baseline,
emit a diff image to reports/visual/ on mismatch.

Baselines live under tests/visual/__snapshots__/<name>.png and are committed to git.
First run with VISUAL_UPDATE=1 (or missing baseline) writes the baseline.

Threshold: percentage of pixels allowed to differ (default 0.3%). Above that → fail.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).parent.parent
BASELINE_DIR = ROOT / "tests" / "visual" / "__snapshots__"
DIFF_DIR = ROOT / "reports" / "visual"
BASELINE_DIR.mkdir(parents=True, exist_ok=True)
DIFF_DIR.mkdir(parents=True, exist_ok=True)


def _diff_pct(img_a: Image.Image, img_b: Image.Image) -> tuple[float, Image.Image]:
    """Return (percent_changed, diff_image)."""
    if img_a.size != img_b.size:
        # Resize the second to match — surfaces as a near-100% diff
        img_b = img_b.resize(img_a.size)
    diff = ImageChops.difference(img_a.convert("RGB"), img_b.convert("RGB"))
    bbox = diff.getbbox()
    if bbox is None:
        return 0.0, diff
    # Count non-zero pixels
    hist = diff.histogram()
    # Each band (R,G,B) contributes 256 bins; pixel is "changed" if any band > 0
    # Approximate by total non-zero histogram weight / total pixels / 3 bands
    total_pixels = img_a.size[0] * img_a.size[1]
    nonzero_weight = sum(hist[i] for i in range(1, 256)) + sum(
        hist[256 + i] for i in range(1, 256)
    ) + sum(hist[512 + i] for i in range(1, 256))
    pct = (nonzero_weight / (total_pixels * 3)) * 100
    return pct, diff


def compare(name: str, current_path: Path, threshold_pct: float | None = None) -> dict:
    """
    Compare current screenshot to baseline. Returns a dict with keys:
        baseline_path, current_path, diff_path, pct_changed, passed, baseline_created

    If baseline does not exist (or VISUAL_UPDATE=1), copies current to baseline and passes.
    """
    threshold_pct = (
        threshold_pct
        if threshold_pct is not None
        else float(os.getenv("VISUAL_THRESHOLD_PCT", "0.3"))
    )
    baseline = BASELINE_DIR / f"{name}.png"
    update = os.getenv("VISUAL_UPDATE", "0") == "1"

    if not baseline.exists() or update:
        baseline.write_bytes(current_path.read_bytes())
        return {
            "baseline_path": str(baseline),
            "current_path": str(current_path),
            "diff_path": None,
            "pct_changed": 0.0,
            "passed": True,
            "baseline_created": True,
        }

    img_base = Image.open(baseline)
    img_curr = Image.open(current_path)
    pct, diff = _diff_pct(img_base, img_curr)

    diff_path = DIFF_DIR / f"{name}-diff.png"
    if pct > 0:
        # Save diff visualization
        diff.save(diff_path)
    passed = pct <= threshold_pct

    return {
        "baseline_path": str(baseline),
        "current_path": str(current_path),
        "diff_path": str(diff_path) if pct > 0 else None,
        "pct_changed": round(pct, 4),
        "threshold_pct": threshold_pct,
        "passed": passed,
        "baseline_created": False,
    }
