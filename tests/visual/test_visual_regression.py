"""
Visual regression suite. Pillow-based pixel diff.

Baselines: tests/visual/__snapshots__/<name>.png (committed to git).
First run (or VISUAL_UPDATE=1) writes baselines; subsequent runs diff against them.
Diff images on mismatch: reports/visual/<name>-diff.png (CI artifact).

Threshold via VISUAL_THRESHOLD_PCT (default 0.3% pixel change tolerated).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page

from flows.auth import goto_dashboard
from flows.visual import compare
from pages.login_page import LoginPage

SCREENSHOTS = Path(__file__).parent.parent.parent / "reports" / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)


def _capture(page: Page, name: str) -> Path:
    out = SCREENSHOTS / f"{name}.png"
    # animations=disabled + a small explicit wait reduces flakiness from
    # CSS transitions and font-loading layout shift
    page.evaluate("() => document.fonts && document.fonts.ready")
    page.screenshot(path=str(out), full_page=True, animations="disabled")
    return out


@pytest.mark.visual
def test_login_page_visual(anon_page: Page, record) -> None:
    LoginPage(anon_page).open()
    anon_page.wait_for_load_state("networkidle")
    current = _capture(anon_page, "login")
    result = compare("login", current)
    record["visual_diff"] = result
    record["screenshots"] = [result["current_path"]]
    if result["baseline_created"]:
        pytest.skip(f"Baseline created at {result['baseline_path']}")
    assert result["passed"], (
        f"Visual diff {result['pct_changed']}% exceeds threshold {result['threshold_pct']}%. "
        f"See {result['diff_path']}"
    )


@pytest.mark.visual
@pytest.mark.mocked
def test_dashboard_visual(mocked_page: Page, record) -> None:
    goto_dashboard(mocked_page)
    mocked_page.wait_for_load_state("networkidle")
    current = _capture(mocked_page, "dashboard")
    result = compare("dashboard", current)
    record["visual_diff"] = result
    record["screenshots"] = [result["current_path"]]
    if result["baseline_created"]:
        pytest.skip(f"Baseline created at {result['baseline_path']}")
    assert result["passed"], (
        f"Visual diff {result['pct_changed']}% exceeds threshold {result['threshold_pct']}%. "
        f"See {result['diff_path']}"
    )


@pytest.mark.visual
@pytest.mark.mocked
def test_devices_visual(mocked_page: Page, record) -> None:
    mocked_page.goto("/nova/resources/devices", wait_until="networkidle")
    current = _capture(mocked_page, "devices")
    result = compare("devices", current)
    record["visual_diff"] = result
    record["screenshots"] = [result["current_path"]]
    if result["baseline_created"]:
        pytest.skip(f"Baseline created at {result['baseline_path']}")
    assert result["passed"], (
        f"Visual diff {result['pct_changed']}% exceeds threshold {result['threshold_pct']}%. "
        f"See {result['diff_path']}"
    )
